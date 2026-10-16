"""LangGraph pipeline: load sheet -> prune onboarded -> fan-out discovery -> filter -> enrich -> save."""
import operator
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import sheets, sources

_llm_slots = threading.BoundedSemaphore(3)  # keep under Groq free-tier rate limits


class State(TypedDict, total=False):
    countries: list[str]
    categories: list[str]
    per_query: int
    max_new: int
    onboarded: sheets.Onboarded
    existing: set[str]
    pruned: list[str]
    candidates: Annotated[list[dict], operator.add]
    new_brands: list[dict]
    saved: int


class Task(TypedDict):
    country: str
    category: str
    per_query: int
    source: str


def _log(cfg: RunnableConfig, msg: str):
    if cb := cfg.get("configurable", {}).get("log"):
        cb(msg)


def load_sheet(state: State, config: RunnableConfig) -> State:
    onboarded, counts = sheets.load_onboarded()
    _log(config, f"Loaded {len(onboarded)} onboarded brands from tabs: "
         + ", ".join(f"{k} ({v})" for k, v in counts.items()))
    return {"onboarded": onboarded}


def prune(state: State, config: RunnableConfig) -> State:
    removed = sheets.prune_onboarded(state["onboarded"])
    if removed:
        _log(config, f"Removed {len(removed)} now-onboarded brands from tool_search: {', '.join(removed[:15])}")
    existing = {sheets.compact(r["brand_name"]) for r in sheets.read_results()}
    _log(config, f"{len(existing)} brands already in tool_search")
    return {"pruned": removed, "existing": existing}


def fan_out(state: State):
    srcs = ["llm", "duckduckgo", "groq_web", "serpapi"]
    return [Send("discover", Task(country=c, category=cat, per_query=state["per_query"], source=s))
            for c in state["countries"] for cat in state["categories"] for s in srcs]


def discover(task: Task, config: RunnableConfig) -> State:
    fn = {"llm": sources.discover_llm, "groq_web": sources.discover_groq_web, "duckduckgo": sources.discover_ddg, "serpapi": sources.discover_serpapi}[task["source"]]
    with _llm_slots:
        try:
            found, label = fn(task["country"], task["category"], task["per_query"])
        except Exception as e:
            _log(config, f"  ✗ {task['source']} {task['country']}/{task['category']}: {str(e)[:120]}")
            return {"candidates": []}
    if found:
        _log(config, f"  ✓ {label}: {len(found)} brands for {task['country']} / {task['category']}")
    for f in found:
        f.update(country=task["country"], sources=[label])
    return {"candidates": found}


def filter_new(state: State, config: RunnableConfig) -> State:
    onboarded, existing = state["onboarded"], state["existing"]
    merged: dict[str, dict] = {}
    skipped = 0
    for c in state.get("candidates", []):
        key = sheets.compact(c["name"])
        if not key or key in existing or onboarded.contains(c["name"]):
            skipped += 1
            continue
        if key in merged:
            m = merged[key]
            m["sources"] = sorted(set(m["sources"]) | set(c["sources"]))
            m["website"] = m["website"] or c["website"]
        else:
            merged[key] = dict(c)
    new = list(merged.values())[: state["max_new"]]
    _log(config, f"{len(state.get('candidates', []))} candidates → skipped {skipped} onboarded/duplicates → {len(new)} new")
    return {"new_brands": new}


def enrich(state: State, config: RunnableConfig) -> State:
    def one(b: dict) -> dict:
        srcs = list(b["sources"])
        if bf := sources.brandfetch(b["name"], b["website"]):
            b.update({k: v for k, v in bf.items() if v})
            srcs.append("brandfetch")
        domain = sources._domain(b["website"])
        # Cheap sources first; the browser and search only run when contacts are still missing.
        steps = (("website_scrape", lambda: sources.website_scrape(b["website"])),
                 ("browser_scrape", lambda: sources.browser_scrape(b["website"])),
                 ("serpapi", lambda: sources.serp_contacts(b["name"], b["country"], b["website"])),
                 ("duckduckgo", lambda: sources.ddg_contacts(b["name"], b["country"])),
                 ("apollo", lambda: sources.apollo(domain)),
                 ("builtwith", lambda: sources.builtwith(domain)))
        for name, fn in steps:
            if name in ("browser_scrape", "serpapi", "duckduckgo") and b.get("email") and b.get("linkedin"):
                continue
            data = fn()
            new = {k: v for k, v in data.items() if v and not b.get(k)}
            if new:
                b.update(new)
                srcs.append(name)
        b["data_source"] = " | ".join(dict.fromkeys(srcs))
        return b

    brands = state.get("new_brands", [])
    if brands:
        _log(config, f"Enriching {len(brands)} brands (Brandfetch, HTTP → headless browser scrape, DuckDuckGo, Apollo, BuiltWith)…")
    with ThreadPoolExecutor(8) as ex:
        enriched = list(ex.map(one, brands))
    return {"new_brands": enriched}


def save(state: State, config: RunnableConfig) -> State:
    rows = [{"brand_name": b["name"], "catagory": b["category"], "county": b["country"],
             "data_source": b["data_source"], "website": b.get("website", ""), "linkedin": b.get("linkedin", ""),
             "email": b.get("email", ""), "phone": b.get("phone", ""), "logo": b.get("logo", "")}
            for b in state.get("new_brands", [])]
    n = sheets.append_results(rows)
    _log(config, f"Saved {n} new brands to the tool_search tab ✔")
    return {"saved": n}


def build():
    g = StateGraph(State)
    for name, fn in (("load_sheet", load_sheet), ("prune", prune), ("discover", discover),
                     ("filter_new", filter_new), ("enrich", enrich), ("save", save)):
        g.add_node(name, fn)
    g.add_edge(START, "load_sheet")
    g.add_edge("load_sheet", "prune")
    g.add_conditional_edges("prune", fan_out, ["discover"])
    g.add_edge("discover", "filter_new")
    g.add_edge("filter_new", "enrich")
    g.add_edge("enrich", "save")
    g.add_edge("save", END)
    return g.compile()


GRAPH = build()
