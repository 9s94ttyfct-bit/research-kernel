// Research Kernel Workbench: one page, hash routes, no build step. Everything the server returns that came from
// sources or agents is inserted as text (textContent), never as markup; only the draft HTML, which the server renders
// with raw HTML escaped, is inserted as HTML.

const state = { project: null, ws: null, chat: null, openCount: 0 };
const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// Replace a node's children, flattening nested arrays the same way el() does.
function fill(node, ...children) {
  node.replaceChildren(...el("div", {}, ...children).childNodes);
}

function toast(message, kind = "info") {
  const t = $("#toast");
  t.textContent = message;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, kind === "error" ? 8000 : 3500);
}

async function api(path, { method = "GET", body, query } = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(query || {})) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401) {
    renderSignedOut();
    throw new Error("signed out");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    const d = data.detail;
    const msg = typeof d === "string" ? d : d?.message || JSON.stringify(d || data.error || res.statusText);
    throw Object.assign(new Error(msg), { detail: d });
  }
  return data.result;
}

function renderSignedOut() {
  fill($("#page"), el("h1", {}, "Open the Workbench from Research Kernel"),
    el("p", { class: "lead" }, "This page needs a fresh sign-in link. Run `rk open` in a terminal and open the link it prints, or use the Workbench tab in the built-in browser window."));
}

// ---------------------------------------------------------------------------------------------------------------
// project switcher, browser state, live updates

async function loadProjects() {
  const res = await api("/ui/api/projects");
  const sel = $("#project-select");
  fill(sel, ...res.projects.map((p) => el("option", { value: p.slug }, `${p.title} (${p.slug})`)));
  if (!res.projects.length) sel.append(el("option", { value: "" }, "No project yet"));
  state.project = res.current && res.projects.some((p) => p.slug === res.current) ? res.current : res.projects[0]?.slug || null;
  if (state.project) sel.value = state.project;
  return res;
}

function showBrowser(status) {
  const b = $("#browser-state");
  if (!status || !status.running) {
    b.dataset.state = "off";
    b.textContent = "Browser: not running";
    return;
  }
  const paused = status.paused_sites || [];
  b.dataset.state = paused.length ? "paused" : "running";
  b.textContent = paused.length ? `Browser: waiting for you (${paused.map((p) => p.site).join(", ")})` : "Browser: ready";
}

function setOpenCount(n) {
  state.openCount = n;
  const c = $("#open-count");
  c.hidden = n === 0;
  c.textContent = String(n);
}

async function refreshOpenCount() {
  if (!state.project) return setOpenCount(0);
  const res = await api("/ui/api/checkpoints", { query: { project: state.project, status: "open" } });
  setOpenCount(res.checkpoints.length);
}

const listeners = new Set();

function connect() {
  if (state.ws) state.ws.close();
  state.chat = null;
  if (!state.project) return;
  const ws = new WebSocket(`${location.origin.replace(/^http/, "ws")}/ui/ws?project=${encodeURIComponent(state.project)}`);
  state.ws = ws;
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "browser") showBrowser(msg.status);
    if (msg.type === "checkpoint") setOpenCount(msg.open_count);
    for (const fn of listeners) fn(msg);
  };
  ws.onclose = (e) => {
    if (state.ws === ws && e.code !== 1000) setTimeout(connect, 3000);
  };
}

function listen(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ---------------------------------------------------------------------------------------------------------------
// pages

const pages = {};
let leaving = [];  // what the shown page undoes when the user moves on: its subscriptions, its open chat
let chatOpens = 0;  // numbers each chat.open so a re-rendered chat page ignores the replies meant for an earlier render

function needProject() {
  if (state.project) return false;
  fill($("#page"), el("h1", {}, "No project yet"),
    el("p", { class: "lead" }, "Create a project first; every library, chat and checkpoint belongs to one."),
    el("p", {}, el("a", { href: "#/projects" }, "Create a project")));
  return true;
}

pages.projects = async (root) => {
  const res = await api("/ui/api/projects");
  const list = res.projects.length
    ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Title"), el("th", {}, "Slug"), el("th", {}, ""))),
        el("tbody", {}, res.projects.map((p) => el("tr", {},
          el("td", {}, p.title), el("td", {}, p.slug),
          el("td", {}, p.slug === state.project ? "Current" : el("button", { class: "secondary", onclick: () => switchProject(p.slug) }, "Switch to this project"))))))
    : el("p", { class: "empty" }, "No projects yet.");
  const form = el("form", { class: "inline" },
    el("label", { class: "field" }, "Title", el("input", { type: "text", name: "title", required: true })),
    el("label", { class: "field" }, "Short name (letters, digits, dashes)", el("input", { type: "text", name: "slug", required: true, pattern: "[a-z0-9][a-z0-9-]{1,62}" })),
    el("label", { class: "field" }, "Field", el("input", { type: "text", name: "field" })),
    el("label", { class: "field" }, "Paper type", el("select", { name: "paper_type" },
      ["critical_review", "systematic_review", "scoping_review", "research_proposal"].map((t) => el("option", { value: t }, t.replace("_", " "))))),
    el("button", { type: "submit" }, "Create project"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    try {
      await api("/ui/api/projects", { method: "POST", body: f });
      toast(`Created ${f.title}`);
      await switchProject(f.slug);
    } catch (err) { toast(err.message, "error"); }
  });
  fill(root, el("h1", {}, "Projects"), list, el("h2", {}, "New project"), form);
};

async function switchProject(slug) {
  await api("/ui/api/projects/current", { method: "POST", body: { slug } });
  state.project = slug;
  await loadProjects();
  connect();
  refreshOpenCount();
  route();
}

pages.chat = async (root) => {
  if (needProject()) return;
  const transcript = el("div", { class: "transcript", "aria-live": "polite" });
  const input = el("textarea", { name: "text", placeholder: "Ask the Lead agent (English or Chinese)…", "aria-label": "Message" });
  const send = el("button", { type: "submit" }, "Send");
  const form = el("form", { class: "chat-input" }, input, send);
  const status = el("p", { class: "hint" }, "Starting a chat with the Lead agent…");
  fill(root, el("h1", {}, "Chat"), el("p", { class: "lead" }, "The Lead agent works on this project with its tools; tool calls appear folded under each answer."), status, transcript, form);

  let current = null;
  const ref = ++chatOpens;
  state.chat = null;  // until this render's own chat.opened arrives
  const ws = state.ws;
  let chat = null, left = false;
  const close = (id) => { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "chat.close", chat_id: id })); };
  // Kept past leaving the page: a chat opened for this render after the user has moved on is closed, not leaked.
  const stopOpening = listen((msg) => {
    if (msg.ref !== ref || (msg.type !== "chat.opened" && msg.type !== "chat.error")) return;
    stopOpening();
    if (msg.type === "chat.error") status.textContent = `Chat unavailable: ${msg.message}`;
    else if (left) close(msg.chat_id);
    else { chat = state.chat = msg.chat_id; status.textContent = `Talking to ${msg.driver}.`; }
  });
  root.onLeave(() => {
    left = true;
    if (!chat) return;
    close(chat);
    if (state.chat === chat) state.chat = null;
  });
  const add = (node) => { transcript.append(node); node.scrollIntoView({ block: "end" }); return node; };
  root.listen((msg) => {
    if (msg.type !== "chat" || msg.chat_id !== state.chat) return;
    if (msg.kind === "text") {
      current = current || add(el("div", { class: "msg" }));
      current.textContent += msg.data.text ?? msg.data.content ?? "";
    } else if (msg.kind === "tool_call" || msg.kind === "tool_result") {
      add(el("details", { class: "tool" }, el("summary", {}, msg.kind === "tool_call" ? `Tool: ${msg.data.name ?? ""}` : "Tool result"),
        el("pre", {}, JSON.stringify(msg.data, null, 2))));
    } else if (msg.kind === "error") {
      add(el("div", { class: "msg error" }, msg.data.message || "error"));
      send.disabled = false;
    } else if (msg.kind === "done") {
      current = null;
      send.disabled = false;
    }
  });
  const open = () => state.ws.send(JSON.stringify({ type: "chat.open", ref }));
  if (state.ws && state.ws.readyState === WebSocket.OPEN) open(); else state.ws?.addEventListener("open", open, { once: true });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !state.chat) return;
    add(el("div", { class: "msg you" }, text));
    current = null;
    send.disabled = true;
    input.value = "";
    state.ws.send(JSON.stringify({ type: "chat.send", chat_id: state.chat, text }));
  });
};

function badgeList(b) {
  const ref = { pass: "ok", page_metadata_only: "warn", mismatch: "bad", not_found: "bad" }[b.ref_status] || "neutral";
  const retr = ["retracted", "withdrawn", "partial_retraction", "eoc"].includes(b.retraction) ? "bad" : b.retraction === "none" ? "ok" : "neutral";
  return el("span", { class: "badges" },
    el("span", { class: `badge ${ref}`, title: "Reference check (V-REF)" }, `Reference: ${b.ref_status.replaceAll("_", " ")}`),
    el("span", { class: `badge ${retr}`, title: "Retraction check" }, `Retraction: ${b.retraction}`),
    el("span", { class: `badge ${b.fulltext ? "ok" : "neutral"}` }, b.fulltext ? "Full text" : "No full text"),
    el("span", { class: `badge ${b.quotes_verified ? "ok" : "neutral"}` }, `${b.quotes_verified} verified quote${b.quotes_verified === 1 ? "" : "s"}`));
}

pages.library = async (root, params) => {
  if (needProject()) return;
  const q = params.get("q") || "";
  const res = await api("/ui/api/library", { query: { project: state.project, q, collection: params.get("collection") } });
  const search = el("input", { type: "search", name: "q", value: q, placeholder: "Search titles, notes and full text", "aria-label": "Search the library", size: 34 });
  const colSel = el("select", { name: "collection", "aria-label": "Collection" }, el("option", { value: "" }, "All items"),
    res.collections.map((c) => el("option", { value: c.id, selected: params.get("collection") === c.id }, c.name)));
  const tools = el("form", { class: "toolbar" }, search, colSel, el("button", { type: "submit" }, "Search"),
    el("button", { type: "button", class: "secondary", onclick: savePage, title: "Alt+S" }, "Save the page open in the browser"),
    el("label", { class: "field" }, "Import BibTeX, RIS or CSL-JSON", el("input", { type: "file", accept: ".bib,.ris,.json", onchange: importFile })),
    el("span", { class: "hint" }, "Export: ", ["bibtex", "ris", "csl"].map((f) => [el("a", { href: `/ui/api/library/export?project=${encodeURIComponent(state.project)}&format=${f}` }, f), " "])));
  tools.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = new URLSearchParams();
    if (search.value) p.set("q", search.value);
    if (colSel.value) p.set("collection", colSel.value);
    location.hash = `#/library?${p}`;
  });
  const detail = el("section", { class: "detail", "aria-live": "polite" }, el("p", { class: "empty" }, "Choose an item to see its sources, files and notes."));
  const items = el("ul", { class: "items", role: "listbox", "aria-label": "Library items" },
    res.items.length ? res.items.map((i) => {
      const li = el("li", { role: "option", tabindex: "0", "aria-selected": "false", "data-item": i.id },
        el("div", { class: "title" }, i.title),
        el("div", { class: "meta" }, [i.creators?.[0]?.family, i.year, i.venue].filter(Boolean).join(", ")),
        badgeList(i.badges));
      const pick = () => { items.querySelectorAll("li").forEach((x) => x.setAttribute("aria-selected", "false")); li.setAttribute("aria-selected", "true"); showItem(i.id, detail); };
      li.addEventListener("click", pick);
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
      return li;
    }) : el("li", { class: "empty" }, q ? "Nothing matches this search." : "The library is empty. Search in the browser, then save a page or import a file."));
  fill(root, el("h1", {}, "Library"), tools, el("div", { class: "split" }, items, detail));
  const chosen = params.get("item");  // a link to an item (and one of its notes): #/library?item=…&note=…
  if (chosen) {
    items.querySelector(`li[data-item="${CSS.escape(chosen)}"]`)?.setAttribute("aria-selected", "true");
    await showItem(chosen, detail, params.get("note"));
  }
};

// Bring one of the shown item's notes into view and focus it.
function focusNote(box, noteId) {
  const q = box.querySelector(`blockquote[data-note-id="${CSS.escape(noteId)}"]`);
  if (!q) return toast("That note is not one of this item's notes.", "error");
  box.querySelectorAll("blockquote.target").forEach((x) => x.classList.remove("target"));
  q.classList.add("target");
  q.scrollIntoView({ block: "center" });
  q.focus({ preventScroll: true });
}

// What a quote note was verified against, in words (a quote of the full text shows its page instead)
const AGAINST = { abstract: "abstract", record: "bibliographic record" };
const noteLabel = (against) => `Quote note${AGAINST[against] ? ` (${AGAINST[against]})` : ""}`;

// The extraction of a study (read_extract): the values that are usable evidence, each linked to its quote note, and
// apart from them the rows that are no evidence, with their problem. `ex` is the whole extraction: its attempt and
// whether that attempt passed read_extract's gate.
function extractionSection(ex, s, itemId, box) {
  const value = (v) => (v === "not_reported" ? "not reported" : v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
  const state = s.in_evidence
    ? s.read_on === "abstract" ? "Read on the abstract only: every value from it has sufficiency low." : "Read on the full text."
    : `Not in the evidence: ${s.out_reason}.`;
  const gate = ex.gate && !ex.gate.passed
    ? el("p", { class: "notice gate-hint" }, `From read_extract attempt ${ex.attempt}, which has not passed its gate`,
        ex.gate.failures.length ? `: ${ex.gate.failures.join("; ")}` : " yet", ". Later stages read only a passed attempt.")
    : null;
  return [
    el("h2", {}, "Extraction"),
    gate,
    el("p", { class: s.in_evidence ? "hint" : "hint bad" }, state),
    s.values.length
      ? el("table", { class: "extraction" }, el("thead", {}, el("tr", {}, ["Field", "Value", "Sufficiency", "Evidence"].map((h) => el("th", {}, h)))),
          el("tbody", {}, s.values.map((v) => el("tr", {}, el("td", {}, v.field), el("td", {}, value(v.value)),
            el("td", { class: v.sufficient === "low" ? "low" : "" }, v.sufficient),
            el("td", {}, v.note ? el("a", { href: `#/library?item=${encodeURIComponent(itemId)}&note=${encodeURIComponent(v.note.id)}`,
              onclick: (e) => { e.preventDefault(); focusNote(box, v.note.id); } }, noteLabel(v.note.against)) : "—")))))
      : el("p", { class: "empty" }, "No value of this study is usable evidence."),
    s.set_aside.length
      ? el("div", { class: "set-aside" }, el("h3", {}, "Not used as evidence"),
          el("ul", {}, s.set_aside.map((r) => el("li", {}, el("strong", {}, r.field), ` = ${value(r.value)}: `, r.problem))))
      : null,
    s.problems.length ? el("ul", { class: "hint" }, s.problems.map((p) => el("li", {}, p))) : null,
  ];
}

async function showItem(id, box, noteId = null) {
  const [d, ex] = await Promise.all([api(`/ui/api/library/item/${encodeURIComponent(id)}`, { query: { project: state.project } }),
    api("/ui/api/extraction", { query: { project: state.project, item_id: id } }).catch((err) => ({ error: err.message }))]);
  const it = d.item;
  const ids = Object.entries(it.ids).filter(([, v]) => v);
  const study = ex.studies?.[0];
  fill(box, el("h2", {}, it.title),
  el("p", { class: "meta" }, [it.creators.map((c) => [c.given, c.family].filter(Boolean).join(" ")).join(", "), it.year, it.venue].filter(Boolean).join(" — ")),
  badgeList(d.badges),
  ids.length ? el("p", { class: "hint" }, ids.map(([k, v]) => `${k}: ${v}`).join("   ")) : null,
  study ? extractionSection(ex, study, id, box) : ex.error ? el("p", { class: "error" }, `Could not load the extraction: ${ex.error}`) : null,
  el("h2", {}, "Files"),
  d.attachments.length
    ? el("ul", {}, d.attachments.map((a) => el("li", {},
        el("a", { href: `/ui/api/files/${encodeURIComponent(state.project)}/${a.id}`, target: "_blank", rel: "noopener" }, a.kind === "pdf" ? "PDF" : "Saved page"),
        ` — ${a.origin.replaceAll("_", " ")}`, a.access_note ? el("div", { class: "hint" }, a.access_note) : null)))
    : el("p", { class: "empty" }, "No file yet. The Reader fetches open-access text first, then the library page in the browser."),
  el("h2", {}, "Notes"),
  d.notes.length
    ? el("div", {}, d.notes.map((n) => el("blockquote", { class: `note ${n.kind === "quote" && !n.verified?.quote?.ok ? "unverified" : ""}`, "data-note-id": n.id, tabindex: "-1" },
        n.text, el("div", { class: "hint" }, `${n.kind}${AGAINST[n.verified?.quote?.against] ? `, ${AGAINST[n.verified.quote.against]}` : n.locator ? `, page ${n.locator.page}` : ""}`))))
    : el("p", { class: "empty" }, "No notes yet."));
  if (noteId) focusNote(box, noteId);
}

async function savePage() {
  if (!state.project) return toast("Create a project first", "error");
  try {
    const collection = location.hash.startsWith("#/library") ? document.querySelector('select[name="collection"]')?.value : "";
    const r = await api("/ui/api/library/save-page", { method: "POST", body: { project: state.project, ...(collection ? { collection } : {}) } });
    toast(r.created ? `Saved to the library (${r.attachment?.kind === "pdf" ? "with PDF" : "page snapshot"})` : "Already in the library; file added");
    if (location.hash.startsWith("#/library")) route();
  } catch (err) { toast(err.message, "error"); }
}

async function importFile(e) {
  const file = e.target.files[0];
  if (!file) return;
  const format = file.name.endsWith(".ris") ? "ris" : file.name.endsWith(".json") ? "csl" : "bibtex";
  try {
    const r = await api("/ui/api/library/import", { method: "POST", body: { project: state.project, format, text: await file.text() } });
    toast(`Imported ${r.added ?? r.items?.length ?? "the"} items`);
    route();
  } catch (err) { toast(err.message, "error"); }
}

// ---------------------------------------------------------------------------------------------------------------
// discovery: the review cards (topics, research question, a blocked stage) and the stage graph

const GAP_KINDS = { contradiction: "Contradiction", stated_limitation: "Stated limitation", citation_distortion: "Citation distortion",
  retracted_still_cited: "Retracted, still cited", evidence_map_hole: "Evidence-map hole" };
const words = (s) => String(s ?? "").replaceAll("_", " ");
const yearOf = (w) => (w.year ? ` (${w.year})` : "");
const count = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// A definition list of [term, value] pairs; empty values are left out.
function facts(pairs) {
  return el("dl", { class: "facts" }, pairs.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}

function reviseForm(label, onSend, extra = null) {
  const comment = el("textarea", { name: "comment", required: true });
  const form = el("form", { class: "revise" }, el("label", { class: "field" }, label, comment), extra,
    el("button", { type: "submit", class: "secondary" }, "Ask for a revision"));
  form.addEventListener("submit", (e) => { e.preventDefault(); if (comment.value.trim()) onSend(comment.value.trim(), form); });
  return form;
}

function topicBlock(t, answer) {
  const ref = t.reference || {};
  return el("li", { class: "topic" },
    el("h3", {}, t.title),
    el("p", {}, t.summary),
    facts([["Feasibility", t.feasibility], ["Data needed", t.data_needed], ["Paper type", words(t.paper_type)], ["Risks", t.risks]]),
    el("h4", {}, "Gaps it rests on"),
    t.gaps.length
      ? el("ul", { class: "gaps" }, t.gaps.map((g) => el("li", {},
          el("div", { class: "badges" }, el("span", { class: "badge neutral" }, GAP_KINDS[g.kind] || words(g.kind)),
            el("span", { class: `badge ${g.verified ? "ok" : "warn"}` }, g.verified ? "Evidence chain checked" : "Evidence chain not checked")),
          el("p", { class: "statement" }, g.statement),
          g.quotes.map((q) => el("blockquote", { class: "note" }, q.quote, el("div", { class: "hint" }, q.item_title || q.item_id))))))
      : el("p", { class: "empty" }, "The gaps of this topic were not found."),
    el("h4", {}, "Closest prior work (reference only)"),
    el("p", { class: "hint" }, "How the judges say the topic differs from each work. A reference for your decision, not a verdict on it.",
      ref.queries ? ` ${count(ref.queries.length, "search", "searches")} on ${(ref.sources_used || []).join(", ") || "no source"} found ${count(ref.found ?? 0, "work")}.` : ""),
    t.closest.length
      ? el("ul", { class: "closest" }, t.closest.map((w) => el("li", {},
          el("div", { class: "work-title" }, w.title, yearOf(w)),
          Object.keys(w.differences || {}).length
            ? el("dl", { class: "facts diffs" }, Object.entries(w.differences).map(([vendor, d]) => [el("dt", {}, `Judge (${vendor})`), el("dd", {}, d)]))
            : el("p", { class: "empty" }, "No difference recorded."))))
      : el("p", { class: "empty" }, "No closest work listed."),
    el("div", { class: "toolbar" }, el("button", { type: "button", "aria-label": `Approve this topic: ${t.title}`, onclick: () => answer({ decision: "approve", topic_id: t.id }) }, "Approve this topic")));
}

async function topicReviewCard(c, answer) {
  const o = c.options || {};
  const data = await api("/ui/api/topics", { query: { project: state.project, attempt: o.attempt } });
  const byId = new Map(data.topics.map((t) => [t.id, t]));
  const under = (o.topic_ids || []).map((id) => byId.get(id)).filter((t) => t && t.status !== "rejected");
  const target = el("select", { name: "target" }, el("option", { value: "topics" }, "New topics from the same gaps"), el("option", { value: "gaps" }, "Go back to the gaps"));
  return [
    under.length ? el("ol", { class: "topics" }, under.map((t) => topicBlock(t, answer))) : el("p", { class: "empty" }, "The topics under review were not found."),
    el("div", { class: "other-answers" },
      el("h4", {}, "Or revise or reject them"),
      reviseForm("What should change", (comment) => answer({ decision: "revise", target: target.value, comment }), el("label", { class: "field" }, "What to redo", target)),
      el("div", { class: "toolbar" }, el("button", { type: "button", class: "secondary", onclick: () => answer({ decision: "reject" }) }, "Reject all topics"))),
  ];
}

function alreadyDone(ad) {
  if (!ad) return el("p", { class: "empty" }, "The already-done check has not run.");
  const head = {
    not_found_in_scope: "Prior work answering this question was not found within the searched scope.",
    already_done: "At least two judges of different vendors say published work already answers this question.",
    split: ad.split_reason || "The judges did not agree, or fewer than two judge vendors gave valid judgements.",
  }[ad.result] || words(ad.result);
  const skipped = Object.entries(ad.skipped || {});
  return [
    el("p", {}, el("strong", {}, head)),
    el("p", { class: "hint" }, `${count(ad.queries.length, "search", "searches")} on ${ad.sources_used.join(", ") || "no source"}; the top ${ad.works.length} of ${count(ad.found, "work")} judged by ${ad.judged.join(", ") || "no judge"}.`),
    skipped.length ? el("p", { class: "hint" }, "Judges skipped: ", skipped.map(([role, why]) => `${role}: ${why}`).join("; ")) : null,
    ad.works.length ? el("ul", { class: "works" }, ad.works.map((w) => el("li", {},
      el("div", { class: "work-title" }, w.title, yearOf(w)),
      Object.entries(w.judgements || {}).map(([vendor, j]) => el("div", { class: "judgement" },
        el("div", { class: "hint" }, `${vendor}: ${j.answered ? "already answers it" : "does not answer it"}${j.ok_quote ? "" : " (quote not verbatim in what the judge saw: discarded)"}`),
        el("blockquote", { class: `note${j.ok_quote ? "" : " unverified"}` }, j.quote)))))) : null,
  ];
}

async function rqCard(c, answer) {
  const d = await api("/ui/api/rq", { query: { project: state.project, rq_id: c.options?.rq_id } });
  const b = d.rq.body;
  const view = [
    el("p", { class: "rq-sentence" }, d.sentence),
    d.topic ? el("p", { class: "hint" }, "Topic: ", d.topic.title) : null,
    el("h4", {}, "Problem"), el("p", {}, b.problem.condition),
    b.problem.consequences.length ? el("ul", {}, b.problem.consequences.map((x) => el("li", {}, x))) : null,
    el("h4", {}, "FINER (as the Ideator wrote it)"),
    facts([["Feasible", b.finer.feasible], ["Interesting", b.finer.interesting], ["Novelty (as the Ideator claimed it)", b.finer.novel], ["Ethical", b.finer.ethical], ["Relevant", b.finer.relevant]]),
    b.clinical && b.pico ? [el("h4", {}, "PICO"), facts([["Population", b.pico.population], ["Intervention", b.pico.intervention], ["Comparison", b.pico.comparison], ["Outcome", b.pico.outcome]])] : null,
    el("h4", {}, "Hypotheses"),
    el("ol", { class: "hypotheses" }, d.hypotheses.map((h) => el("li", {}, el("p", {}, h.statement), el("p", { class: "hint" }, "Falsified if: ", h.falsification)))),
    el("h4", {}, "Already-done check"),
    alreadyDone(d.rq.already_done),
  ];
  if (c.kind === "significance_novelty") {
    return [...view,
      el("p", { class: "hint" }, "The judges did not settle whether published work already answers this question. Proceed treats it as not found within the searched scope, and you then approve it; or go back to the topics."),
      el("div", { class: "toolbar" },
        el("button", { type: "button", onclick: () => answer({ decision: "proceed" }) }, "Proceed with this question"),
        el("button", { type: "button", class: "secondary", onclick: () => answer({ decision: "back" }) }, "Go back to topics"))];
  }
  return [...view,
    el("div", { class: "toolbar" }, el("button", { type: "button", onclick: () => answer({ decision: "approve" }) }, "Approve this research question")),
    reviseForm("What should change in the question", (comment) => answer({ decision: "revise", comment }))];
}

function blockedCard(c, answer) {
  const o = c.options || {};
  const comment = optionalComment(`Guidance for ${words(o.to)} if it runs again (optional)`);
  const allow = () => answer(comment.value() ? { decision: "allow_one_more", comment: comment.value() } : { decision: "allow_one_more" });
  return [
    facts([["Back-edge", (o.edge || "").replace("->", " → ")], ["Allow one more", `${words(o.to)} runs again`], ["Stop", "the stage ends here (an active run fails)"]]),
    (o.failures || []).length ? [el("h4", {}, "Why"), el("ul", {}, o.failures.map((f) => el("li", {}, f)))] : null,
    comment.field,
    el("div", { class: "toolbar" },
      el("button", { type: "button", onclick: allow }, "Allow one more use"),
      el("button", { type: "button", class: "secondary", onclick: () => answer({ decision: "stop" }) }, "Stop")),
  ];
}

// ---------------------------------------------------------------------------------------------------------------
// production: the protocol and screening disagreement cards, the PRISMA flow, the extraction of a study

function criteria(list) {
  return list.length ? el("ul", { class: "criteria" }, list.map((x) => el("li", {}, el("strong", {}, x.id), " ", x.text))) : el("p", { class: "empty" }, "None.");
}

// "selection_process" -> "Selection process"; "screener_a" -> "Screener A".
const label = (key) => { const w = words(key); return w ? w[0].toUpperCase() + w.slice(1) : w; };
const screener = (role) => String(role).replace(/^screener_(\w)$/, (_, x) => `Screener ${x.toUpperCase()}`);

// The pool options of a meta-analysis in words (the protocol's synthesis plan, the pooling approval).
const HKSJ = { auto: "auto (the Cochrane Handbook rule)", true: "always", false: "never (Wald intervals)" };
const POOL_MODELS = { DL: "DerSimonian–Laird random effects", REML: "REML random effects", FE: "fixed effect (inverse variance)" };
const PI_DF = { ci: "the confidence interval's (RevMan)", "k-2": "t with k−2 degrees of freedom (Higgins 2009)" };
const minStudies = (n) => `at least ${n} studies with a computable effect (fewer: narrative synthesis)`;
const POOL_LABELS = { pool_model: "Pooling model", hksj: "HKSJ intervals", pi_df: "Prediction interval", min_studies: "Minimum studies" };

async function protocolCard(c, answer) {
  const d = await api("/ui/api/protocol", { query: { project: state.project, protocol_id: c.options?.protocol_id } });
  const p = d.protocol, b = p.body, ss = b.search_strategy;
  const earlier = d.in_force && d.in_force !== p.id ? d.versions.find((v) => v.id === d.in_force) : null;
  const cap = el("input", { type: "number", name: "record_cap", min: "1", step: "1", inputmode: "numeric", placeholder: String(b.record_cap) });
  const approve = el("form", { class: "approve" },
    el("label", { class: "field" }, "Record cap (optional)", cap, el("span", { class: "hint" }, `Leave it empty to keep ${b.record_cap} records.`)),
    el("button", { type: "submit" }, "Approve this protocol"));
  approve.addEventListener("submit", (e) => {
    e.preventDefault();
    const raw = cap.value.trim();
    if (!raw) return answer({ decision: "approve" });
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1) return toast("The record cap is a whole number of records, at least 1.", "error");
    answer({ decision: "approve", record_cap: n });
  });
  const why = el("textarea", { name: "back_comment" });
  const back = el("form", { class: "revise" }, el("label", { class: "field" }, "Why go back (optional)", why),
    el("button", { type: "submit", class: "secondary" }, "Back to the research question"));
  back.addEventListener("submit", (e) => { e.preventDefault(); const comment = why.value.trim(); answer(comment ? { decision: "back", comment } : { decision: "back" }); });
  // every plan value as text: el() skips false, so hksj is shown in words; outcomes with their fields; lists joined
  const outcome = (o) => {
    const fields = Object.entries(o.fields || {}).map(([r, f]) => `${r} = ${f}`).join(", ");
    return fields ? `${o.name} (${fields})` : o.name;
  };
  const planValue = (k, v) => (k === "method" ? words(v) : k === "hksj" ? HKSJ[String(v)] ?? String(v)
    : k === "pool_model" ? POOL_MODELS[v] ?? String(v) : k === "pi_df" ? PI_DF[v] ?? String(v)
    : k === "min_studies" ? minStudies(v)
    : k === "outcomes" ? v.map(outcome).join("; ") : Array.isArray(v) ? v.join(", ") : String(v));
  // the pool options are the meta-analysis's: shown only for that method; the outcomes for every plan
  const POOL_KEYS = new Set(["pool_model", "hksj", "pi_df", "min_studies"]);
  const isMeta = (b.synthesis_plan || {}).method === "meta_analysis";
  const plan = Object.entries(b.synthesis_plan || {})
    .filter(([k, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0)
      && (isMeta || !POOL_KEYS.has(k)));
  const prismaP = Object.entries(b.prisma_p || {});
  return [
    el("p", { class: "hint" }, `Version ${p.version} · ${words(d.paper_type)} · `,
      d.preregistration ? "freezing it counts as preregistration (G-PREREG)" : "freezing it is for traceability only, not preregistration"),
    earlier ? el("p", { class: "notice" }, `Version ${earlier.version} is frozen: approving this one records a deviation from it.`) : null,
    el("h4", {}, "Research question"), el("p", { class: "rq-sentence" }, b.question),
    el("h4", {}, "Search strategy"),
    facts([["Sources", ss.sources.join(", ")], ["Publication years", d.publication_years], ["Languages", ss.languages.join(", ")]]),
    el("ul", { class: "queries", "aria-label": "Queries" }, ss.queries.map((q) => el("li", {}, el("code", {}, q)))),
    // how the kernel runs the search: its facts, as the protocol's text states them
    d.search_execution_lines.map((line) => el("p", { class: "hint" }, line)),
    el("h4", {}, "Record cap"), el("p", {}, `${b.record_cap} records after deduplication.`),
    d.probe_lines.map((line) => el("p", { class: "hint" }, line)),
    el("h4", {}, "Eligibility"),
    el("div", { class: "eligibility" }, el("div", {}, el("div", { class: "hint" }, "Include"), criteria(b.eligibility.include)),
      el("div", {}, el("div", { class: "hint" }, "Exclude"), criteria(b.eligibility.exclude))),
    el("h4", {}, "Extraction schema"),
    b.extraction_schema.length
      ? el("table", { class: "schema" }, el("thead", {}, el("tr", {}, ["Field", "Type", "Description"].map((h) => el("th", {}, h)))),
          el("tbody", {}, b.extraction_schema.map((f) => el("tr", {}, el("td", {}, f.name),
            el("td", {}, f.type, f.categories ? `: ${f.categories.join(" | ")}` : "", f.unit ? ` (${f.unit})` : "", f.source === "record" ? ", from the bibliographic record" : ""), el("td", {}, f.description)))))
      : el("p", { class: "empty" }, "No extraction field."),
    el("h4", {}, "Synthesis plan"),
    plan.length ? facts(plan.map(([k, v]) => [POOL_LABELS[k] ?? label(k), planValue(k, v)])) : el("p", { class: "empty" }, "None."),
    prismaP.length ? [el("h4", {}, "PRISMA-P"), facts(prismaP.map(([k, v]) => [label(k), v]))] : null,
    el("div", { class: "other-answers" }, approve,
      reviseForm("What should change in the protocol", (comment) => answer({ decision: "revise", comment })), back),
  ];
}

const SAID = { yes: "Include", no: "Exclude", unclear: "Unclear" };

async function disagreementCard(c, answer) {
  const d = await api(`/ui/api/screening/disagreements/${encodeURIComponent(c.id)}`, { query: { project: state.project } });
  const chosen = new Map();
  const status = el("p", { class: "hint", role: "status" });
  const submit = el("button", { type: "button", disabled: true, onclick: () => answer({ decisions: Object.fromEntries(chosen) }) }, "Submit decisions");
  const update = () => {
    status.textContent = `${chosen.size} of ${count(d.records.length, "record")} decided`;
    submit.disabled = chosen.size !== d.records.length;  // every record of the checkpoint needs your decision
  };
  update();
  const said = (s) => el("td", { class: "said" }, el("div", { class: "who" }, screener(s.screener)),
    el("strong", {}, s.include ? SAID[s.include] || s.include : "No decision"), s.criterion ? ` (${s.criterion})` : "",
    s.reason ? el("div", { class: "reason" }, s.reason) : null);
  const names = (d.records[0]?.screeners || []).map((s) => screener(s.screener));
  const rows = d.records.map((r) => {
    const group = `decide-${c.id}-${r.record_id}`;
    const radio = (value, label) => el("label", { class: "choice" },
      el("input", { type: "radio", name: group, value, "aria-label": `${label}: ${r.title}`, onchange: () => { chosen.set(r.record_id, value); update(); } }), label);
    return el("tr", { "data-record": r.record_id },
      el("td", { class: "record" }, el("div", { class: "record-title" }, r.title),
        el("div", { class: "hint" }, [r.year, r.venue, r.language && `language ${r.language}`].filter(Boolean).join(" · ")),
        el("div", { class: "hint text-label" }, r.text_label),
        el("div", { class: "excerpt", tabindex: "0", role: "region", "aria-label": `${r.text_label}: ${r.title}` }, r.text || "(no text)")),
      r.screeners.map(said),
      el("td", { class: "decide" }, el("div", { class: "who" }, "Your decision"),
        el("div", { role: "radiogroup", "aria-label": `Your decision: ${r.title}` }, radio("include", "Include"), radio("exclude", "Exclude"))));
  });
  return [
    el("p", { class: "hint" }, `${d.phase_words[0].toUpperCase()}${d.phase_words.slice(1)} screening, checkpoint ${d.batch} of ${d.batches}: the two screeners disagree on ${count(d.records.length, "record")}. Decide each one.`),
    el("h4", {}, "Eligibility criteria"),
    el("div", { class: "eligibility" }, el("div", {}, el("div", { class: "hint" }, "Include"), criteria(d.criteria.include)),
      el("div", {}, el("div", { class: "hint" }, "Exclude"), criteria(d.criteria.exclude))),
    el("div", { class: "table-wrap" }, el("table", { class: "disagreements" },
      el("thead", {}, el("tr", {}, el("th", {}, "Record"), names.map((n) => el("th", {}, n)), el("th", {}, "Your decision"))),
      el("tbody", {}, rows))),
    el("div", { class: "toolbar" }, submit, status),
  ];
}

// ---------------------------------------------------------------------------------------------------------------
// synthesis, argument and design: claims with their badges and evidence, the cards that decide on them

const CRITERIA = ["accurate", "precise", "sufficient", "representative", "authoritative", "clear", "relevant"];
const COMPARABILITY = ["population", "intervention", "comparator", "outcome", "design"];
// An object's entries with the `order` keys first, in that order (stored JSON comes back with sorted keys).
const inOrder = (obj, order) => Object.entries(obj).sort(([a], [b]) => {
  const i = order.indexOf(a), j = order.indexOf(b);
  return (i < 0 ? order.length : i) - (j < 0 ? order.length : j) || a.localeCompare(b);
});
const CLAIM_STATUS = { supported: "ok", uncertain: "warn", draft: "neutral", unsupported: "bad", contradicted: "bad", withdrawn: "neutral" };
const TESTS = { true: "True", limited: "Limited as it should be", circumstance_fits: "Circumstance fits", consequence_fits: "Consequence fits", accepted: "Accepted in the field" };
const ROLE_NAMES = { main: "Main claim", reason: "Reason", response: "Response", warrant: "Warrant", synthesis: "Synthesis finding" };
const artifactUrl = (id) => `/ui/api/artifacts/${encodeURIComponent(state.project)}/${encodeURIComponent(id)}`;

function claimBadges(c) {
  const h = c.hedge || {};
  return el("div", { class: "badges" },
    el("span", { class: `badge ${CLAIM_STATUS[c.status] || "neutral"}` }, label(c.status)),
    c.role ? el("span", { class: "badge neutral" }, ROLE_NAMES[c.role] || label(c.role)) : null,
    c.contested ? el("span", { class: "badge warn" }, "Contested") : null,
    c.status === "withdrawn" ? null : h.verdict
      ? el("span", { class: `badge ${h.verdict === "pass" ? "ok" : "bad"}` }, h.verdict === "pass" ? `Hedge ok (level ${h.level})` : `Overstated (level ${h.level})`)
      : el("span", { class: "badge neutral" }, "Hedge not checked"),
    c.v_stats ? el("span", { class: `badge ${c.v_stats === "pass" ? "ok" : "bad"}` }, `V-STATS ${c.v_stats}`) : null);
}

function evidenceList(c) {
  if (!c.evidence.length) return el("p", { class: "empty" }, c.kind === "stat_result" ? "Computed by the kernel from the extraction (its artifacts)." : "No evidence linked.");
  return c.evidence.map((e) => el("div", { class: "evidence" },
    e.notes.map((n) => n.missing ? el("p", { class: "hint bad" }, `Note ${n.id} is missing.`)
      : el("blockquote", { class: `note${n.verified ? "" : " unverified"}` }, n.text,
        el("div", { class: "hint" }, n.label || n.title || n.item_id, AGAINST[n.against] ? ` (${AGAINST[n.against]})` : ""))),
    e.interpretation ? el("p", { class: "hint" }, "How it supports the claim: ", e.interpretation) : null,
    Object.keys(e.criteria).length ? el("p", { class: "hint" }, CRITERIA.filter((k) => e.criteria[k]).map((k) => `${label(k)} ${e.criteria[k].score}/2`).join(" · ")) : null));
}

// A claim: its badges, text and scope; why it was withdrawn, is contested or fails the claim gate (unless `problems`
// is false: a review card lists them itself); its evidence (folded unless `open`).
function claimBlock(c, { open = false, problems = true } = {}) {
  return el("div", { class: `claim${c.status === "withdrawn" ? " withdrawn" : ""}`, "data-claim": c.id },
    claimBadges(c),
    el("p", { class: "claim-text" }, c.text),
    c.scope_limits.length ? el("p", { class: "hint" }, "Scope: ", c.scope_limits.join("; ")) : null,
    c.withdrawn_reason ? el("p", { class: "hint" }, "Withdrawn: ", c.withdrawn_reason) : null,
    c.contested_reason ? el("p", { class: "hint" }, "Contested: ", c.contested_reason) : null,
    c.narrowed_from ? el("p", { class: "hint" }, `Narrower rewrite of ${c.narrowed_from}.`) : null,
    problems && c.status !== "withdrawn" && c.problems.length ? el("ul", { class: "check bad" }, c.problems.map((p) => el("li", {}, p))) : null,
    el("details", { class: "claim-evidence", open, "data-key": `evidence-${c.id}` }, el("summary", {}, `Evidence (${c.evidence.length})`), evidenceList(c)));
}

// Two radio buttons (or more) for one decision; `onpick(value)` on change.
function choices(group, name, options, onpick) {
  return el("div", { role: "radiogroup", class: "choices", "aria-label": name },
    options.map(([value, text, aria]) => el("label", { class: "choice" },
      el("input", { type: "radio", name: group, value, "aria-label": aria, onchange: () => onpick(value) }), text)));
}

function optionalComment(label = "Comment (optional)") {
  const box = el("textarea", { name: "comment" });
  return { field: el("label", { class: "field" }, label, box), value: () => box.value.trim() };
}

const groupName = (g) => `${g.outcome} (${g.measure})`;
const studyNames = (list) => list.map((s) => s.label).join(", ");

// "Back to read_extract" on synthesize's pooling_approval and uncertain_support: the studies to read again (none
// ticked: those missing a value the pooling needs, else every study; the others keep their extraction) and an
// optional comment, the guidance read_extract gets. Offered when the checkpoint lists `back` among its decisions.
function backToReadForm(o, answer) {
  const studies = o.studies || [];
  const fields = o.fields || [];
  const picked = new Set();
  const look = new Map();  // study id -> the fields to look for in it (optional)
  const why = el("textarea", { name: "back_comment" });
  const study = (s) => el("div", { class: "back-study" },
    el("label", { class: "choice" },
      el("input", { type: "checkbox", value: s.item_id, "aria-label": `Read again: ${s.label}`,
        onchange: (e) => (e.target.checked ? picked.add(s.item_id) : picked.delete(s.item_id)) }), s.label),
    fields.length ? el("details", { class: "back-fields" }, el("summary", {}, "Fields to look for (optional)"),
      el("div", { class: "choices" }, fields.map((f) => el("label", { class: "choice" },
        el("input", { type: "checkbox", value: f, "aria-label": `Look for ${f} in ${s.label}`, onchange: (e) => {
          const set = look.get(s.item_id) || new Set();
          if (e.target.checked) set.add(f); else set.delete(f);
          look.set(s.item_id, set);
        } }), f)))) : null);
  const form = el("form", { class: "revise back-read" },
    studies.length ? el("fieldset", { class: "back-studies" },
      el("legend", {}, "Studies to read again (none ticked: those missing a value the pooling needs, else every study)"),
      studies.map(study)) : null,
    el("label", { class: "field" }, "Why go back (optional; what to look for, e.g. a table)", why),
    el("button", { type: "submit", class: "secondary" }, "Back to read_extract"));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const comment = why.value.trim();
    const named = studies.map((s) => s.item_id).filter((id) => picked.has(id));
    const per = Object.fromEntries(named.filter((id) => look.get(id)?.size).map((id) => [id, fields.filter((f) => look.get(id).has(f))]));
    answer({ decision: "back", ...(named.length ? { studies: named } : {}), ...(Object.keys(per).length ? { fields: per } : {}),
      ...(comment ? { comment } : {}) });
  });
  return el("div", { class: "other-answers" }, el("h4", {}, "Or read studies again"), form);
}
const offersBack = (o) => Array.isArray(o.decisions) && o.decisions.includes("back");

// argue's final review: the Arguer's synthesis requests (as text; it argued on the findings it had) and a "Back to
// synthesize" form with an optional comment, the guidance synthesize gets with the requests. Offered when the
// checkpoint lists `back` among its decisions (only when the Arguer asked for something).
function synthesisRequests(o) {
  return [el("h4", {}, "The Arguer asked synthesize for findings it lacked"),
    el("p", { class: "hint" }, "It argued on the findings it has; nothing goes back to synthesize unless you say so. A request you do not send back is listed in the limitations."),
    el("ul", { class: "synthesis-requests" }, o.synthesis_requests.map((q) => el("li", {}, el("span", { class: "hint" }, `round ${q.round}: `), q.text)))];
}
// The supported synthesis findings a new synthesize attempt keeps word for word (`revisable`, id -> text): the
// ticked ones go in "revise" and are written again, as your comment says.
function keptPicker(revisable) {
  const picked = new Set();
  const ids = Object.keys(revisable || {});
  const field = ids.length ? el("fieldset", { class: "back-studies kept-findings" },
    el("legend", {}, "Findings to write again (the others are kept word for word)"),
    ids.map((id) => el("label", { class: "choice" },
      el("input", { type: "checkbox", value: id, "aria-label": `Write again: ${revisable[id]}`,
        onchange: (e) => (e.target.checked ? picked.add(id) : picked.delete(id)) }), revisable[id]))) : null;
  return { field, value: () => (picked.size ? { revise: [...picked] } : {}) };
}

// `spent`: set when argue->synthesize has no use left ("used n of limit: back asks to allow one more use").
function backToSynthesizeForm(answer, spent, revisable) {
  const why = el("textarea", { name: "back_comment" });
  const kept = keptPicker(revisable);
  const form = el("form", { class: "revise back-synthesize" },
    spent ? el("p", { class: "hint warn back-spent" }, spent) : null,
    kept.field,
    el("label", { class: "field" }, "Why go back to synthesize (optional; needed when you tick findings: how to write them again)", why),
    el("button", { type: "submit", class: "secondary" }, "Back to synthesize"));
  form.addEventListener("input", () => why.setCustomValidity(""));  // typing, or ticking and unticking a finding
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const comment = why.value.trim();
    const revise = kept.value();
    if (revise.revise && !comment) {  // the kernel refuses findings to write again without your comment on how
      why.setCustomValidity("Say how to write the ticked findings again.");
      why.reportValidity();
      return;
    }
    answer({ decision: "back", to: "synthesize", ...(comment ? { comment } : {}), ...revise });
  });
  return el("div", { class: "other-answers" }, el("h4", {}, "Or go back to synthesize for what the Arguer asked"), form);
}

async function poolingCard(c, answer) {
  const o = c.options || {};
  const d = await api("/ui/api/synthesis", { query: { project: state.project, attempt: o.attempt } });
  const listed = (o.groups || []).map((i) => d.groups.find((g) => g.group === i)).filter(Boolean);
  const chosen = new Map();
  const comment = optionalComment();
  const status = el("p", { class: "hint", role: "status" });
  const submit = el("button", { type: "button", disabled: true, onclick: () => {
    const text = comment.value();
    answer({ decisions: Object.fromEntries(chosen), ...(text ? { comment: text } : {}) });
  } }, "Submit pooling decisions");
  const update = () => {
    status.textContent = `${chosen.size} of ${count(listed.length, "group")} decided`;
    submit.disabled = chosen.size !== (o.groups || []).length;  // every group of the checkpoint needs your decision
  };
  update();
  const p = d.pool_options || {};
  const group = (g) => el("li", { class: "pool-group", "data-group": g.group },
    el("h3", {}, `Group ${g.group}: ${groupName(g)}`),
    facts([["Studies", `${g.studies.length}: ${studyNames(g.studies)}`],
      ["Added by the kernel", g.added_by_kernel.length ? `${studyNames(g.added_by_kernel)} (the plan left them out without a reason; their effects are computable, so they are pooled too)` : ""],
      ["The Arguer's reason", g.reason]]),
    g.left_out.length ? [el("h4", {}, "Left out by the plan (recorded as a deviation)"),
      el("ul", {}, g.left_out.map((x) => el("li", {}, `${x.label}: `, x.reason)))] : null,
    el("h4", {}, "Comparability"),
    el("table", { class: "comparability" }, el("thead", {}, el("tr", {}, ["Criterion", "Score", "Reason"].map((h) => el("th", {}, h)))),
      el("tbody", {}, inOrder(g.comparability, COMPARABILITY).map(([k, v]) => el("tr", {}, el("td", {}, label(k)), el("td", {}, `${v.score}/2`), el("td", {}, v.reason))))),
    el("div", { class: "decide" }, el("div", { class: "who" }, "Your decision"),
      choices(`pool-${c.id}-${g.group}`, `Your decision: ${groupName(g)}`,
        [["pool", "Pool", `Pool: ${groupName(g)}`], ["no_pool", "Do not pool", `Do not pool: ${groupName(g)}`]],
        (v) => { chosen.set(String(g.group), v); update(); })));
  return [
    el("p", { class: "hint" }, "The kernel computes every number from the extraction rows. A group you do not pool is synthesized narratively, recorded as a deviation from the protocol."),
    el("h4", {}, "Pool options the protocol pre-specifies"),
    facts([[POOL_LABELS.pool_model, POOL_MODELS[p.model] || p.model], [POOL_LABELS.hksj, HKSJ[String(p.hksj)] ?? String(p.hksj)],
      [POOL_LABELS.pi_df, PI_DF[p.pi_df] || p.pi_df], ["Interval level", p.level ? `${Math.round(p.level * 100)}%` : ""],
      [POOL_LABELS.min_studies, p.min_studies ? minStudies(p.min_studies) : ""]]),
    listed.length ? el("ol", { class: "pool-groups" }, listed.map(group)) : el("p", { class: "empty" }, "The groups of this checkpoint were not found."),
    comment.field,
    el("div", { class: "toolbar" }, submit, status),
    offersBack(o) ? backToReadForm(o, answer) : null,
  ];
}

// uncertain_support of synthesize (its claims that fail the gate) and of argue's final review (the claims of the
// argument and the findings it rests on that fail its gate, and whether the main claim answers the research question,
// with the Arguer's failed self-check shown as an input of that answer). Each claim offers the answers the checkpoint
// allows it (`claim_choices`: withdraw always, supported only when its only problem is its V-SUPPORT status). An older
// uncertain_support without listed claims gets the plain card.
const REVIEW_CHOICES = { supported: "Supported", withdraw: "Withdraw" };
async function uncertainCard(c, answer) {
  const o = c.options;
  const rqAsked = Array.isArray(o.answers_rq);
  const d = await api(rqAsked ? "/ui/api/argument" : "/ui/api/synthesis", { query: { project: state.project, attempt: o.attempt } });
  const byId = new Map((rqAsked ? d.claims : d.findings).map((x) => [x.id, x]));
  const chosen = new Map();
  let rq = null;
  const comment = optionalComment();
  const status = el("p", { class: "hint", role: "status" });
  const submit = el("button", { type: "button", disabled: true, onclick: () => {
    const text = comment.value();
    answer({ claims: Object.fromEntries(chosen), ...(rqAsked ? { answers_rq: rq } : {}), ...(text ? { comment: text } : {}) });
  } }, "Submit your review");
  const update = () => {
    status.textContent = `${chosen.size} of ${count(o.claim_ids.length, "claim")} decided${rqAsked ? (rq ? "; research question answered" : "; the research question is still open") : ""}`;
    submit.disabled = chosen.size !== o.claim_ids.length || (rqAsked && !rq);
  };
  update();
  const allowed = (cid) => (o.claim_choices && o.claim_choices[cid]) || o.choices || ["supported", "withdraw"];
  const row = (cid) => {
    const x = byId.get(cid);
    if (!x) return el("li", {}, el("p", { class: "error" }, `Claim ${cid} was not found.`));
    const why = (o.problems && o.problems[cid]) || x.problems;
    const only = allowed(cid);
    return el("li", { class: "review-claim", "data-claim-review": cid }, claimBlock(x, { open: true, problems: false }),
      why.length ? [el("div", { class: "who" }, "Why it is listed"), el("ul", { class: "check bad" }, why.map((p) => el("li", {}, p)))] : null,
      x.support_reason ? el("p", { class: "hint" }, "The judges: ", x.support_reason) : null,
      rqAsked ? objectionList(d.objections.filter((ob) => [cid, ...x.evidence.map((e) => e.id)].includes(ob.target_id))) : null,
      only.length === 1 && only[0] === "withdraw" ? el("p", { class: "hint" }, "Only withdrawing it mends the gate: its problem is not one the judges decide.") : null,
      choices(`claim-${c.id}-${cid}`, `Your decision: ${x.text}`,
        only.map((v) => [v, REVIEW_CHOICES[v] || label(v), `${REVIEW_CHOICES[v] || label(v)}: ${x.text}`]),
        (v) => { chosen.set(cid, v); update(); }));
  };
  const main = rqAsked ? byId.get(o.main_claim_id) : null;
  const kept = keptPicker(o.revisable);  // synthesize's rerun: the findings to write again
  return [
    rqAsked ? [el("h4", {}, "Research question"), el("p", { class: "rq-sentence" }, d.question || ""),
      el("h4", {}, "Main claim"), main ? claimBlock(main) : el("p", { class: "error" }, "The main claim was not found."),
      (o.main_problems || []).length ? el("p", { class: "hint warn" }, "No answer here mends the main claim's own problems above: say whether it answers the research question, and argue again with your comment so the Arguer revises it.") : null,
      o.arguer_self_check && o.arguer_self_check.ok === false ? el("p", { class: "hint warn self-check" }, "The Arguer's own check says the main claim does not answer the research question: ", o.arguer_self_check.reason || "no reason given") : null,
      (o.synthesis_requests || []).length ? synthesisRequests(o) : null,
      el("h4", {}, "Does the main claim answer the research question?"),
      choices(`rq-${c.id}`, "Does the main claim answer the research question?",
        [["yes", "Yes, it answers the research question", "Yes, it answers the research question"],
          ["no", "No: back to the question stage", "No: back to the question stage"]], (v) => { rq = v; update(); })] : null,
    o.claim_ids.length ? [el("h4", {}, rqAsked ? "Claims that fail the argument's gate" : "Synthesis claims that fail the gate"),
      el("p", { class: "hint" }, "Withdraw each, or call it supported when its only problem is that the judges did not find it supported. Withdrawing a claim takes it out and lists it in the limitations."),
      o.no_supported_left ? el("p", { class: "hint warn" }, "No other finding is supported: withdrawing every listed claim leaves the synthesis without one, and its gate fails. You may synthesize again, or read studies again, instead.") : null,
      el("ol", { class: "review-claims" }, o.claim_ids.map(row))] : null,
    o.narrative_missing ? el("p", { class: "hint warn" }, `The Arguer gave no acceptable narrative synthesis (${o.narrative_missing}), so there is nothing to review: synthesize again with your comment, or read studies again.`) : null,
    o.narrative_missing ? null : comment.field,
    o.narrative_missing ? null : el("div", { class: "toolbar" }, submit, status),
    el("div", { class: "other-answers" }, el("h4", {}, `Or ${rqAsked ? "argue" : "synthesize"} again`),
      reviseForm(`What should change (${rqAsked ? "argue" : "synthesize"} runs again with your comment)`,
        (text) => answer({ decision: "rerun", comment: text, ...(rqAsked ? {} : kept.value()) }),
        rqAsked ? null : kept.field)),
    !rqAsked && offersBack(o) ? backToReadForm(o, answer) : null,
    rqAsked && offersBack(o) && o.back_to === "synthesize" ? backToSynthesizeForm(answer, o.back_spent, o.revisable) : null,
  ];
}

async function conflictCard(c, answer) {
  const o = c.options || {};
  const d = await api("/ui/api/argument", { query: { project: state.project, attempt: o.attempt } });
  const byId = new Map(d.claims.map((x) => [x.id, x]));
  const conflict = d.conflicts.find((x) => x.index === o.index) || {};
  const comment = optionalComment();
  const decide = (decision) => { const text = comment.value(); answer({ decision, ...(text ? { comment: text } : {}) }); };
  const side = (name, id) => el("div", { class: "finding" }, el("h3", {}, `Claim ${name}`),
    byId.has(id) ? claimBlock(byId.get(id), { open: true }) : el("p", { class: "error" }, `Claim ${id} was not found.`));
  return [
    el("div", { class: "conflict-pair" }, side("a", o.claim_a), side("b", o.claim_b)),
    el("h4", {}, "Why they contradict each other (the Skeptic)"), el("p", {}, conflict.why || ""),
    el("p", { class: "hint" }, "If claim a holds, claim b is withdrawn from the argument (and the other way round). Both contested keeps both, marked contested, each with an objection the Arguer answers."),
    comment.field,
    el("div", { class: "toolbar" },
      el("button", { type: "button", onclick: () => decide("a_holds") }, "Claim a holds"),
      el("button", { type: "button", onclick: () => decide("b_holds") }, "Claim b holds"),
      el("button", { type: "button", class: "secondary", onclick: () => decide("both_contested") }, "Both stand, contested")),
  ];
}

// The design of a research proposal: its hypotheses with their falsification links, items with their measurements,
// expected results and alternatives, the dropped hypotheses and the feasibility claims.
function designDetails(d) {
  const b = d.design;
  const measure = new Map(b.items.flatMap((i) => i.measurements.map((m) => [m.id, m])));
  const mName = (id) => (measure.has(id) ? `${id} (${measure.get(id).what})` : id);
  // H1, H2, … by position, known by the design's key (an RQ hypothesis id, or a new one's ref) and by its id
  const num = new Map();
  d.hypotheses.forEach((h, i) => { num.set(h.key, `H${i + 1}`); if (h.id) num.set(h.id, `H${i + 1}`); });
  const hName = (id) => num.get(id) || id;
  return [
    el("h4", {}, "Hypotheses"),
    el("ol", { class: "hypotheses" }, d.hypotheses.map((h) => el("li", {},
      el("div", { class: "badges" }, el("span", { class: "badge neutral" }, hName(h.key)),
        h.new ? el("span", { class: "badge warn" }, d.frozen_hash ? "New hypothesis" : "New hypothesis (added when you approve)") : null,
        h.status ? el("span", { class: "badge neutral" }, words(h.status)) : null),
      el("p", {}, h.statement),
      facts([["Falsified if", h.falsification], ["Rival of", h.rival_of ? hName(h.rival_of) : ""], ["Tested by", h.tested_by.join(", ") || "no design item"],
        ["Falsified by", h.falsified_by.map(mName).join("; ") || "no measurement"], ["Hypothesis id", h.id || ""]])))),
    el("h4", {}, "Design items"),
    el("ol", { class: "design-items" }, b.items.map((i) => el("li", {},
      el("strong", {}, `${i.id} (${i.kind})`),
      facts([["Tests", i.hypothesis_ids.map(hName).join(", ")], ["Sample", i.sample], ["Controls", i.controls.join(", ") || "none"], ["Analysis", i.analysis], ["Data source", i.data_source]]),
      el("ul", { class: "measurements" }, i.measurements.map((m) => el("li", {}, `${m.id}: ${m.what}, by ${m.instrument}`, m.unit ? ` (${m.unit})` : "")))))),
    el("h4", {}, "Expected results"), el("p", {}, b.expected_results),
    el("h4", {}, "Alternative explanations"),
    b.alternative_explanations.length ? el("ul", {}, b.alternative_explanations.map((x) => el("li", {}, x))) : el("p", { class: "empty" }, "None given."),
    d.dropped.length ? [el("h4", {}, "Dropped hypotheses (not tested)"), el("ul", {}, d.dropped.map((x) => el("li", {}, `${x.id}: `, x.statement || "", el("div", { class: "hint" }, "Reason: ", x.reason))))] : null,
    el("h4", {}, "Feasibility claims"),
    d.feasibility_claims.length ? el("ul", { class: "claims" }, d.feasibility_claims.map((x) => el("li", {}, claimBlock(x)))) : el("p", { class: "empty" }, "None."),
  ];
}

// design_approval: approve (deciding every feasibility claim the judges could not agree on), revise or back to argue.
// After the Ideator said the research question is untestable (`untestable`: its reason), the card shows that reason
// and offers what the checkpoint's `choices` allow: back to the research question, a revision and, only with a draft
// that passes its structure, the approval.
async function designCard(c, answer) {
  const o = c.options || {};
  const allowed = o.choices || ["approve", "revise", "back"];
  const d = await api("/ui/api/design", { query: { project: state.project, attempt: o.attempt } });
  if (!d.design && !o.untestable) return el("p", { class: "error" }, `The design ${o.draft_id} was not found.`);
  const listed = o.claim_ids || [];
  const byId = new Map(d.feasibility_claims.map((x) => [x.id, x]));
  const chosen = new Map();
  const approve = el("button", { type: "button", disabled: listed.length > 0, onclick: () => answer({ decision: "approve", ...(listed.length ? { claims: Object.fromEntries(chosen) } : {}) }) }, "Approve this design");
  const status = el("p", { class: "hint", role: "status" });
  const update = () => {
    status.textContent = listed.length ? `${chosen.size} of ${count(listed.length, "feasibility claim")} decided` : "";
    approve.disabled = chosen.size !== listed.length;
  };
  update();
  const goBack = (label, button, decision) => {
    const why = el("textarea", { name: `${decision}_comment` });
    const form = el("form", { class: "revise" }, el("label", { class: "field" }, label, why),
      el("button", { type: "submit", class: "secondary" }, button));
    form.addEventListener("submit", (e) => { e.preventDefault(); const comment = why.value.trim(); answer(comment ? { decision, comment } : { decision }); });
    return form;
  };
  const canApprove = allowed.includes("approve") && d.design;
  return [
    o.untestable ? [el("h4", {}, "The Ideator says the research question cannot be tested"),
      el("p", { class: "hint warn" }, o.untestable),
      el("p", { class: "hint" }, canApprove ? "It gave a design all the same. Send the project back to the research question, ask for a revision, or approve the design."
        : "Send the project back to the research question, or ask for a revision.")] : null,
    d.design ? [el("p", { class: "hint" }, `Design ${d.draft_id}.${canApprove ? " Approving freezes it and pre-registers the hypotheses it tests." : ""}`), designDetails(d)] : null,
    canApprove && listed.length ? [el("h4", {}, "Feasibility claims the judges could not agree on"),
      el("ol", {}, listed.map((cid) => {
        const x = byId.get(cid);
        return el("li", {}, x ? x.text : cid, choices(`feasible-${c.id}-${cid}`, `Your decision: ${x ? x.text : cid}`,
          [["supported", "Supported", `Supported: ${x ? x.text : cid}`], ["withdraw", "Withdraw", `Withdraw: ${x ? x.text : cid}`]],
          (v) => { chosen.set(cid, v); update(); }));
      }))] : null,
    el("div", { class: "other-answers" },
      canApprove ? el("div", { class: "toolbar" }, approve, status) : null,
      allowed.includes("revise") ? reviseForm("What should change in the design", (comment) => answer({ decision: "revise", comment })) : null,
      allowed.includes("question") ? goBack("Why go back to the question (optional)", "Back to the research question", "question") : null,
      allowed.includes("back") ? goBack("Why go back (optional)", "Back to argue", "back") : null),
  ];
}

// argue_request (draft, P2): the Writer says a section needs claims the argument lacks. Send the stage back to argue
// for them (once per section; the comment goes with the request) or have the section written from the claims given,
// the gap stated as a limitation (the comment goes to the Writer). Both comments are optional.
function argueRequestCard(c, answer) {
  const o = c.options || {};
  const form = (decision, label, button) => {
    const why = el("textarea", { name: `${decision}_comment` });
    const f = el("form", { class: "revise" }, el("label", { class: "field" }, label, why),
      el("button", decision === "back" ? { type: "submit", class: "secondary" } : { type: "submit" }, button));
    f.addEventListener("submit", (e) => { e.preventDefault(); const comment = why.value.trim(); answer(comment ? { decision, comment } : { decision }); });
    return f;
  };
  return [
    el("p", {}, `The Writer says the section ${o.title || o.section} needs claims the argument does not have:`),
    el("ul", {}, (o.needs || []).map((n) => el("li", {}, n))),
    el("p", { class: "hint" }, "Argue is asked once per section; after that the section is written from the claims it has."),
    el("div", { class: "other-answers" },
      form("write", "What the Writer should keep in mind (optional)", "Write it, the gap as a limitation"),
      form("back", "What argue should look for (optional)", "Back to argue")),
  ];
}

// The audit report: whether it passed, the sections it audited, every hard gate with its failures (as text) and the
// warnings. Used by the final_review card and the Audit page.
function gatesList(rep) {
  return el("ul", { class: "gates" }, rep.gates.map((g) => el("li", { "data-gate": g.gate },
    el("span", { class: `badge ${g.failures.length ? "bad" : "ok"}` }, g.failures.length ? count(g.failures.length, "failure") : "passes"), " ",
    el("strong", {}, g.gate),
    g.failures.length ? el("ul", { class: "failures" }, g.failures.map((f) => el("li", {}, f))) : null)));
}

function auditSummary(rep) {
  return [
    el("p", { class: rep.ok ? "check ok" : "check bad" }, `Audit report v${rep.version} (attempt ${rep.attempt}): `,
      rep.ok ? "the manuscript passed every hard gate." : "the manuscript failed the audit."),
    facts([["Sections", Object.entries(rep.sections).map(([s, v]) => `${s} v${v}`).join(", ")],
      ["Final review", rep.approved ? "approved" : "not approved yet"]]),
    gatesList(rep),
    rep.warnings.length ? [el("h4", {}, "Warnings"), el("ul", { class: "warnings" }, rep.warnings.map((w) => el("li", {}, w)))] : null,
  ];
}

// final_review (the audit's, P2): approve for export, ask for a revision (what to change) or send the manuscript back
// to a stage (where to, and optionally why). The server checks the answer with the audit's own check
// (final_review_problem) and refuses one it could not apply; the card only gates its buttons. A report that cannot be
// shown (asked for only when it is listed, so no 404) leaves revise and back; approve stays disabled.
async function finalReviewCard(c, answer) {
  const o = c.options || {};
  let rep = null, missing = "there is no such report";
  try {
    const d = await api("/ui/api/audit", { query: { project: state.project } });
    if (d.report?.version === o.report_version) rep = d.report;
    else if (d.reports.some((r) => r.version === o.report_version))
      rep = (await api("/ui/api/audit", { query: { project: state.project, version: o.report_version } })).report;
  } catch (e) {
    if (e.message === "signed out") throw e;
    missing = e.message;
  }
  let to = null;
  const send = el("button", { type: "submit", class: "secondary", disabled: true }, "Send it back");
  const why = el("textarea", { name: "back_comment" });
  const back = el("form", { class: "revise back-review" },
    el("div", { class: "field" }, el("span", { class: "hint" }, "Where it goes back to"),
      choices(`back-${c.id}`, "Where the manuscript goes back to", (o.back_to || []).map((s) => [s, `Back to ${words(s)}`, `Back to ${words(s)}`]),
        (v) => { to = v; send.disabled = false; })),
    el("label", { class: "field" }, "Why send it back (optional)", why), send);
  back.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!to) return;
    const comment = why.value.trim();
    answer(comment ? { decision: "back", to, comment } : { decision: "back", to });
  });
  return [
    rep ? auditSummary(rep) : el("p", { class: "error" }, `The audit report v${o.report_version} cannot be shown: ${missing}.`),
    el("p", { class: "hint" }, "Read the manuscript on the ", el("a", { href: "#/drafts" }, "Drafts"), " page and the reports on the ",
      el("a", { href: "#/audit" }, "Audit"), " page."),
    el("div", { class: "other-answers" },
      el("div", { class: "toolbar" }, el("button", { type: "button", disabled: !(rep && rep.ok), onclick: () => answer({ decision: "approve" }) }, "Approve for export")),
      reviseForm("What should change in the manuscript (number separate points (1), (2), … : each is asked and answered on its own)", (comment) => answer({ decision: "revise", comment })),
      back),
  ];
}

const CARDS = { topic_review: topicReviewCard, rq_approval: rqCard, significance_novelty: rqCard, stage_blocked: blockedCard,
  protocol_approval: protocolCard, screening_disagreements: disagreementCard, pooling_approval: poolingCard,
  uncertain_support: uncertainCard, evidence_conflict: conflictCard, design_approval: designCard, final_review: finalReviewCard,
  argue_request: argueRequestCard };
// The card of a checkpoint, else null (the plain card): an uncertain_support without listed claims predates them.
const cardFor = (c) => (c.kind === "uncertain_support" && !Array.isArray(c.options?.claim_ids) ? null : CARDS[c.kind] || null);
const KINDS = { topic_review: "Topic review", rq_approval: "Research question approval",
  significance_novelty: "Already answered? The judges split", stage_blocked: "Stage blocked at its back-edge limit",
  protocol_approval: "Protocol approval", screening_disagreements: "Screening disagreements",
  pooling_approval: "Pooling approval", uncertain_support: "Supported? Your review",
  evidence_conflict: "Contradicting claims", design_approval: "Design approval", final_review: "Final review",
  argue_request: "A section needs claims the argument lacks" };

const DECIDED_BY = { agreement: "both screeners", single: "one screener", human: "you", vote: "the P1 vote (not reviewed by a human)", rule: "the language rule" };
const PHASE_NAMES = { title_abstract: "Title and abstract", full_text: "Full text" };

// The PRISMA flow (systematic and scoping reviews): each box with its count and what it holds, the full-text
// exclusions by reason, V-PRISMA and the notes. The other paper types: the selection reasons of every decision.
function prismaPanel(d) {
  if (d.error) return el("p", { class: "error" }, `Could not load the PRISMA flow: ${d.error}`);
  if (d.attempt === null || (!d.prisma && !d.selection.length))
    return el("p", { class: "empty" }, "Search and screening have not run yet.");
  const notes = d.notes.length ? [el("h3", {}, "Notes"), el("ul", { class: "prisma-notes" }, d.notes.map((n) => el("li", {}, n)))] : null;
  if (!d.prisma) {
    const phase = (ph) => {
      const rows = d.selection.filter((s) => s.phase === ph);
      if (!rows.length) return null;
      const inc = rows.filter((s) => s.include === "yes").length;
      return el("details", { class: "selection", open: ph === "full_text" },
        el("summary", {}, `${PHASE_NAMES[ph]}: ${inc} included, ${rows.length - inc} not included`),
        el("table", {}, el("thead", {}, el("tr", {}, ["Record", "Decision", "Decided by", "Selection reason"].map((h) => el("th", {}, h)))),
          el("tbody", {}, rows.map((s) => el("tr", {}, el("td", {}, s.title), el("td", {}, SAID[s.include] || s.include),
            el("td", {}, DECIDED_BY[s.decided_by] || s.decided_by),
            el("td", {},
              // a decision of the screeners rests on their reasons; one of you, the vote or a rule has its own
              ["single", "agreement"].includes(s.decided_by) ? null : el("div", { class: "final-reason" }, s.reason || "(no reason)"),
              s.screeners.map((x) => el("div", { class: "hint" }, `${screener(x.screener)}: `, x.reason || "(no reason)"))))))));
    };
    return [
      el("p", { class: "hint" }, `A ${words(d.paper_type)} reports the selection reasons, not PRISMA counts (search_screen attempt ${d.attempt}).`),
      d.selection_check.length ? el("ul", { class: "check bad" }, d.selection_check.map((f) => el("li", {}, f))) : el("p", { class: "check ok" }, "Every selection decision has its reason."),
      phase("title_abstract"), phase("full_text"), notes];
  }
  const p = d.prisma;
  const sub = (pairs) => pairs.filter(([n]) => n).map(([n, text]) => el("div", { class: "hint" }, `${n} ${text}`));
  const box = (label, n, ...more) => el("li", {}, el("span", { class: "box-label" }, label), el("span", { class: "box-count" }, String(n)), more);
  const reasons = Object.entries(p.excluded_ft);
  return [
    el("p", { class: "hint" }, `PRISMA flow of search_screen attempt ${d.attempt}. Each count is taken from the stored records and decisions.`),
    el("ol", { class: "prisma-flow" },
      box("Identified", p.identified, el("div", { class: "hint" }, "every hit of every source and query")),
      box("Duplicates removed", p.duplicates_removed),
      box("Screened on title and abstract", p.screened),
      box("Excluded at title/abstract", p.excluded_ta, sub([[p.excluded_language, "for language"],
        [p.excluded_disagreement.title_abstract, "by the P1 vote on a disagreement"], [p.unreasoned_ta, "without a reason"]])),
      box("Sought for full text", p.sought),
      box("Not retrieved", p.not_retrieved, el("div", { class: "hint" }, "no full text and no abstract")),
      box("Assessed at full text", p.assessed_ft, sub([[p.abstract_only, "on the abstract only"]])),
      box("Excluded at full text", reasons.reduce((a, [, n]) => a + n, 0),
        reasons.length ? el("ul", { class: "reasons" }, reasons.map(([why, n]) => el("li", {}, `${why || "no reason"}: ${n}`))) : null),
      box("Included", p.included)),
    d.check.length ? [el("p", { class: "check bad" }, "The counts do not add up (V-PRISMA):"), el("ul", { class: "check bad" }, d.check.map((f) => el("li", {}, f)))]
      : el("p", { class: "check ok" }, "The counts add up (V-PRISMA)."),
    notes,
  ];
}

// The PRISMA flow, or the error that kept it from loading (the panel shows it; the rest of Progress still draws).
const prismaOrError = () => api("/ui/api/prisma", { query: { project: state.project } }).catch((err) => {
  if (err.message === "signed out") throw err;
  return { error: err.message };
});

// The synthesis of synthesize: how the studies were assessed, each pooling group with its decision and forest plot
// (an <img> of the stored SVG, never inline), the evidence map, the findings, the narrative and the fallbacks.
function synthesisPanel(d) {
  if (d.error) return el("p", { class: "error" }, `Could not load the synthesis: ${d.error}`);
  if (d.attempt === null) return el("p", { class: "empty" }, "Synthesis has not run yet.");
  const arts = new Map(d.artifacts.map((a) => [a.id, a]));
  const findings = new Map(d.findings.map((f) => [f.id, f]));
  const vstats = (id) => {
    const a = arts.get(id);
    return a ? el("span", { class: `badge ${a.v_stats.verdict === "pass" ? "ok" : "bad"}` }, `V-STATS ${a.v_stats.verdict}`) : null;
  };
  const group = (g) => {
    const result = g.pooled ? "Pooled" : g.fallback ? `Not pooled: ${g.fallback}` : g.decision ? `Decided ${words(g.decision.decision)}` : "Awaiting your decision";
    const stat = g.pooled ? findings.get(g.pooled.claim_id) : null;
    return el("li", { class: "synthesis-group", "data-group": g.group },
      el("h3", {}, groupName(g)),
      facts([["Studies", studyNames(g.studies)],
        ["Decision", g.decision ? `${words(g.decision.decision)}, by ${DECIDED_BY[g.decision.by] || g.decision.by}${g.decision.reason ? `: ${g.decision.reason}` : ""}` : ""],
        ["Result", result],
        ["Not computable", g.pooled?.uncomputable.length ? g.pooled.uncomputable.map((u) => `${u.label} (${u.reason})`).join("; ") : ""],
        ["Left out", g.pooled?.excluded.length ? g.pooled.excluded.map((u) => `${u.label} (${u.reason})`).join("; ") : ""]]),
      g.pooled ? el("figure", { class: "forest" },
        el("img", { src: artifactUrl(g.pooled.fig), alt: `Forest plot: ${groupName(g)}, ${g.pooled.studies.length} studies`, loading: "lazy" }),
        el("figcaption", {}, stat ? stat.text : "", " ", vstats(g.pooled.fig))) : null);
  };
  const map = d.map ? [el("h3", {}, `Evidence map (${d.map.table.n} studies)`), vstats(d.map.stt),
    Object.entries(d.map.table.fields).map(([field, counts]) => el("table", { class: "evidence-map" },
      el("thead", {}, el("tr", {}, el("th", {}, label(field)), el("th", {}, "Studies"))),
      el("tbody", {}, Object.entries(counts).map(([cat, n]) => el("tr", {}, el("td", {}, cat), el("td", {}, String(n)))))))] : null;
  const n = d.narrative;
  return [
    el("p", { class: "hint" }, `Synthesis of synthesize attempt ${d.attempt}${d.method ? ` (${words(d.method)})` : ""}. Every number comes from kernel code; the findings are checked by V-SUPPORT and V-HEDGE, the statistics by V-STATS.`),
    d.gate && !d.gate.passed && d.gate.failures.length ? el("ul", { class: "check bad" }, d.gate.failures.map((f) => el("li", {}, f))) : null,
    d.groups.length ? [el("h3", {}, "Meta-analysis"), el("ol", { class: "synthesis-groups" }, d.groups.map(group))] : null,
    map,
    el("h3", {}, "Findings"),
    d.findings.length ? el("ul", { class: "claims" }, d.findings.map((f) => el("li", {}, claimBlock(f)))) : el("p", { class: "empty" }, "No finding yet."),
    n ? [el("h3", {}, "Representativeness"), el("p", {}, n.representativeness),
      n.heterogeneity_note ? [el("h3", {}, "Heterogeneity"), el("p", {}, n.heterogeneity_note)] : null,
      n.swim ? el("details", { class: "swim", "data-key": "swim" }, el("summary", {}, "SWiM reporting items"),
        facts(Object.entries(n.swim).map(([k, v]) => [label(k), v]))) : null] : null,
    el("h3", {}, "Each study's criteria"),
    d.assessments.length ? el("ul", { class: "assessments" }, d.assessments.map((a) => el("li", {},
      el("details", { "data-key": `assessment-${a.item_id}` },
        el("summary", {}, el("span", { class: "study" }, a.label), " ", el("span", { class: "hint" },
          CRITERIA.map((k) => `${label(k)} ${a.criteria[k]?.score ?? "–"}`).join(" · "), a.capped ? " (sufficiency capped: abstract only)" : "")),
        facts(CRITERIA.filter((k) => a.criteria[k]).map((k) => [label(k), `${a.criteria[k].score}/2: ${a.criteria[k].reason}`]))))))
      : el("p", { class: "empty" }, "No study assessed yet."),
    d.fallbacks.length ? [el("h3", {}, "Deviations from the protocol"), el("ul", { class: "prisma-notes" }, d.fallbacks.map((f) => el("li", {}, f.reason)))] : null,
  ];
}

const synthesisOrError = () => api("/ui/api/synthesis", { query: { project: state.project } }).catch((err) => {
  if (err.message === "signed out") throw err;
  return { error: err.message };
});

pages.checkpoints = async (root) => {
  if (needProject()) return;
  const [open, answered] = await Promise.all([
    api("/ui/api/checkpoints", { query: { project: state.project, status: "open" } }),
    api("/ui/api/checkpoints", { query: { project: state.project, status: "answered" } })]);
  setOpenCount(open.checkpoints.length);
  const card = async (c) => {
    const special = cardFor(c);
    const long = special && c.kind !== "stage_blocked";  // its card shows everything the prompt says, laid out
    const li = el("li", { "data-kind": c.kind, "data-checkpoint": c.id, class: c.status === "answered" ? "answered" : "" },
      el("div", { class: "kind" }, KINDS[c.kind] || words(c.kind)),
      long ? el("details", { class: "prompt" }, el("summary", {}, "The checkpoint's full text"), el("p", {}, c.prompt)) : el("p", { class: "prompt" }, c.prompt));
    if (c.kind === "captcha_wait" || c.kind === "login_wait")
      li.append(el("p", { class: "hint" }, "Handle it in the built-in browser window, then press Done. The Kernel never solves CAPTCHAs or sees your password."));
    if (c.status === "answered") {
      li.append(el("p", { class: "hint" }, `Answered: ${JSON.stringify(c.answer)}`));
      return li;
    }
    const answer = async (body) => {
      // a failed answer gives each button back the state it had (a gated Submit stays disabled)
      const had = [...li.querySelectorAll("button")].map((b) => [b, b.disabled]);
      had.forEach(([b]) => { b.disabled = true; });
      try {
        await api(`/ui/api/checkpoints/${c.id}/answer`, { method: "POST", body: { project: state.project, answer: body } });
        toast("Answer recorded");
        if (!unfinished(li)) return route();
        // you are still answering another card: keep the page (and what you chose there), mark this one answered
        li.classList.add("answered");
        li.querySelectorAll("input, textarea, select, button").forEach((x) => { x.disabled = true; });
        li.append(el("p", { class: "hint" }, "Answer recorded."));
        notice.hidden = false;
      } catch (err) {
        had.forEach(([b, disabled]) => { b.disabled = disabled; });
        toast(err.message, "error");
      }
    };
    if (special) {
      try { li.append(el("div", { class: "card-body" }, await special(c, answer))); }
      catch (err) { li.append(el("p", { class: "error" }, `Could not load what this checkpoint is about: ${err.message}`)); }
      return li;
    }
    const opts = Array.isArray(c.options) ? c.options : [];
    const buttons = el("div", { class: "toolbar" }, opts.map((o) => el("button", { type: "button", onclick: () => answer({ choice: o }) }, o === "done" ? "Done" : String(o))));
    const text = el("textarea", { "aria-label": "Your answer" });
    const form = el("form", {}, text, el("button", { type: "submit", class: "secondary" }, "Send this answer"));
    form.addEventListener("submit", (e) => { e.preventDefault(); if (text.value.trim()) answer({ text: text.value.trim() }); });
    li.append(buttons);
    if (c.kind !== "captcha_wait" && c.kind !== "login_wait") li.append(form);
    return li;
  };
  const [openCards, answeredCards] = await Promise.all([Promise.all(open.checkpoints.map(card)),
    Promise.all(answered.checkpoints.slice(-20).reverse().map(card))]);
  // A checkpoint that opened, or one shown open that was answered, redraws the page, unless you are answering one
  // (text written, a choice made, a record cap typed): then a notice offers it.
  const unfinished = (skip = null) => [...document.querySelectorAll("#page .checkpoints > li:not(.answered)")].some((x) => x !== skip && (
    [...x.querySelectorAll("textarea, input[type='number']")].some((t) => t.value.trim())
    || x.querySelector(".card-body input[type='checkbox']:checked") !== null
    || x.querySelector(".card-body input[type='radio']:checked") !== null));
  const shown = new Set(open.checkpoints.map((c) => c.id));
  const notice = el("p", { class: "notice", role: "status", hidden: true }, "Checkpoints changed. ",
    el("button", { type: "button", class: "secondary", onclick: () => route() }, "Show the latest"));
  let left = false, timer = null;
  root.onLeave(() => { left = true; clearTimeout(timer); });
  root.listen((msg) => {
    if (msg.type !== "checkpoint" || shown.has(msg.checkpoint.id) === (msg.checkpoint.status === "open")) return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (left) return;
      if (unfinished()) notice.hidden = false;
      else route();
    }, 300);
  });
  fill(root, el("h1", {}, "Checkpoints"), el("p", { class: "lead" }, "Questions only you can answer. Agents wait here; nothing is decided for you."), notice,
    openCards.length ? el("ul", { class: "checkpoints" }, openCards) : el("p", { class: "empty" }, "Nothing is waiting for you."),
    answeredCards.length ? [el("h2", {}, "Answered"), el("ul", { class: "checkpoints" }, answeredCards)] : null);
};

const STAGE_STATUS = { passed: "Passed", active: "Active", pending: "Pending", plan5: "Not yet available" };
const STAGE_STATE = { waiting: "waiting for you", running: "running", reopened: "to run again" };
const RUN_STATUS = { running: "Running", waiting_human: "Waiting for you", paused_waiting_human: "Waiting for your answer",
  paused: "Paused", paused_budget: "Paused (budget reached)", done: "Done", failed: "Failed", cancelled: "Cancelled" };

function stageChip(s) {
  let note = "";
  if (s.status === "active") note = STAGE_STATE[s.state] || "next";
  else if (s.status === "pending" && s.state === "reopened") note = "to run again";
  const gate = s.last_gate && !s.last_gate.ok ? s.last_gate : null;
  return el("li", { class: `stage-chip ${s.status}`, "data-status": s.status, "data-stage": s.stage },
    el("span", { class: "stage-name" }, words(s.stage)),
    el("span", { class: "stage-status" }, note ? `${STAGE_STATUS[s.status]}: ${note}` : STAGE_STATUS[s.status]),
    s.attempt ? el("span", { class: "hint" }, `attempt ${s.attempt}`) : null,
    (s.back_edges || []).map((e) => el("span", { class: "hint" },
      `${e.to === s.stage ? "reruns" : `back to ${words(e.to)}`}: ${e.count} of ${e.limit + e.allowed}`)),
    s.state === "waiting" ? el("a", { href: "#/checkpoints" }, "Answer it") : null,
    gate ? el("details", { class: "gate" }, el("summary", {}, `Gate failed (attempt ${gate.attempt})`),
      el("ul", {}, gate.failures.map((f) => el("li", {}, f)))) : null);
}

// The run's status line, and the controls that fit its state.
function runControls(g, act) {
  if (!g.intake) return { status: el("p", { class: "hint" }, "Record the intake first (rk project intake): it sets the paper type and how discovery runs."), controls: null };
  // the Lead drives a D1 project's discovery until the orchestrator takes over (question passed, or production began); a run that is active always gets its status and controls
  if (g.discovery_mode !== "d2" && !g.orchestrator_drives && !g.run_active)
    return { status: el("p", { class: "hint" }, "This project uses guided discovery (D1): the Lead drives the stages in ", el("a", { href: "#/chat" }, "Chat"), ". Ask it to run the next stage."), controls: null };
  const run = g.run;
  const status = run
    ? el("p", { class: "run-status" }, `Run ${run.id} (until ${run.until}): `, el("strong", {}, RUN_STATUS[run.status] || words(run.status)), run.reason ? ` — ${run.reason}` : "")
    : el("p", { class: "run-status" }, "No run yet.");
  if (!g.runs_available) return { status, controls: el("p", { class: "hint" }, "Runs are driven by rk serve's orchestrator, which this server does not have.") };
  const btn = (text, onclick, cls) => el("button", { type: "button", class: cls, onclick }, text);
  const buttons = [];
  if (!g.run_active) {
    // a failed or cancelled run cannot go on: Resume starts a new run with the same end stage, and the server says so
    if (run && ["failed", "cancelled"].includes(run.status))
      buttons.push(btn(`Resume: start a new run until ${run.until}`, () => act("resume")));
    // the stages a run could end at now (the server's until_options): topics, question or any later stage with a worker; after question passed only with a frozen RQ
    const kind = g.discovery_mode === "d2" ? "D2 run" : "run";
    for (const until of g.until_options || [])
      buttons.push(btn(`Start ${kind} until ${words(until)}`, () => act("start", until), buttons.length ? "secondary" : undefined));
    if (!buttons.length && g.orchestrator_drives)
      return { status, controls: el("p", { class: "hint" }, "No run can start now: the next stage is not available yet, or a run past question needs a frozen research question.") };
  } else if (g.requested === "cancelled") {
    status.append(" Cancelling after the current step…");
  } else {
    const paused = g.requested === "paused" || ["paused", "paused_budget"].includes(run.status);
    if (g.requested === "paused") status.append(" Pausing after the current step…");
    buttons.push(paused ? btn("Resume run", () => act("resume")) : btn("Pause run", () => act("pause")), btn("Cancel run", () => act("cancel"), "secondary"));
  }
  return { status, controls: buttons.length ? el("div", { class: "toolbar" }, buttons) : null };
}

// What identifies a control across redraws: the stage chip it sits in and its accessible name.
const focusKey = (node) => `${node.closest("[data-stage]")?.dataset.stage || ""}|${node.getAttribute("aria-label") || node.textContent}`;

// Focus the control in `box` that has the key `key`, else the box's first button.
function restoreFocus(box, key) {
  if (key === null || box.contains(document.activeElement)) return;
  const same = [...box.querySelectorAll("button, a, summary")].find((n) => focusKey(n) === key);
  (same || box.querySelector("button"))?.focus();
}

// Redraw `box` keeping what a keyboard user had: the focused control and the open disclosures (a disclosure is known
// by its data-key, else by the stage chip it sits in).
function redrawKeeping(box, redraw) {
  const had = box.contains(document.activeElement) ? focusKey(document.activeElement) : null;
  const keyOf = (d) => d.dataset.key || d.closest("[data-stage]")?.dataset.stage;
  const open = new Set([...box.querySelectorAll("details[open]")].map(keyOf));
  redraw();
  box.querySelectorAll("details").forEach((d) => { if (open.has(keyOf(d))) d.open = true; });
  restoreFocus(box, had);
}

pages.progress = async (root) => {
  if (needProject()) return;
  const [p, first, firstPrisma, firstSynthesis] = await Promise.all([api("/ui/api/progress", { query: { project: state.project } }),
    api("/ui/api/stages", { query: { project: state.project } }), prismaOrError(), synthesisOrError()]);
  const graphBox = el("div", {});
  const runStatus = el("div", { "aria-live": "polite" });  // only the status line is announced
  const runButtons = el("div", {});
  const prismaBox = el("div", { class: "prisma" });
  const synthesisBox = el("div", { class: "synthesis" });
  let asked = 0, timer = null, graphShown = null, runShown = null, prismaShown = null, synthesisShown = null;
  const showPrisma = (d) => {
    const now = JSON.stringify(d);
    if (now === prismaShown) return;
    prismaShown = now;
    redrawKeeping(prismaBox, () => fill(prismaBox, prismaPanel(d)));
  };
  const showSynthesis = (d) => {
    const now = JSON.stringify(d);
    if (now === synthesisShown) return;
    synthesisShown = now;
    redrawKeeping(synthesisBox, () => fill(synthesisBox, synthesisPanel(d)));
  };
  const refresh = async () => {
    const n = ++asked;
    try {
      const [g, d, s] = await Promise.all([api("/ui/api/stages", { query: { project: state.project } }),
        prismaOrError(), synthesisOrError()]);
      if (n === asked) { draw(g); showPrisma(d); showSynthesis(s); }  // a slower, older answer does not draw over a newer one
    } catch (err) { toast(err.message, "error"); }
  };
  const act = async (action, until) => {
    const had = runButtons.contains(document.activeElement) ? focusKey(document.activeElement) : null;
    runButtons.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      if (action === "start") await api("/ui/api/run", { method: "POST", body: { project: state.project, until } });
      else {
        const r = await api(`/ui/api/run/${action}`, { method: "POST", body: { project: state.project } });
        if (r && r.message) toast(r.message);
      }
    } catch (err) { toast(err.message, "error"); }
    await refresh();
    runButtons.querySelectorAll("button").forEach((b) => { b.disabled = false; });  // also when nothing changed
    restoreFocus(runButtons, had);
  };
  // Each part is redrawn only when what it shows changed, so live updates leave focus and open details alone.
  const draw = (g) => {
    const graphNow = JSON.stringify([g.paper_type, g.graph, g.back_edges, g.edge_limit]);
    if (graphNow !== graphShown) {
      graphShown = graphNow;
      const used = g.back_edges.filter((e) => e.count || e.allowed);
      redrawKeeping(graphBox, () => fill(graphBox,
        el("p", { class: "hint" }, g.paper_type ? `Paper type: ${words(g.paper_type)}. ` : "No paper type yet. ", "Stages marked “Not yet available” come in a later version."),
        el("ol", { class: "stages", "aria-label": "Stages" }, g.graph.map(stageChip)),
        el("p", { class: "hint" }, `Back-edges and reruns used (each up to ${g.edge_limit} times, plus any you allow): `,
          used.length ? used.map((e) => `${words(e.from)} ${e.from === e.to ? "reruns" : `→ ${words(e.to)}`} ${e.count} of ${e.limit + e.allowed}`).join(" · ") : "none")));
    }
    const runNow = JSON.stringify([g.intake, g.discovery_mode, g.runs_available, g.run, g.run_active, g.requested]);
    if (runNow !== runShown) {
      runShown = runNow;
      const { status, controls } = runControls(g, act);
      fill(runStatus, status);
      redrawKeeping(runButtons, () => fill(runButtons, controls));
    }
  };
  draw(first);
  showPrisma(firstPrisma);
  showSynthesis(firstSynthesis);
  const feed = el("ul", { class: "feed", "aria-live": "polite" });
  const row = (e) => el("li", {}, el("time", { datetime: e.ts }, e.ts.replace("T", " ").slice(0, 19)), `${e.type} `, el("span", { class: "hint" }, `${e.actor}${e.entity_id ? ` · ${e.entity_id}` : ""}`));
  feed.append(...p.events.map(row));
  const later = () => { clearTimeout(timer); timer = setTimeout(refresh, 200); };
  root.onLeave(() => clearTimeout(timer));
  root.listen((msg) => {
    if (msg.type === "event") {
      feed.prepend(row(msg.event));
      if (/^(stage|run|edge|screening|synthesis|artifact|claim)\./.test(msg.event.type)) later();
    } else if (msg.type === "run") later();
  });
  fill(root, el("h1", {}, "Progress"),
    el("p", { class: "lead" }, "Where the stages stand, and what the agents and you did in this project, newest first."),
    el("h2", {}, "Stages"), graphBox,
    el("h2", {}, "Run"), el("div", { class: "run-controls" }, runStatus, runButtons),
    el("h2", {}, "Search and screening"), prismaBox,
    el("h2", {}, "Synthesis"), synthesisBox,
    el("h2", {}, "Tasks"),
    p.tasks.length ? el("table", {}, el("thead", {}, el("tr", {}, ["Task", "Role", "Driver", "Status", "Tries", "Error"].map((h) => el("th", {}, h)))),
      el("tbody", {}, p.tasks.map((t) => el("tr", {}, el("td", {}, t.id), el("td", {}, t.role), el("td", {}, t.driver), el("td", {}, t.status), el("td", {}, t.attempts), el("td", {}, t.error || "")))))
      : el("p", { class: "empty" }, "No agent tasks yet."),
    el("h2", {}, "Open checkpoints"),
    p.open_checkpoints.length ? el("p", {}, el("a", { href: "#/checkpoints" }, `${p.open_checkpoints.length} waiting for you`)) : el("p", { class: "empty" }, "None."),
    el("h2", {}, "Activity"), feed);
};

const TEST_OK = (t) => el("span", { class: `badge ${t.ok ? "ok" : "bad"}` }, t.ok ? "holds" : "fails");

// A reason edge's warrant (a claim of origin warrant, shown under its edge) with the Skeptic's five tests.
function warrantBlock(w) {
  if (!w) return el("p", { class: "hint" }, "No warrant recorded for this link.");
  return el("div", { class: "warrant" },
    el("div", { class: "hint" }, `Warrant (${words(w.warrant?.type || "")})`, w.dropped ? ": its link was dropped from the argument" : ""),
    el("p", { class: "warrant-text" }, w.text),
    w.tests ? el("ul", { class: "warrant-tests" }, Object.entries(TESTS).filter(([k]) => w.tests[k]).map(([k, name]) =>
      el("li", {}, TEST_OK(w.tests[k]), ` ${name}: `, w.tests[k].reason)))
      : el("p", { class: "hint" }, "Not tested by the Skeptic yet."));
}

const OBJECTION_STATUS = { open: "warn", responded: "ok", conceded: "neutral", rejected_invalid: "neutral" };

function objectionList(list) {
  if (!list.length) return null;
  return el("ul", { class: "objections" }, list.map((o) => el("li", { "data-objection": o.id },
    el("div", { class: "badges" }, el("span", { class: `badge ${OBJECTION_STATUS[o.status] || "neutral"}` }, `Objection: ${words(o.status)}`),
      el("span", { class: "badge neutral" }, `Priority ${o.priority}`), el("span", { class: "badge neutral" }, words(o.category)),
      o.soundness ? el("span", { class: "badge bad" }, "Failed warrant test") : null,
      o.counterpart ? el("span", { class: "badge warn" }, "Contradiction") : null),
    el("p", {}, o.text),
    o.response ? el("div", { class: "response" }, el("span", { class: "hint" }, `Response (${words(o.response.status)}): `), o.response.text) : null,
    o.note ? el("p", { class: "hint" }, "Note: ", o.note) : null)));
}

// The argument: the main claim with the reasons under it, each reason edge with its warrant, every claim, edge and
// warrant with the objections on it; then the findings it rests on, the conflicts and what was conceded.
function argumentPanel(d) {
  if (d.attempt === null) return el("p", { class: "empty" }, "argue has not run yet.");
  const claims = new Map(d.claims.map((c) => [c.id, c]));
  const shown = new Set();
  const on = (ids) => { ids.forEach((i) => shown.add(i)); return d.objections.filter((o) => ids.includes(o.target_id)); };
  const node = (cid, seen) => {
    const c = claims.get(cid);
    if (!c || seen.has(cid)) return el("li", { class: "claim-node" }, el("p", { class: "hint" }, `See ${cid} above.`));
    seen = new Set([...seen, cid]);
    shown.add(cid);
    const reasons = d.edges.filter((e) => e.type === "reason_of" && e.to_id === cid);
    return el("li", { class: "claim-node", "data-claim": cid },
      claimBlock(c),
      objectionList(on([cid, ...c.evidence.map((e) => e.id)])),
      reasons.length ? el("ul", { class: "reasons" }, reasons.map((e) => {
        const w = d.warrants.find((x) => x.licenses === e.id);
        return el("li", { class: `reason-link${e.out || e.dropped ? " out" : ""}` },
          el("div", { class: "hint" }, e.dropped ? `Dropped link (${e.dropped_reason || "dropped from the argument"}): ` : e.out ? "Link out of the argument: " : "Supported by:"),
          el("ul", { class: "claim-tree-sub" }, node(e.from_id, seen)),
          warrantBlock(w),
          objectionList(on([e.id, ...(w ? [w.edge_id] : [])])));
      })) : null);
  };
  const tree = d.main_claim_id ? el("ul", { class: "claim-tree" }, node(d.main_claim_id, new Set())) : el("p", { class: "empty" }, "No main claim yet.");
  const unshown = (c) => c.role === "synthesis" && !shown.has(c.id);
  const findings = d.claims.filter((c) => unshown(c) && c.status !== "withdrawn");
  const withdrawnFindings = d.claims.filter((c) => unshown(c) && c.status === "withdrawn");
  const responses = new Set(d.objections.map((o) => o.response_claim_id).filter(Boolean));
  const loose = d.claims.filter((c) => !shown.has(c.id) && !["synthesis", "warrant"].includes(c.role) && !responses.has(c.id));
  const block = (c) => el("li", {}, claimBlock(c), objectionList(on([c.id, ...c.evidence.map((e) => e.id)])));
  const findingBlocks = findings.map(block);
  const withdrawnBlocks = withdrawnFindings.map(block);
  const looseBlocks = loose.map(block);
  const other = d.objections.filter((o) => !shown.has(o.target_id));
  const lim = d.limitations || {};
  const conceded = lim.claims?.conceded || [];
  const name = (id) => claims.get(id)?.text || id;
  return [
    el("p", { class: "hint" }, `Argument of argue attempt ${d.attempt}`, d.end ? `; the rounds ended at ${words(d.end.reason)}` : "", d.gate?.passed ? "; it passed its gate." : "."),
    d.gate && !d.gate.passed && d.gate.failures.length ? el("ul", { class: "check bad" }, d.gate.failures.map((f) => el("li", {}, f))) : null,
    d.question ? [el("h3", {}, "Research question"), el("p", { class: "rq-sentence" }, d.question)] : null,
    d.rq_checks.length ? el("p", { class: "hint" }, "Does the main claim answer it? ",
      d.rq_checks.map((r) => `${r.decision === "answers" ? "yes" : "no"}, by ${DECIDED_BY[r.by] || r.by}${r.reason ? ` (${r.reason})` : ""}`).join("; ")) : null,
    el("h3", {}, "Claims"), tree,
    looseBlocks.length ? [el("h3", {}, "Other claims"), el("ul", { class: "claims" }, looseBlocks)] : null,
    findingBlocks.length ? [el("h3", {}, `Synthesis findings it rests on${d.synthesis_attempt ? ` (synthesize attempt ${d.synthesis_attempt})` : ""}`), el("ul", { class: "claims" }, findingBlocks)] : null,
    withdrawnBlocks.length ? [el("h3", {}, "Withdrawn findings"), el("ul", { class: "claims" }, withdrawnBlocks)] : null,
    d.conflicts.length ? [el("h3", {}, "Contradicting claims"), el("ul", { class: "conflicts" }, d.conflicts.map((c) => el("li", {},
      el("p", {}, el("strong", {}, "a: "), name(c.claim_a)), el("p", {}, el("strong", {}, "b: "), name(c.claim_b)),
      el("p", { class: "hint" }, "The Skeptic: ", c.why),
      el("p", { class: "hint" }, c.decision ? `Decided: ${words(c.decision.decision)}, by ${DECIDED_BY[c.decision.by] || c.decision.by}` : "Awaiting your decision"))))] : null,
    other.length ? [el("h3", {}, "Other objections"), objectionList(other)] : null,
    conceded.length ? [el("h3", {}, "Conceded (in the limitations)"), el("ul", {}, conceded.map((o) => el("li", {}, o.text, o.note ? el("div", { class: "hint" }, o.note) : null)))] : null,
  ];
}

function designPanel(d) {
  return el("section", { class: "design-view" }, el("h2", {}, "Design"),
    el("p", { class: "hint" }, `${d.draft_id}: ${d.status}`, d.frozen_hash ? "; frozen hash " : "", d.frozen_hash ? el("code", {}, d.frozen_hash) : null),
    designDetails(d),
    d.problems.length ? [el("h4", {}, "Problems"), el("ul", { class: "check bad" }, d.problems.map((p) => el("li", {}, p)))] : null);
}

pages.argument = async (root) => {
  if (needProject()) return;
  const box = el("div", {});
  let shown = null, timer = null;
  const load = async () => {
    const [a, d] = await Promise.all([api("/ui/api/argument", { query: { project: state.project } }),
      api("/ui/api/design", { query: { project: state.project } })]);
    const now = JSON.stringify([a, d]);
    if (now === shown) return;
    shown = now;
    redrawKeeping(box, () => fill(box, el("div", { class: "argument" }, argumentPanel(a)), d.draft_id ? designPanel(d) : null));
  };
  await load();
  root.onLeave(() => clearTimeout(timer));
  root.listen((msg) => {
    if (msg.type !== "event" || !/^(argue|design|claim|objection|warrant|edge|stage)\./.test(msg.event.type)) return;
    clearTimeout(timer);
    timer = setTimeout(() => load().catch((err) => toast(err.message, "error")), 300);
  });
  fill(root, el("h1", {}, "Argument"),
    el("p", { class: "lead" }, "The argument the Arguer built on the synthesis and the Skeptic attacked: each claim with its status and hedge, the warrant of each reason with its tests, and every objection with its answer."),
    box);
};

// The stored verdict of each sentence of the shown version (as text; the server never verifies again to show it):
// verified, failing with its problems, or not verified yet; the section's own problems first.
function verdictPanel(s) {
  const status = s.error ? el("p", { class: "hint bad" }, s.error)
    : el("p", { class: s.ok === true ? "check ok" : s.ok === false ? "check bad" : "hint" },
      `v${s.version}: `, s.ok === true ? "every sentence verified." : s.ok === false ? "it fails verification." : "not verified yet.");
  const badge = (ok) => (ok === true ? ["ok", "Verified"] : ok === false ? ["bad", "Fails"] : ["neutral", "Not verified"]);
  return el("section", { class: "verdicts" }, el("h2", {}, "Verification"), status,
    s.problems.length ? el("ul", { class: "check bad" }, s.problems.map((p) => el("li", {}, p))) : null,
    el("ol", { class: "sentences" }, s.sentences.map((x) => {
      const [cls, word] = badge(x.ok);
      return el("li", { "data-index": x.index }, el("span", { class: `badge ${cls}` }, word), " ", el("span", { class: "sentence" }, x.text),
        x.problems.length ? el("ul", { class: "problems" }, x.problems.map((p) => el("li", {}, p))) : null);
    })));
}

// revise's questions (the attempt it shows), each with how it was handled, as text.
const OUTCOMES = { revised: ["ok", "Revised"], answered: ["ok", "Answered"], declined: ["warn", "Declined"],
  deleted: ["neutral", "Deleted by the P1 degrade"], rejected: ["bad", "Rejected"], open: ["warn", "Not answered yet"] };
function revisePanel(rv) {
  const gate = rv.gate ? (rv.gate.passed ? "It passed its gate." : rv.gate.failures.length ? `Its gate failed: ${rv.gate.failures.join("; ")}` : "Not gated yet.") : "";
  return el("section", { class: "revise-panel" }, el("h2", {}, "Revise"),
    rv.attempt === null ? el("p", { class: "empty" }, "revise has not run yet.") : [
      el("p", { class: "hint" }, `The reader's questions of attempt ${rv.attempt} and how each was handled. ${gate}`),
      rv.questions.length ? el("ol", { class: "questions" }, rv.questions.map((q) => {
        const [cls, word] = OUTCOMES[q.outcome] || ["neutral", label(q.outcome)];
        return el("li", { "data-question": q.id },
          el("div", { class: "badges" }, el("span", { class: `badge ${cls}` }, word), el("span", { class: "badge neutral" }, `priority ${q.priority}`),
            el("span", { class: "hint" }, `${q.section}, round ${q.round}`)),
          el("p", { class: "question" }, q.text),
          q.sentence ? el("blockquote", { class: "note" }, q.sentence) : null,
          q.note ? el("p", { class: "hint" }, `The Writer's note: ${q.note}`) : null,
          q.outcome === "rejected" && q.rejected_reasons.length ? [el("p", { class: "hint" }, "The revision answering it was not accepted:"),
            el("ul", { class: "check bad" }, q.rejected_reasons.map((r) => el("li", {}, r)))] : null);
      })) : el("p", { class: "empty" }, "The reader asked no question."),
      rv.refused.length ? [el("h3", {}, "Refused questions"), el("ul", { class: "refused" }, rv.refused.map((q) => el("li", {},
        el("span", { class: "hint" }, `${q.section}, priority ${q.priority}: `), q.text || "(empty)", el("div", { class: "hint" }, `Refused: ${q.why}`))))] : null,
      rv.dropped.length ? [el("h3", {}, "Not carried into this attempt"), el("ul", { class: "dropped" }, rv.dropped.map((q) => el("li", {},
        el("span", { class: "hint" }, `${q.section}, priority ${q.priority}: `), q.text || "",
        q.sentence ? el("blockquote", { class: "note" }, q.sentence) : null, el("div", { class: "hint" }, `Not carried: ${q.reason}`))))] : null,
      rv.back_edge ? el("p", { class: "hint warn" }, `Back to ${rv.back_edge.to}: ${rv.back_edge.reason}`) : null]);
}

pages.drafts = async (root, params) => {
  if (needProject()) return;
  const list = (await api("/ui/api/drafts", { query: { project: state.project } })).drafts;
  if (!list.length) {
    fill(root, el("h1", {}, "Drafts"), el("p", { class: "empty" }, "No drafts yet. Drafts appear here once the Writer has written a section."));
    return;
  }
  const section = params.get("section") || list[0].section;
  const d = await api(`/ui/api/drafts/${encodeURIComponent(section)}`, { query: { project: state.project, version: params.get("version") } });
  const m = await api("/ui/api/manuscript", { query: { project: state.project, section, version: d.version } });
  const picker = el("form", { class: "toolbar" },
    el("label", { class: "field" }, "Section", el("select", { name: "section" }, list.map((s) => el("option", { value: s.section, selected: s.section === section }, s.section)))),
    el("label", { class: "field" }, "Version", el("select", { name: "version" }, list.find((s) => s.section === section).versions.map((v) => el("option", { value: v, selected: v === d.version }, `v${v}`)))),
    el("button", { type: "submit" }, "Show"));
  picker.addEventListener("submit", (e) => { e.preventDefault(); location.hash = `#/drafts?${new URLSearchParams(new FormData(picker))}`; });
  const body = el("article", { class: "draft" });
  body.innerHTML = d.html; // server-rendered with raw HTML escaped (markdown-it, html=False)
  const note = el("aside", { class: "margin-note", "aria-live": "polite" }, el("p", { class: "hint" }, "Choose a source marker in the text to see what supports the sentence."));
  const showMarker = (span) => {
    body.querySelectorAll(".mk").forEach((x) => x.setAttribute("aria-expanded", "false"));
    span.setAttribute("aria-expanded", "true");
    const mk = d.markers[Number(span.dataset.i)];
    note.className = `margin-note${mk.missing ? " missing" : ""}`;
    fill(note, el("strong", {}, mk.label || mk.kind),
      mk.status ? el("div", { class: "hint" }, `${mk.kind === "obj" ? "Objection" : "Claim"} status: ${mk.status}`) : null,
      mk.source ? el("div", { class: "hint" }, "Source of the objection: ",
        el("a", { href: `#/library?item=${encodeURIComponent(mk.source.item_id)}` },
          `${mk.source.title || mk.source.item_id}${mk.source.year ? ` (${mk.source.year})` : ""}`)) : null,
      (mk.evidence || []).map((ev) => el("blockquote", { class: "note" }, ev.text,
        el("div", { class: "hint" }, `${ev.item_title || ev.item_id}${ev.locator ? `, page ${ev.locator.page}` : ""}`))),
      mk.missing ? el("p", { class: "hint" }, "This marker points to nothing in the project. The sentence is not supported until it does.") : null);
  };
  body.querySelectorAll(".mk").forEach((span) => {
    span.tabIndex = 0;
    span.setAttribute("role", "button");
    span.setAttribute("aria-expanded", "false");
    span.addEventListener("click", () => showMarker(span));
    span.addEventListener("mouseenter", () => showMarker(span));
    span.addEventListener("keydown", (e) => { if (e.key === "Enter") showMarker(span); });
  });
  fill(root, el("h1", {}, "Drafts"), picker, el("div", { class: "draft-layout" }, body, note),
    verdictPanel(m.sections[0]), revisePanel(m.revise));
};

// The Audit page: a report (the latest, or the one picked) with every hard gate and its failures, the stage owning
// them, the final review, and the export: why it cannot run yet, or the files to download (through the artifact
// route, export files only).
const OWNERS = { fail: "No stage the audit can go back to mends these: the audit stops", read_extract: "They take the manuscript back to read_extract",
  argue: "They take the manuscript back to argue", draft: "They take the manuscript back to draft" };
const MEDIA = { "application/pdf": "PDF", "application/zip": "reproducible package (zip)", "text/markdown": "markdown",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word" };
const exportUrl = (name) => `/ui/api/artifacts/${encodeURIComponent(state.project)}/export/${encodeURIComponent(name)}`;
const kib = (n) => (n < 1024 ? `${n} bytes` : `${Math.round(n / 1024)} KiB`);
pages.audit = async (root, params) => {
  if (needProject()) return;
  const d = await api("/ui/api/audit", { query: { project: state.project, version: params.get("version") } });
  const rep = d.report, e = d.export;
  const picker = d.reports.length > 1 ? el("form", { class: "toolbar" },
    el("label", { class: "field" }, "Report", el("select", { name: "version" }, d.reports.map((r) =>
      el("option", { value: r.version, selected: r.version === rep.version }, `v${r.version} (attempt ${r.attempt}, ${r.ok ? "passed" : "failed"})`)))),
    el("button", { type: "submit" }, "Show")) : null;
  picker?.addEventListener("submit", (ev) => { ev.preventDefault(); location.hash = `#/audit?${new URLSearchParams(new FormData(picker))}`; });
  const owners = rep ? Object.entries(rep.routes).filter(([, fs]) => fs.length) : [];
  fill(root, el("h1", {}, "Audit"),
    el("p", { class: "lead" }, "Every hard gate of the kernel over the final manuscript, the final review and the export."), picker,
    el("div", { class: "audit" },
      rep ? [auditSummary(rep), owners.length ? [el("h3", {}, "Who mends the failures"),
        el("ul", { class: "owners" }, owners.map(([o, fs]) => el("li", {}, `${OWNERS[o] || words(o)}: ${count(fs.length, "failure")}.`)))] : null]
        : el("p", { class: "empty" }, "The audit has not run yet."),
      d.final_review.length ? [el("h2", {}, "Final review"), el("ul", { class: "reviews" }, d.final_review.map((c) =>
        el("li", {}, `${c.id}: ${c.status}`, c.answer ? ` (${JSON.stringify(c.answer)})` : "")))] : null,
      el("section", { class: "export" }, el("h2", {}, "Export"),
        e.ready === null ? el("p", { class: "check ok" }, "The audit passed and the final review approved the manuscript: it can be exported (rk export, or a run until export).")
          : el("p", { class: "hint" }, `Not exportable now: ${e.ready}`),
        // the files are the manuscript's export only while it stands as audited and approved and the export passed for it
        e.ready !== null ? null : [
          e.built && e.current === null ? el("p", { class: "hint" }, `Export attempt ${e.attempt}, built from the audit report v${e.built.audit_report ?? "?"}: package `, el("code", {}, e.built.package)) : null,
          e.files.length ? el("ul", { class: "downloads" }, e.files.map((f) => el("li", {},
            el("a", { href: exportUrl(f.name), download: f.name }, f.name), ` — ${MEDIA[f.media_type] || f.media_type}, ${kib(f.bytes)}`)))
            : el("p", { class: "empty" }, e.built && e.current ? `Not the current export: ${e.current}` : "Nothing exported yet.")])));
};

// The Models section of Settings: one model per role. The catalog loads after the page (omp may be slow), and the
// server refuses drivers that break the vendor rules; its problems and warnings are shown here, as text.
const JUDGES = ["judge", "judge_2", "judge_3"];
function modelsSection(s) {
  const status = el("p", { class: "hint", "aria-live": "polite" }, "Loading the models each driver can run…");
  const problems = el("ul", { class: "problems", role: "alert" });
  const warnings = el("ul", { class: "warnings" });
  const notes = (list, items) => fill(list, (items || []).map((t) => el("li", {}, t)));
  notes(problems, s.driver_problems);
  notes(warnings, s.driver_warnings);
  const form = el("form", { class: "model-roles" });
  const box = el("section", { class: "models", "aria-labelledby": "models-heading" },
    el("h2", { id: "models-heading" }, "Models"),
    el("p", { class: "hint" }, "The model each role runs on. A judge must be of a known vendor, two judges must be of different vendors, and a writer's claims are checked only by judges of other vendors. omp gateway models are available when omp is installed."),
    status, form, problems, warnings);
  api("/ui/api/models").then(({ roles, models, omp_status: ompStatus }) => {
    const byProvider = new Map();
    for (const m of models) {
      if (!byProvider.has(m.provider)) byProvider.set(m.provider, []);
      byProvider.get(m.provider).push(m);
    }
    const known = new Set(models.map((m) => m.driver));
    fill(status, ompStatus || "");  // whenever omp failed or some of its entries were left out
    status.hidden = !ompStatus;
    const rows = roles.map((role) => {
      const current = s.drivers[role] || "";
      const judge = JUDGES.includes(role);
      const optionText = (m) => [m.label === m.driver ? m.driver : `${m.label} · ${m.driver}`,
        m.vendor || (judge ? "unknown vendor: cannot judge" : "unknown vendor"),
        m.local ? "local model" : null,
        m.available ? null : `unavailable: ${m.reason}`].filter(Boolean).join(" — ");
      const select = el("select", { name: `driver:${role}`, id: `driver-${role}` },
        [...byProvider].map(([provider, list]) => el("optgroup", { label: provider },
          list.map((m) => el("option", { value: m.driver, selected: m.driver === current }, optionText(m))))),
        el("option", { value: "other", selected: !known.has(current) }, "Other…"));
      const other = el("input", { type: "text", name: `other:${role}`, value: known.has(current) ? "" : current,
        placeholder: "provider:model, e.g. omp_gateway:ollama-cloud/kimi-k3", "aria-label": `Other driver for ${role}`, autocomplete: "off" });
      other.hidden = select.value !== "other";
      select.addEventListener("change", () => { other.hidden = select.value !== "other"; if (!other.hidden) other.focus(); });
      return el("div", { class: "model-row" }, el("label", { for: `driver-${role}` }, role), select, other);
    });
    fill(form, rows, el("button", { type: "submit" }, "Save models"));
  }).catch((err) => fill(status, `The model list is unavailable: ${err.message}`));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const drivers = {};
    for (const select of form.querySelectorAll("select")) {
      const role = select.name.slice("driver:".length);
      const value = select.value === "other" ? form.querySelector(`input[name="other:${role}"]`).value.trim() : select.value;
      if (value) drivers[role] = value;
    }
    try {
      const r = await api("/ui/api/settings", { method: "POST", body: { drivers } });
      Object.assign(s.drivers, drivers);
      notes(problems, []);
      notes(warnings, r.warnings);
      toast(r.warnings.length ? "Saved models, with warnings" : "Saved models");
    } catch (err) {
      notes(problems, err.detail?.problems || [err.message]);
      toast("The models were not saved", "error");
    }
  });
  return box;
}

pages.settings = async (root) => {
  const s = await api("/ui/api/settings");
  const text = (name, label, hint, type = "text") => el("label", { class: "field" }, label,
    el("input", { type, name, value: s[name] ?? "", autocomplete: "off" }), hint ? el("span", { class: "hint" }, hint) : null);
  const secret = (name, label) => el("label", { class: "field" }, label,
    el("input", { type: "password", name, placeholder: s[`${name}_set`] ? "Saved — type to replace" : "Not set", autocomplete: "off" }));
  const form = el("form", {},
    el("h2", {}, "Contact"),
    el("div", { class: "inline toolbar" }, text("email", "Email for Crossref and Unpaywall", null, "email")),
    el("h2", {}, "Keys"),
    el("p", { class: "hint" }, "Keys are stored in config.toml on this computer only and are never shown again."),
    el("div", { class: "inline toolbar" }, secret("openalex_key", "OpenAlex key"), secret("s2_key", "Semantic Scholar key"),
      secret("anthropic_api_key", "Anthropic API key"), secret("openai_api_key", "OpenAI API key"), secret("ollama_api_key", "Ollama Cloud key")),
    el("h2", {}, "Library access"),
    el("div", { class: "inline toolbar" },
      text("library_name", "Library name"),
      text("library_portal_url", "Library start page", null, "url"),
      text("library_proxy", "Proxy link pattern", "For example https://login.ezproxy.example.edu/login?url={url}"),
      el("label", { class: "field" }, "Discovery system", el("select", { name: "library_discovery" },
        el("option", { value: "" }, "None"), ["primo", "summon"].map((v) => el("option", { value: v, selected: s.library_discovery === v }, v)))),
      text("primo_base", "Primo address", null, "url"), text("primo_vid", "Primo view (vid)"), text("summon_base", "Summon address", null, "url"),
      text("translation_server_url", "Zotero translation-server (optional)", null, "url")),
    el("button", { type: "submit" }, "Save settings"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {};
    for (const [k, v] of new FormData(form)) {
      if (k.endsWith("_key") && !v) continue; // empty password field: keep the stored key
      body[k] = v === "" ? null : v;
    }
    try {
      const r = await api("/ui/api/settings", { method: "POST", body });
      toast(`Saved ${r.saved.length} settings`);
      route();
    } catch (err) { toast(err.message, "error"); }
  });
  const browser = await api("/ui/api/browser/status");
  const out = el("div", { "aria-live": "polite" });
  const run = async (step, body) => {
    try {
      const r = await api(`/ui/api/setup/check/${step}`, { method: "POST", body: body || {} });
      if (step === "capabilities") {
        fill(out, el("table", {}, el("thead", {}, el("tr", {}, ["Area", "What", "Status"].map((h) => el("th", {}, h)))),
          el("tbody", {}, r.capabilities.map((c) => el("tr", {}, el("td", {}, c.area), el("td", {}, c.name), el("td", {}, c.status))))));
      } else if (step === "providers") {
        fill(out, el("ul", {}, Object.entries(r).map(([name, p]) => el("li", {},
          `${name} (${p.roles.join(", ")}): `, p.ok ? "ready" : `not ready — ${p.reason}. To fix: ${p.hint || "see rk providers"}`))));
      } else if (step === "library") {
        fill(out, el("p", {}, r.status === "ok" ? `Library access works: the PDF came through ${r.origin.replaceAll("_", " ")}.` : `Library access failed: ${r.detail}`));
      } else {
        fill(out, el("p", {}, r.status));
      }
    } catch (err) { toast(err.message, "error"); }
  };
  const doi = el("input", { type: "text", placeholder: "DOI of a paywalled article", "aria-label": "DOI to test", size: 30 });
  const checks = el("div", { class: "toolbar" },
    el("button", { type: "button", class: "secondary", onclick: () => run("email") }, "Check email"),
    el("button", { type: "button", class: "secondary", onclick: () => run("openalex") }, "Check OpenAlex key"),
    el("button", { type: "button", class: "secondary", onclick: () => run("s2") }, "Check Semantic Scholar key"),
    el("button", { type: "button", class: "secondary", onclick: () => run("providers") }, "Check model providers"),
    el("button", { type: "button", class: "secondary", onclick: () => run("capabilities") }, "Show what works"));
  const library = el("div", { class: "toolbar" },
    el("button", { type: "button", class: "secondary", disabled: !browser.running || !s.library_portal_url, onclick: async () => {
      try { await api("/ui/api/browser/open-portal", { method: "POST", body: {} }); toast("Sign in to your library in the browser window"); }
      catch (err) { toast(err.message, "error"); } } }, "Open the library start page to sign in"),
    doi, el("button", { type: "button", class: "secondary", disabled: !browser.running, onclick: () => doi.value.trim() && run("library", { doi: doi.value.trim() }) }, "Test library access"));
  fill(root, el("h1", {}, "Settings"), form, modelsSection(s),
    el("h2", {}, "Check your setup"), checks,
    el("p", { class: "hint" }, "Library sign-in happens in the built-in browser window; the Kernel never sees your password."), library, out,
    el("h2", {}, "Built-in browser"),
    el("p", {}, browser.running ? `Running. Paused sites: ${(browser.paused_sites || []).map((p) => p.site).join(", ") || "none"}.` : "Not running. Start rk serve without --no-browser."));
};

// ---------------------------------------------------------------------------------------------------------------
// router

let renders = 0;

async function route() {
  for (const undo of leaving.splice(0)) undo();
  const render = ++renders;
  const current = () => render === renders;
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const name = pages[path] ? path : state.project ? "chat" : "projects";
  document.querySelectorAll(".rail a").forEach((a) => { if (a.dataset.page !== name) a.removeAttribute("aria-current"); else a.setAttribute("aria-current", "page"); });
  // Pages draw, subscribe and register what to undo on leaving through this root, so a page still awaiting its data
  // when the user has moved on to another page neither draws over that page nor takes its live updates.
  const page = $("#page");
  const root = {
    replaceChildren: (...nodes) => { if (current()) page.replaceChildren(...nodes); },
    listen: (fn) => { if (current()) leaving.push(listen(fn)); },
    onLeave: (fn) => { if (current()) leaving.push(fn); },
  };
  try {
    await pages[name](root, new URLSearchParams(query || ""));
    if (current()) document.title = `${name[0].toUpperCase()}${name.slice(1)} — Research Kernel`;
  } catch (err) {
    if (err.message !== "signed out") fill(root, el("h1", {}, "Something went wrong"), el("p", {}, err.message));
  }
}

async function start() {
  try {
    await api("/ui/api/session");
    await loadProjects();
  } catch { return; }
  $("#project-select").addEventListener("change", (e) => switchProject(e.target.value));
  document.addEventListener("keydown", (e) => { if (e.altKey && e.code === "KeyS") { e.preventDefault(); savePage(); } });
  window.addEventListener("hashchange", route);
  connect();
  refreshOpenCount();
  api("/ui/api/browser/status").then(showBrowser).catch(() => {});
  route();
}

start();
