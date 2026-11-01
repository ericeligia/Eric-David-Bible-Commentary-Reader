"""Bible Commentary side-by-side panel backend.

Proxies the public-domain GetBible Commentaries API
(https://commentaries.getbible.net/v1/) and returns one panel per
commentator for a single verse. No scraping, no LLM calls.

Run from this directory:
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

API_BASE = "https://commentaries.getbible.net/v1"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 BibleCommentaryApp/0.1"
)
TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Fixed display order. id=None means the work is not in the GetBible API.
# `fallback` ids are tried in order if the primary returns no entry for the verse.
COMMENTATORS: list[dict[str, Any]] = [
    {"key": "matthew-henry", "name": "Matthew Henry", "id": "mhc", "fallback": ["mhcc"],
     "note": "Complete commentary (mhc); concise (mhcc) used as fallback"},
    {"key": "john-gill", "name": "John Gill", "id": None, "fallback": [],
     "note": "Not in GetBible Commentaries API"},
    {"key": "adam-clarke", "name": "Adam Clarke", "id": "clarke", "fallback": [], "note": None},
    {"key": "spurgeon", "name": "Spurgeon", "id": "tdavid", "fallback": [],
     "note": "Treasury of David — Psalms only"},
    {"key": "jfb", "name": "JFB", "id": "jfb", "fallback": [],
     "note": "Jamieson, Fausset & Brown"},
    {"key": "barnes", "name": "Barnes", "id": "barnes", "fallback": [], "note": None},
    {"key": "wesley", "name": "Wesley", "id": "wesley", "fallback": [], "note": None},
    {"key": "geneva", "name": "Geneva", "id": None, "fallback": [],
     "note": "Not in GetBible Commentaries API"},
    {"key": "keil-delitzsch", "name": "Keil-Delitzsch", "id": "kd", "fallback": [],
     "note": "Old Testament only"},
    {"key": "vincent", "name": "Vincent", "id": None, "fallback": [],
     "note": "Not in GetBible Commentaries API"},
]

# --- Theological tradition tagging -------------------------------------------
# camp is used for UI colouring and tension detection.
TRADITIONS: dict[str, dict[str, str]] = {
    "matthew-henry": {"tradition": "Covenant / moderate Calvinist", "camp": "moderate-calvinist",
        "election_stance": "Holds covenant theology with a moderate Calvinist view of election that still urges gospel invitation."},
    "john-gill": {"tradition": "Hyper-Calvinist", "camp": "high-calvinist",
        "election_stance": "Affirms particular redemption and a high Calvinism that often soft-pedals free offers of grace."},
    "adam-clarke": {"tradition": "Arminian-leaning Methodist", "camp": "arminian",
        "election_stance": "Resists unconditional election and emphasizes conditional grace and human response."},
    "spurgeon": {"tradition": "Calvinist (evangelistic)", "camp": "calvinist",
        "election_stance": "Calvinist on election yet insistently preached Christ to all hearers."},
    "jfb": {"tradition": "Moderate Calvinist (Scottish)", "camp": "moderate-calvinist",
        "election_stance": "Reflects a moderate Scottish Calvinism on divine sovereignty and election."},
    "barnes": {"tradition": "Moderate Calvinist (New School)", "camp": "moderate-calvinist",
        "election_stance": "New School Presbyterian moderate Calvinism, affirming election without harsh particularism."},
    "wesley": {"tradition": "Arminian", "camp": "arminian",
        "election_stance": "Classic Arminian rejection of unconditional election in favor of prevenient grace and free response."},
    "geneva": {"tradition": "Reformed / strongly Calvinist", "camp": "high-calvinist",
        "election_stance": "Strongly Reformed Calvinist notes stressing God\u2019s sovereign election."},
    "keil-delitzsch": {"tradition": "Conservative Lutheran", "camp": "lutheran",
        "election_stance": "Conservative Lutheran; not Calvinist on election, reading Scripture through Lutheran categories."},
    "vincent": {"tradition": "Moderate / non-dogmatic", "camp": "moderate",
        "election_stance": "Moderate and largely non-dogmatic on election, prioritizing lexical and historical notes over system."},
}
for _c in COMMENTATORS:
    _c.update(TRADITIONS[_c["key"]])

# Opposing camps on election. One callout per rule: the first present name from
# each side (priority order) is the representative; others go in also_a/also_b.
# `skip_if` suppresses a softer rule when a stronger one already fired.
TENSION_RULES: list[dict[str, Any]] = [
    {"id": "high-calvinist-vs-arminian", "severity": "strong",
     "side_a": ["john-gill", "geneva"], "side_b": ["wesley", "adam-clarke"],
     "sentences": {
        ("john-gill", "wesley"): "Gill\u2019s hyper-Calvinist particular redemption clashes with Wesley\u2019s Arminian free response on election.",
        ("john-gill", "adam-clarke"): "Gill\u2019s unconditional, particular election clashes with Clarke\u2019s conditional grace and human response.",
        ("geneva", "wesley"): "The Geneva notes\u2019 sovereign unconditional election clashes with Wesley\u2019s prevenient grace and free response.",
        ("geneva", "adam-clarke"): "The Geneva notes\u2019 sovereign unconditional election clashes with Clarke\u2019s conditional, response-based grace.",
     }},
    {"id": "high-calvinist-vs-lutheran", "severity": "moderate",
     "side_a": ["geneva", "john-gill"], "side_b": ["keil-delitzsch"],
     "sentences": {
        ("geneva", "keil-delitzsch"): "Geneva\u2019s Reformed double predestination contrasts with Keil-Delitzsch\u2019s Lutheran refusal to read election in Calvinist terms.",
        ("john-gill", "keil-delitzsch"): "Gill\u2019s particular redemption differs from Keil-Delitzsch\u2019s Lutheran, non-Calvinist reading of election.",
     }},
    {"id": "evangelistic-calvinist-vs-arminian", "severity": "soft",
     "skip_if": ["high-calvinist-vs-arminian"],
     "side_a": ["spurgeon"], "side_b": ["wesley", "adam-clarke"],
     "sentences": {
        ("spurgeon", "wesley"): "Spurgeon shares Wesley\u2019s urgent appeal to all but grounds salvation in unconditional election rather than free response.",
        ("spurgeon", "adam-clarke"): "Spurgeon preaches to all like Clarke but grounds salvation in unconditional election rather than conditional grace.",
     }},
]

NAME_BY_KEY = {c["key"]: c["name"] for c in COMMENTATORS}


def detect_tensions(keys: list[str]) -> list[dict]:
    present = set(keys)
    fired: set[str] = set()
    out = []
    for rule in TENSION_RULES:
        if fired & set(rule.get("skip_if", [])):
            continue
        a_side = [k for k in rule["side_a"] if k in present]
        b_side = [k for k in rule["side_b"] if k in present]
        if not a_side or not b_side:
            continue
        a, b = a_side[0], b_side[0]
        fired.add(rule["id"])
        out.append({
            "id": rule["id"], "severity": rule["severity"],
            "a": NAME_BY_KEY[a], "b": NAME_BY_KEY[b], "a_key": a, "b_key": b,
            "also_a": [NAME_BY_KEY[k] for k in a_side[1:]],
            "also_b": [NAME_BY_KEY[k] for k in b_side[1:]],
            "sentence": rule["sentences"][(a, b)],
        })
    return out


# Canonical English names -> GetBible book numbers (Genesis=1 ... Revelation=66).
BOOKS = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges",
    "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles",
    "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
    "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah", "Lamentations",
    "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah",
    "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    "Matthew", "Mark", "Luke", "John", "Acts", "Romans", "1 Corinthians",
    "2 Corinthians", "Galatians", "Ephesians", "Philippians", "Colossians",
    "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus",
    "Philemon", "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John",
    "3 John", "Jude", "Revelation",
]
OT_LAST = 39

ALIASES = {
    "gen": 1, "ge": 1, "exod": 2, "ex": 2, "lev": 3, "num": 4, "deut": 5, "dt": 5,
    "josh": 6, "judg": 7, "1sam": 9, "2sam": 10, "1kgs": 11, "1ki": 11, "2kgs": 12,
    "2ki": 12, "1chr": 13, "2chr": 14, "neh": 16, "esth": 17, "ps": 19, "psa": 19,
    "psalm": 19, "pss": 19, "prov": 20, "pr": 20, "eccl": 21, "ecc": 21, "qoh": 21,
    "song": 22, "songofsongs": 22, "sos": 22, "canticles": 22, "isa": 23, "jer": 24,
    "lam": 25, "ezek": 26, "eze": 26, "dan": 27, "hos": 28, "obad": 31, "jon": 32,
    "mic": 33, "nah": 34, "hab": 35, "zeph": 36, "hag": 37, "zech": 38, "mal": 39,
    "matt": 40, "mt": 40, "mk": 41, "mrk": 41, "lk": 42, "luk": 42, "jn": 43, "jhn": 43,
    "rom": 45, "ro": 45, "1cor": 46, "2cor": 47, "gal": 48, "eph": 49, "phil": 50,
    "php": 50, "col": 51, "1thess": 52, "1th": 52, "2thess": 53, "2th": 53,
    "1tim": 54, "2tim": 55, "tit": 56, "phlm": 57, "philem": 57, "heb": 58, "jas": 59,
    "1pet": 60, "1pt": 60, "2pet": 61, "2pt": 61, "1jn": 62, "2jn": 63, "3jn": 64,
    "rev": 66, "revelations": 66, "apocalypse": 66,
}


def _norm(s: str) -> str:
    s = s.lower().strip().rstrip(".")
    s = re.sub(r"^(first|i)\s+", "1", s)
    s = re.sub(r"^(second|ii)\s+", "2", s)
    s = re.sub(r"^(third|iii)\s+", "3", s)
    return re.sub(r"[\s.]+", "", s)


BOOK_NAMES: dict[str, int] = {_norm(n): i for i, n in enumerate(BOOKS, start=1)}
BOOK_NAMES.update(ALIASES)


def book_number(name: str) -> int:
    key = _norm(name)
    if key.isdigit() and 1 <= int(key) <= 66:
        return int(key)
    if key in BOOK_NAMES:
        return BOOK_NAMES[key]
    # Unique-prefix match (e.g. "Phile" -> Philemon, "Rev" handled above)
    hits = {n for k, n in BOOK_NAMES.items() if k.startswith(key)} if len(key) >= 3 else set()
    if len(hits) == 1:
        return hits.pop()
    raise ValueError(f"Unknown book: {name!r}")


REF_RE = re.compile(r"^\s*(.+?)\s+(\d+)\s*[:.]\s*(\d+)\s*$")


def parse_ref(ref: str) -> tuple[int, int, int]:
    m = REF_RE.match(ref.replace("+", " "))
    if not m:
        raise ValueError(f"Could not parse reference {ref!r}; expected e.g. 'John 3:16'")
    return book_number(m.group(1)), int(m.group(2)), int(m.group(3))


def find_entry(chapter_json: dict, verse: int) -> Optional[dict]:
    for e in chapter_json.get("entries") or []:
        verses = e.get("verses") or [e.get("verse")]
        if verse in verses:
            return e
    return None


# Small in-process cache of upstream chapter responses (200s and 404s only).
_CHAPTER_CACHE: dict[str, tuple[int, Any]] = {}
_CACHE_MAX = 500


async def fetch_chapter(client: httpx.AsyncClient, cid: str, book: int, chapter: int):
    """Return (status_code, json_or_None, url)."""
    url = f"{API_BASE}/{cid}/{book}/{chapter}.json"
    if url in _CHAPTER_CACHE:
        code, data = _CHAPTER_CACHE[url]
        return code, data, url
    code, data = await _fetch_uncached(client, url)
    if code in (200, 404):
        if len(_CHAPTER_CACHE) >= _CACHE_MAX:
            _CHAPTER_CACHE.pop(next(iter(_CHAPTER_CACHE)))
        _CHAPTER_CACHE[url] = (code, data)
    return code, data, url


async def _fetch_uncached(client: httpx.AsyncClient, url: str):
    try:
        r = await client.get(url)
    except httpx.HTTPError as exc:
        return None, str(exc)
    if r.status_code != 200:
        return r.status_code, None
    try:
        return 200, r.json()
    except ValueError:
        return 200, None


async def build_panel(client, c: dict, book: int, chapter: int, verse: int) -> dict:
    base = {"key": c["key"], "id": c["id"], "name": c["name"], "note": c["note"],
            "tradition": c["tradition"], "election_stance": c["election_stance"], "camp": c["camp"]}
    if not c["id"]:
        return {**base, "status": "unavailable", "text": None,
                "reason": "Not in GetBible Commentaries API"}
    if c["id"] == "kd" and book > OT_LAST:
        return {**base, "status": "not_covered", "text": None,
                "reason": "Keil-Delitzsch covers the Old Testament only"}
    if c["id"] == "tdavid" and book != 19:
        return {**base, "status": "not_covered", "text": None,
                "reason": "Spurgeon's Treasury of David covers Psalms only"}

    last_reason = None
    for cid in [c["id"], *c["fallback"]]:
        code, data, url = await fetch_chapter(client, cid, book, chapter)
        if code is None:
            last_reason = f"Network error: {data}"
            continue
        if code == 404:
            last_reason = "No commentary for this chapter"
            continue
        if code != 200 or not isinstance(data, dict):
            last_reason = f"Upstream HTTP {code}"
            continue
        entry = find_entry(data, verse)
        if entry and (entry.get("text") or "").strip():
            text = re.sub(r"</?hi\b[^>]*>", "", entry["text"])  # strip upstream inline markup
            return {**base, "id": cid, "status": "ok", "text": text,
                    "verses": entry.get("verses") or [entry.get("verse")],
                    "osis": entry.get("osis"), "references": entry.get("references"),
                    "source_url": url}
        last_reason = "No entry for this verse"
    status = "error" if last_reason and last_reason.startswith(("Network", "Upstream")) else "not_covered"
    return {**base, "status": status, "text": None, "reason": last_reason}


app = FastAPI(title="Bible Commentary Panels", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/commentaries")
async def commentaries():
    return [{"key": c["key"], "name": c["name"], "id": c["id"],
             "available": c["id"] is not None, "note": c["note"],
             "tradition": c["tradition"], "election_stance": c["election_stance"],
             "camp": c["camp"]} for c in COMMENTATORS]


@app.get("/api/verse")
async def verse(
    ref: Optional[str] = Query(None, description="e.g. 'John 3:16'"),
    book: Optional[str] = None,
    chapter: Optional[int] = None,
    verse: Optional[int] = None,
    commentators: Optional[str] = Query(None, description="Comma-separated keys; default all"),
):
    try:
        if ref:
            b, ch, v = parse_ref(ref)
        elif book and chapter and verse:
            b, ch, v = book_number(book), chapter, verse
        else:
            raise ValueError("Provide ?ref=John+3:16 or ?book=John&chapter=3&verse=16")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    wanted = set(commentators.split(",")) if commentators else None
    selected = [c for c in COMMENTATORS if not wanted or c["key"] in wanted]

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                 timeout=TIMEOUT, follow_redirects=True) as client:
        panels = await asyncio.gather(*(build_panel(client, c, b, ch, v) for c in selected))

    return {"ref": f"{BOOKS[b - 1]} {ch}:{v}", "book": b, "chapter": ch, "verse": v,
            "panels": list(panels), "tensions": detect_tensions([c["key"] for c in selected])}


# Serve the frontend from the same server at http://localhost:8000/
# Mounted last so /api/* and /health take precedence.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    from fastapi.staticfiles import StaticFiles
   app.mount("/", StaticFiles(directory=".", html=True), name="frontend")
    
