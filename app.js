// Vanilla JS client for the side-by-side commentary panels.
// When served by FastAPI (same origin) API_BASE is "". If you open index.html
// from disk, it falls back to http://localhost:8000 — or set ?api=http://host:port
(() => {
  const params = new URLSearchParams(location.search);
  const API_BASE = params.get("api") || (location.protocol === "file:" ? "http://localhost:8000" : "");

  // Fallback list (same order as backend) if /api/commentaries is unreachable.
  let COMMENTATORS = [
    ["matthew-henry", "Matthew Henry", true], ["john-gill", "John Gill", false],
    ["adam-clarke", "Adam Clarke", true], ["spurgeon", "Spurgeon", true],
    ["jfb", "JFB", true], ["barnes", "Barnes", true], ["wesley", "Wesley", true],
    ["geneva", "Geneva", false], ["keil-delitzsch", "Keil-Delitzsch", true],
    ["vincent", "Vincent", false],
  ].map(([key, name, available]) => ({ key, name, available, note: null }));

  const $ = (id) => document.getElementById(id);
  const form = $("verse-form"), input = $("ref"), btn = $("submit-btn");
  const list = $("commentator-list"), panels = $("panels"), statusEl = $("status"), title = $("ref-title");
  const tensionsEl = $("tensions");

  const STATUS_LABEL = { ok: "", unavailable: "Not in API", not_covered: "No entry", error: "Error" };

  function setStatus(msg, isError = false) {
    statusEl.textContent = msg;
    statusEl.classList.toggle("error", isError);
  }

  function renderPicker() {
    list.innerHTML = "";
    for (const c of COMMENTATORS) {
      const label = document.createElement("label");
      if (!c.available) label.classList.add("unavailable");
      label.title = c.note || "";
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = c.key; cb.checked = true;
      cb.addEventListener("change", updateCount);
      label.append(cb, " " + c.name);
      list.append(label);
    }
    updateCount();
  }

  const selectedKeys = () => [...list.querySelectorAll("input:checked")].map((cb) => cb.value);
  function updateCount() { $("selected-count").textContent = `(${selectedKeys().length}/${COMMENTATORS.length})`; }
  $("select-all").onclick = () => { list.querySelectorAll("input").forEach((cb) => (cb.checked = true)); updateCount(); };
  $("select-none").onclick = () => { list.querySelectorAll("input").forEach((cb) => (cb.checked = false)); updateCount(); };

  function tradTag(c) {
    if (!c || !c.tradition) return null;
    const tag = document.createElement("span");
    tag.className = `trad-tag camp-${c.camp || "moderate"}`;
    tag.textContent = c.tradition;
    tag.title = c.election_stance || "";
    return tag;
  }

  function card(name, cls, metaText, bodyNode, info) {
    const el = document.createElement("article");
    el.className = `card ${cls}`;
    if (info && info.key) el.dataset.key = info.key;
    const header = document.createElement("header");
    const titleBox = document.createElement("div"); titleBox.className = "title-box";
    const h = document.createElement("h3"); h.textContent = name;
    titleBox.append(h);
    const tag = tradTag(info);
    if (tag) titleBox.append(tag);
    const meta = document.createElement("div"); meta.className = "meta"; meta.innerHTML = metaText;
    header.append(titleBox, meta);
    const body = document.createElement("div"); body.className = "body";
    body.append(bodyNode);
    el.append(header, body);
    return el;
  }

  function renderLoading(keys) {
    panels.innerHTML = "";
    for (const c of COMMENTATORS.filter((c) => keys.includes(c.key))) {
      const frag = document.createDocumentFragment();
      for (let i = 0; i < 4; i++) { const s = document.createElement("div"); s.className = "skeleton"; s.style.width = `${95 - i * 12}%`; frag.append(s); }
      panels.append(card(c.name, "loading", "loading…", frag, c));
    }
  }

  function renderPanels(data) {
    panels.innerHTML = "";
    title.hidden = false;
    title.textContent = data.ref;
    for (const p of data.panels) {
      const meta = [];
      if (p.id) meta.push(`<code>${p.id}</code>`);
      if (p.status === "ok" && p.verses && p.verses.length > 1)
        meta.push(`vv. ${p.verses[0]}–${p.verses[p.verses.length - 1]}`);
      if (STATUS_LABEL[p.status]) meta.push(`<span class="badge">${STATUS_LABEL[p.status]}</span>`);
      const reason = p.reason || "No commentary available.";
      const text = p.status === "ok" ? p.text : reason + (p.note && p.note !== reason ? ` (${p.note})` : "");
      const el = card(p.name, p.status, meta.join(" · "), document.createTextNode(text), p);
      const vs = (data.tensions || []).filter((t) => t.a_key === p.key || t.b_key === p.key)
        .map((t) => (t.a_key === p.key ? t.b : t.a));
      if (vs.length) { el.classList.add("in-tension"); el.title = `In tension with ${vs.join(", ")} on election`; }
      panels.append(el);
    }
    renderTensions(data.tensions || []);
    const ok = data.panels.filter((p) => p.status === "ok").length;
    setStatus(`${ok} of ${data.panels.length} commentators have an entry for ${data.ref}.`);
  }

  function renderTensions(tensions) {
    tensionsEl.innerHTML = "";
    tensionsEl.hidden = !tensions.length;
    for (const t of tensions) {
      const row = document.createElement("div");
      row.className = `tension ${t.severity || ""}`;
      const label = document.createElement("strong");
      label.textContent = `${t.a} ↔ ${t.b}`;
      const extra = [...(t.also_a || []), ...(t.also_b || [])];
      const sentence = document.createElement("span");
      sentence.textContent = ` ${t.sentence}` + (extra.length ? ` Same divide: ${extra.join(", ")}.` : "");
      row.append(label, sentence);
      for (const k of [t.a_key, t.b_key]) {
        row.addEventListener("mouseenter", () => panels.querySelector(`[data-key="${k}"]`)?.classList.add("highlight"));
        row.addEventListener("mouseleave", () => panels.querySelector(`[data-key="${k}"]`)?.classList.remove("highlight"));
      }
      tensionsEl.append(row);
    }
  }

  async function lookup(ref) {
    const keys = selectedKeys();
    if (!keys.length) { setStatus("Select at least one commentator.", true); return; }
    btn.disabled = true; title.hidden = true;
    setStatus(`Loading ${ref}…`);
    renderLoading(keys);
    renderTensions([]);
    try {
      const qs = new URLSearchParams({ ref, commentators: keys.join(",") });
      const res = await fetch(`${API_BASE}/api/verse?${qs}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Server returned ${res.status}`);
      renderPanels(body);
      const url = new URL(location.href); url.searchParams.set("ref", ref); history.replaceState(null, "", url);
    } catch (err) {
      panels.innerHTML = "";
      renderTensions([]);
      setStatus(`Could not load commentary: ${err.message}`, true);
    } finally {
      btn.disabled = false;
    }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); const r = input.value.trim(); if (r) lookup(r); });

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/commentaries`);
      if (res.ok) COMMENTATORS = await res.json();
    } catch { /* keep fallback list */ }
    renderPicker();
    const initial = params.get("ref");
    if (initial) { input.value = initial; lookup(initial); }
  })();
})();
