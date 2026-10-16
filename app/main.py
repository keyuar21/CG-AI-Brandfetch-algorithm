import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, outreach, sheets
from .graph import GRAPH

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="CouponzGuru Brand Finder")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

JOBS: dict[str, dict] = {}
_job_lock = threading.Lock()
_cache: dict = {"at": 0, "rows": []}


def brands(fresh: bool = False) -> list[dict]:
    if fresh or time.time() - _cache["at"] > 30:
        _cache.update(at=time.time(), rows=sheets.read_results())
    return _cache["rows"]


def find(name: str) -> dict:
    key = sheets.compact(name)
    for b in brands():
        if sheets.compact(b["brand_name"]) == key:
            return b
    raise HTTPException(404, "Brand not found")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    return {"countries": config.COUNTRIES, "categories": config.CATEGORIES,
            "smtp": bool(config.SMTP_USER and config.SMTP_PASSWORD),
            "sources": {"groq": bool(config.GROQ_API_KEY), "openai": bool(config.OPENAI_API_KEY),
                        "gemini": bool(config.GOOGLE_API_KEY), "brandfetch": bool(config.BRANDFETCH_CLIENT_ID),
                        "apollo": bool(config.APOLLO_API_KEY), "serpapi": bool(config.SERPAPI_API_KEY),
                        "builtwith": bool(config.BUILTWITH_API_KEY), "website_scrape": True}}


@app.get("/api/brands")
def list_brands(fresh: bool = False):
    return [{k: v for k, v in b.items() if k != "_row"} for b in brands(fresh)]


class SearchReq(BaseModel):
    countries: list[str]
    categories: list[str]
    per_query: int = 10
    max_new: int = 60


@app.post("/api/search")
def start_search(req: SearchReq):
    with _job_lock:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise HTTPException(409, "A search is already running")
        job_id = uuid.uuid4().hex[:8]
        job = JOBS[job_id] = {"status": "running", "logs": [], "saved": 0, "pruned": []}

    def log(msg):
        job["logs"].append(msg)

    def run():
        try:
            out = GRAPH.invoke(
                {"countries": req.countries or config.COUNTRIES, "categories": req.categories or ["Any"],
                 "per_query": max(3, min(req.per_query, 25)), "max_new": max(1, min(req.max_new, 200))},
                config={"configurable": {"log": log}, "max_concurrency": 6},
            )
            job.update(status="done", saved=out.get("saved", 0), pruned=out.get("pruned", []))
        except Exception as e:
            log(f"Error: {e}")
            job["status"] = "error"
        brands(fresh=True)

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/search/{job_id}")
def search_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404)
    return JOBS[job_id]


@app.get("/api/brands/{name}/summary")
def brand_summary(name: str):
    b = find(name)
    if not b.get("summary"):
        b["summary"] = outreach.summary(b)
    return {"summary": b["summary"]}


class DraftReq(BaseModel):
    brand: str
    kind: str  # "email" | "linkedin"


@app.post("/api/draft")
def make_draft(req: DraftReq):
    return outreach.draft(find(req.brand), req.kind)


class SendReq(BaseModel):
    to: str
    subject: str
    body: str


@app.post("/api/send-email")
def send(req: SendReq):
    try:
        outreach.send_email(req.to, req.subject, req.body)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
