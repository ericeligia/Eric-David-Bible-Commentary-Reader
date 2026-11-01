# Bible Commentary — Side by Side

Scaffold for a web app where every selected commentator answers the **same verse at once**,
each in its own labeled card/column (responsive grid: multi-column on wide screens, stacked on phones).

Data: public-domain [GetBible Commentaries API](https://commentaries.getbible.net/v1/) only.
No scraping, no LLM calls — the backend proxies the API and returns raw commentary text.

## Layout

```
bible-commentary-app/
├── start-bible-commentary.bat.txt      # Windows launcher (rename: drop .txt, then double-click)
├── start-bible-commentary.command.txt  # Mac launcher (rename: drop .txt, then double-click)
├── backend/
│   ├── main.py            # FastAPI: /health, /api/commentaries, /api/verse; serves ../frontend at /
│   └── requirements.txt
├── frontend/
│   ├── index.html         # verse input, commentator checkboxes, panel grid
│   ├── styles.css
│   └── app.js             # vanilla JS, no build step
├── api-sample-john-3-16.json   # API research + refined findings
└── sample-mhcc-john-3.json
```

## Run locally

**Easiest:** rename `start-bible-commentary.bat.txt` → `start-bible-commentary.bat` (Windows) or `start-bible-commentary.command.txt` → `start-bible-commentary.command` (Mac), then double-click (first time on Mac, right-click → Open if blocked). It creates `.venv` if needed, installs requirements, starts the server, and opens <http://localhost:8000/>.

Manual (same steps the launcher runs):

```bash
cd bible-commentary-app
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn main:app --reload --port 8000
```

Deep link: `http://localhost:8000/?ref=Psalms+23:1`.

Opening `frontend/index.html` straight from disk also works; it then calls
`http://localhost:8000` (override with `?api=http://host:port`). CORS is open.

## API

| Endpoint | Description |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `GET /api/commentaries` | The 10 commentators in display order with `id`, `available`, `note` |
| `GET /api/verse?ref=John+3:16` | One panel per commentator |
| `GET /api/verse?book=John&chapter=3&verse=16` | Same, split params |
| `&commentators=matthew-henry,barnes` | Optional subset (keys from `/api/commentaries`) |

Panel `status` values: `ok` (has `text`, `verses`, `source_url`), `unavailable` (work not in the API),
`not_covered` (no entry for this book/chapter/verse), `error` (network/upstream failure). Bad refs → HTTP 400.

Book parsing accepts full names, common abbreviations (`Jn`, `1 Cor`, `Ps`, `Rev`), Roman/word
ordinals (`I John`, `First Peter`), unique prefixes, or GetBible numbers 1–66.

## Commentator mapping

| Display | GetBible id | Coverage |
|---|---|---|
| Matthew Henry | `mhc` (fallback `mhcc`) | Whole Bible; entries often span many verses |
| John Gill | — | Not in API (placeholder) |
| Adam Clarke | `clarke` | Whole Bible |
| Spurgeon | `tdavid` | Treasury of David — Psalms only |
| JFB | `jfb` | Whole Bible |
| Barnes | `barnes` | NT + parts of OT (404 for Genesis, Psalms) |
| Wesley | `wesley` | Whole Bible |
| Geneva | — | Not in API (placeholder) |
| Keil-Delitzsch | `kd` | Old Testament only |
| Vincent | — | Not in API (placeholder) |

## Theological traditions & tensions

Every commentator (in `/api/commentaries` and on every `/api/verse` panel) carries
`tradition`, `election_stance` (one sentence) and `camp`
(`high-calvinist`, `calvinist`, `moderate-calvinist`, `arminian`, `lutheran`, `moderate`).
The UI shows the tradition as a colour-coded chip under the name; hover it for the stance.

`/api/verse` also returns `tensions`: one-sentence callouts on election/predestination, computed from
the **requested** commentators (regardless of whether text loaded). Rules (`TENSION_RULES` in `main.py`):

1. High Calvinist (Gill, Geneva) vs Arminian (Wesley, Clarke): strong
2. High Calvinist (Geneva, Gill) vs Lutheran (Keil-Delitzsch): moderate
3. Spurgeon vs Arminian: soft, only when rule 1 did not fire

One callout per rule; the first present name on each side is the representative, the rest are
listed in `also_a` / `also_b`. Tensions are tradition-based, not derived from the verse text.

Chapter responses are cached in memory (up to 500 chapters) for fast repeat lookups.

## Next steps (ideas)
- Persist the chapter cache to disk.
- Trim long range entries (e.g. Matthew Henry John 3:1–21 ≈ 60k chars) to the paragraph mentioning the verse.
- Find public-domain sources for Gill, Geneva notes, and Vincent's Word Studies.
