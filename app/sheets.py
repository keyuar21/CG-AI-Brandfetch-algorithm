"""Google Sheet access: the 6 country tabs are the onboarded list, `tool_search` is our results store."""
import re
import threading
from datetime import datetime, timezone

import gspread
from rapidfuzz import fuzz

from . import config

# First four headers already exist in the sheet (spelling kept as-is); the rest we add.
HEADERS = ["brand_name", "catagory", "county", "data_source",
           "website", "linkedin", "email", "phone", "logo", "summary", "added_at"]

_STOP = {"the", "inc", "ltd", "limited", "pvt", "private", "llc", "co", "company", "corp", "corporation",
         "group", "official", "store", "stores", "online", "shop", "com", "in", "brand", "brands",
         "international", "global", "hotels", "hotel", "resorts", "and", "retail"} | {c.lower() for c in config.COUNTRIES}

_lock = threading.Lock()
_client = None


def norm(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"\.(com|in|co|ae|sg|my|ph)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if t not in _STOP]
    return " ".join(toks) or s.strip()


def compact(name: str) -> str:
    return norm(name).replace(" ", "")


class Onboarded:
    """Fast membership check against every brand in the 6 country tabs."""

    def __init__(self, names: list[str]):
        self.raw = names
        self.compacts = {compact(n) for n in names if n.strip()}
        self.by_first = {}
        for c in self.compacts:
            self.by_first.setdefault(c[:2], []).append(c)

    def contains(self, name: str) -> bool:
        c = compact(name)
        if not c:
            return True
        if c in self.compacts:
            return True
        return any(fuzz.ratio(c, o) >= 92 for o in self.by_first.get(c[:2], []))

    def __len__(self):
        return len(self.compacts)


def _sheet():
    global _client
    if _client is None:
        _client = gspread.service_account(filename=config.SERVICE_ACCOUNT_JSON)
    return _client.open_by_url(config.SPREADSHEET_URL)


def _results_ws(sh):
    try:
        ws = sh.worksheet(config.RESULTS_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(config.RESULTS_TAB, rows=1000, cols=len(HEADERS))
    header = ws.row_values(1)
    if header[:len(HEADERS)] != HEADERS:
        if ws.col_count < len(HEADERS):
            ws.add_cols(len(HEADERS) - ws.col_count)
        ws.update(range_name="A1", values=[HEADERS])
    return ws


def load_onboarded() -> tuple[Onboarded, dict[str, int]]:
    sh = _sheet()
    names, counts = [], {}
    for ws in sh.worksheets():
        if ws.title not in config.COUNTRIES:
            continue
        col = ws.col_values(1)[1:]
        col = [c for c in col if c.strip()]
        counts[ws.title] = len(col)
        names.extend(col)
    return Onboarded(names), counts


def read_results() -> list[dict]:
    with _lock:
        ws = _results_ws(_sheet())
        rows = ws.get_all_values()[1:]
    out = []
    for i, r in enumerate(rows, start=2):
        r = r + [""] * (len(HEADERS) - len(r))
        if not r[0].strip():
            continue
        d = dict(zip(HEADERS, r))
        d["_row"] = i
        out.append(d)
    return out


def prune_onboarded(onboarded: Onboarded) -> list[str]:
    """Delete result rows whose brand is now in one of the 6 onboarded tabs."""
    with _lock:
        ws = _results_ws(_sheet())
        rows = ws.get_all_values()[1:]
        doomed = [(i, r[0]) for i, r in enumerate(rows, start=2) if r and r[0].strip() and onboarded.contains(r[0])]
        for i, _ in sorted(doomed, reverse=True):  # bottom-up so row numbers stay valid
            ws.delete_rows(i)
    return [n for _, n in doomed]


def append_results(items: list[dict]) -> int:
    if not items:
        return 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    rows = [[str(it.get(h, "") or "") if h != "added_at" else now for h in HEADERS] for it in items]
    with _lock:
        ws = _results_ws(_sheet())
        ws.append_rows(rows, value_input_option="RAW")
    return len(rows)


def update_cell(brand: str, column: str, value: str):
    with _lock:
        ws = _results_ws(_sheet())
        names = ws.col_values(1)
        target = compact(brand)
        for i, n in enumerate(names[1:], start=2):
            if compact(n) == target:
                ws.update_cell(i, HEADERS.index(column) + 1, value)
                return
