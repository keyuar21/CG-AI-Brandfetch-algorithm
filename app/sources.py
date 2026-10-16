"""Discovery and enrichment sources. Each one is best-effort: a missing key or failure just skips it."""
import random
import re
import threading
import time
from urllib.parse import urljoin, urlparse

import httpx
from rapidfuzz import fuzz

from . import config, llm

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
_disabled: set[str] = set()  # sources that returned auth/plan errors this process

DISCOVER_SYSTEM = (
    "You are a business-development researcher for CouponzGuru, a coupon & deals site. "
    "You find real consumer brands (B2C, sell online, would run affiliate/coupon campaigns). "
    "Only return brands that really exist. Respond with JSON only."
)


def _category_rule(category: str) -> str:
    if category == "Any":
        return ("Cover a diverse mix of categories. For each brand set \"category\" to exactly one of: "
                + ", ".join(config.CATEGORIES) + ".")
    return f'All brands must be in the category "{category}"; set "category" to "{category}".'


def _brand_list_prompt(country: str, category: str, n: int, extra: str = "") -> str:
    return (
        f"Find {n} consumer brands operating in {country} that sell online to consumers. "
        f"{_category_rule(category)} Prefer D2C, emerging and mid-size brands over the most famous giants. "
        f"{extra}\nReturn a JSON array of objects: "
        '{"name": str, "category": str, "website": "https://...", "why": "one short line"}.'
    )


def _clean(items, category: str) -> list[dict]:
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not str(it.get("name", "")).strip():
            continue
        cat = it.get("category") or category
        if cat not in config.CATEGORIES:
            cat = max(config.CATEGORIES, key=lambda c: fuzz.partial_ratio(str(cat).lower(), c.lower()))
        out.append({"name": str(it["name"]).strip(), "category": cat, "website": str(it.get("website") or "").strip()})
    return out


# ---------------------------------------------------------------- discovery

def discover_llm(country: str, category: str, n: int) -> tuple[list[dict], str]:
    text, label = llm.ask(DISCOVER_SYSTEM, _brand_list_prompt(country, category, n))
    return _clean(llm.parse_json(text), category), label


def discover_groq_web(country: str, category: str, n: int) -> tuple[list[dict], str]:
    """Groq gpt-oss with its built-in browser_search tool (live web)."""
    if not (config.GROQ_API_KEY and config.GROQ_WEB_SEARCH) or "groq_web" in _disabled:
        return [], ""
    body = {
        "model": config.GROQ_MODEL,
        "tools": [{"type": "browser_search"}],
        "messages": [{"role": "system", "content": DISCOVER_SYSTEM},
                     {"role": "user", "content": _brand_list_prompt(country, category, n,
                                                                    "Search the web for recently launched or trending brands.")}],
    }
    try:
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", json=body, timeout=75,
                       headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
        if r.status_code in (400, 401, 403, 404):
            _disabled.add("groq_web")
            return [], ""
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
        return _clean(llm.parse_json(text), category), "groq_web_search"
    except Exception:
        return [], ""


def _serp(q: str, country: str = "", num: int = 10) -> list[dict]:
    if not config.SERPAPI_API_KEY or "serpapi" in _disabled:
        return []
    try:
        r = httpx.get("https://serpapi.com/search", timeout=30, params={
            "engine": "google", "q": q, "gl": config.COUNTRY_GL.get(country, "us"), "num": num,
            "api_key": config.SERPAPI_API_KEY})
        if r.status_code in (401, 403):
            _disabled.add("serpapi")
            return []
        return r.json().get("organic_results", [])
    except Exception:
        return []


def discover_serpapi(country: str, category: str, n: int) -> tuple[list[dict], str]:
    """Google results via SerpAPI, then the LLM extracts brand names from the result snippets."""
    cat = "" if category == "Any" else category
    results = _serp(f"best {cat} brands in {country} shop online", country, 20)
    results += _serp(f"top D2C {cat} startups {country} list", country, 20)
    snippets = "\n".join(f"- {x.get('title')}: {x.get('snippet')}" for x in results)
    if not snippets:
        return [], ""
    text, label = llm.ask(DISCOVER_SYSTEM, _brand_list_prompt(
        country, category, n, "Use the brands mentioned in these Google results; you may add closely related "
        f"real brands from the same lists:\n{snippets}"))
    return _clean(llm.parse_json(text), category), f"serpapi+{label}"


def serp_contacts(name: str, country: str, website: str = "") -> dict:
    """Google (SerpAPI) lookup for the LinkedIn company page and a contact email on the brand's domain."""
    if not config.SERPAPI_API_KEY:
        return {}
    out = {}
    for r in _serp(f"{name} {country} site:linkedin.com/company", country, 5):
        if m := LINKEDIN_RE.search(r.get("link", "")):
            out["linkedin"] = m.group(0)
            break
    dom = _domain(website)
    if dom:
        text = " ".join(r.get("snippet", "") for r in _serp(f'"@{dom}" email contact OR partnerships', country, 10))
        emails = [e.lower() for e in EMAIL_RE.findall(text)
                  if e.lower().endswith(dom) and not any(b in e.lower() for b in BAD_EMAIL)]
        emails.sort(key=lambda e: next((i for i, p in enumerate(PREFERRED) if p in e), 99))
        if emails:
            out["email"] = emails[0]
    return out


# ---------------------------------------------------------------- enrichment

def brandfetch(name: str, website: str = "") -> dict:
    if not config.BRANDFETCH_CLIENT_ID:
        return {}
    try:
        r = httpx.get(f"https://api.brandfetch.io/v2/search/{name}", params={"c": config.BRANDFETCH_CLIENT_ID}, timeout=15)
        hits = r.json() if r.status_code == 200 else []
    except Exception:
        return {}
    want = _domain(website)
    best = None
    for h in hits[:5]:
        if want and h.get("domain") == want:
            best = h
            break
        if best is None and not want and fuzz.ratio(name.lower(), str(h.get("name", "")).lower()) >= 85:
            best = h
    if not best:
        return {}
    return {"website": f"https://{best['domain']}", "logo": best.get("icon", "")}


def apollo(domain: str) -> dict:
    if not (config.APOLLO_API_KEY and domain) or "apollo" in _disabled:
        return {}
    try:
        r = httpx.post("https://api.apollo.io/api/v1/organizations/enrich", params={"domain": domain}, timeout=20,
                       headers={"X-Api-Key": config.APOLLO_API_KEY, "Content-Type": "application/json"})
        if r.status_code in (401, 403, 422):
            _disabled.add("apollo")  # free plan doesn't include this endpoint
            return {}
        org = r.json().get("organization") or {}
    except Exception:
        return {}
    return {k: v for k, v in {"linkedin": org.get("linkedin_url"),
                              "phone": (org.get("primary_phone") or {}).get("number") or org.get("phone")}.items() if v}


def builtwith(domain: str) -> dict:
    if not (config.BUILTWITH_API_KEY and domain) or "builtwith" in _disabled:
        return {}
    try:
        r = httpx.get("https://api.builtwith.com/free1/api.json",
                      params={"KEY": config.BUILTWITH_API_KEY, "LOOKUP": domain}, timeout=20)
        if r.status_code in (401, 403):
            _disabled.add("builtwith")
            return {}
        groups = [g.get("name") for g in r.json().get("groups", [])]
    except Exception:
        return {}
    return {"tech": ", ".join(g for g in groups if g)[:200]} if groups else {}


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,10}\b")
LINKEDIN_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/[A-Za-z0-9_\-%.]+", re.I)
TEL_RE = re.compile(r'href=["\']tel:([+\d][\d\s\-().]{6,})["\']', re.I)
BAD_EMAIL = ("example.", "address.com", "doe@", ".js", ".css", "@sentry", "noreply", "no-reply", "jsmith", "john", "jane", "yourname", "firstname", "name@", "user@", "test@", "sentry", "wixpress", ".png", ".jpg", ".webp", ".svg", ".gif", "domain.com", "@2x", "email.com")
PREFERRED = ("partner", "affiliate", "marketing", "collab", "business", "bd@", "brand", "pr@", "hello", "contact", "info", "support", "care")


def _extract(html: str, found: dict):
    found["emails"] |= {e.lower().rstrip(".") for e in EMAIL_RE.findall(html) if not any(b in e.lower() for b in BAD_EMAIL)}
    found["phones"] += [p.strip() for p in TEL_RE.findall(html) if _real_phone(p)]
    if not found["linkedin"] and (m := LINKEDIN_RE.search(html)):
        found["linkedin"] = m.group(0)


def _result(found: dict) -> dict:
    email = sorted(found["emails"], key=lambda e: next((i for i, p in enumerate(PREFERRED) if p in e), 99))
    return {k: v for k, v in {"email": email[0] if email else "", "phone": found["phones"][0] if found["phones"] else "",
                              "linkedin": found["linkedin"]}.items() if v}


CONTACT_PATHS = ("", "/contact", "/contact-us", "/pages/contact", "/pages/contact-us")


def website_scrape(website: str) -> dict:
    """Tier 1: plain HTTP fetch of homepage + contact pages for email / phone / LinkedIn."""
    if not website:
        return {}
    base = website if website.startswith("http") else f"https://{website}"
    found = {"emails": set(), "phones": [], "linkedin": ""}
    with httpx.Client(headers=UA, timeout=10, follow_redirects=True) as c:
        for path in CONTACT_PATHS:
            try:
                r = c.get(urljoin(base + "/", path.lstrip("/")))
            except Exception:
                continue
            if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                _extract(r.text[:600_000], found)
            if found["emails"] and found["phones"] and found["linkedin"]:
                break
    return _result(found)


def browser_scrape(website: str) -> dict:
    """Tier 2: render JS-heavy sites in the shared headless browser (only when tier 1 found little)."""
    if not website:
        return {}
    from . import browser
    base = website if website.startswith("http") else f"https://{website}"
    found = {"emails": set(), "phones": [], "linkedin": ""}
    for html in browser.render([base, urljoin(base + "/", "contact-us"), urljoin(base + "/", "pages/contact")]):
        _extract(html[:5_000_000], found)
    return _result(found)


# ---------------------------------------------------------------- DuckDuckGo (free, no key)

_ddg_lock = threading.Semaphore(2)  # DDG rate-limits bursts


def _ddg(query: str, n: int = 8, region: str = "wt-wt") -> list[dict]:
    if "ddg" in _disabled:
        return []
    from ddgs import DDGS
    with _ddg_lock:
        time.sleep(random.uniform(0.4, 1.2))
        try:
            return list(DDGS().text(query, region=region, max_results=n)) or []
        except Exception as e:
            if "ratelimit" in str(e).lower():
                time.sleep(3)
            return []


DDG_REGION = {"India": "in-en", "USA": "us-en", "UAE": "xa-en", "Singapore": "sg-en", "Malaysia": "my-en", "Philippines": "ph-en"}


def discover_ddg(country: str, category: str, n: int) -> tuple[list[dict], str]:
    """Free web search → LLM extracts brand names from the snippets."""
    cat = "" if category == "Any" else category
    results = _ddg(f"best {cat} D2C brands in {country} shop online", 10, DDG_REGION.get(country, "wt-wt"))
    results += _ddg(f"new emerging {cat} brands {country} 2026", 8, DDG_REGION.get(country, "wt-wt"))
    snippets = "\n".join(f"- {r.get('title')}: {r.get('body')}" for r in results)
    if not snippets:
        return [], ""
    text, label = llm.ask(DISCOVER_SYSTEM, _brand_list_prompt(
        country, category, n, f"Extract brands mentioned in these web search results:\n{snippets}"))
    return _clean(llm.parse_json(text), category), f"duckduckgo+{label}"


def ddg_contacts(name: str, country: str) -> dict:
    """Look up a LinkedIn company page and a public contact email via search snippets."""
    out = {}
    for r in _ddg(f"{name} {country} site:linkedin.com/company", 3):
        if m := LINKEDIN_RE.search(r.get("href", "")):
            out["linkedin"] = m.group(0)
            break
    text = " ".join(f"{r.get('body', '')}" for r in _ddg(f'"{name}" contact email', 5))
    emails = [e.lower().rstrip(".") for e in EMAIL_RE.findall(text) if not any(b in e.lower() for b in BAD_EMAIL)]
    key = re.sub(r"[^a-z0-9]", "", name.lower())[:5]
    emails = [e for e in emails if key and key in e.split("@")[1].replace("-", "")]  # must be the brand's own domain
    if emails:
        out["email"] = emails[0]
    return out


def _real_phone(p: str) -> bool:
    digits = re.sub(r"\D", "", p)
    return len(digits) >= 7 and "123456" not in digits and len(set(digits)) > 2


def _domain(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host[4:] if host.startswith("www.") else host
