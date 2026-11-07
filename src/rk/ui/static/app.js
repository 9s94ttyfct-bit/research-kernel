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
    throw new Error(msg);
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
      const li = el("li", { role: "option", tabindex: "0", "aria-selected": "false" },
        el("div", { class: "title" }, i.title),
        el("div", { class: "meta" }, [i.creators?.[0]?.family, i.year, i.venue].filter(Boolean).join(", ")),
        badgeList(i.badges));
      const pick = () => { items.querySelectorAll("li").forEach((x) => x.setAttribute("aria-selected", "false")); li.setAttribute("aria-selected", "true"); showItem(i.id, detail); };
      li.addEventListener("click", pick);
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
      return li;
    }) : el("li", { class: "empty" }, q ? "Nothing matches this search." : "The library is empty. Search in the browser, then save a page or import a file."));
  fill(root, el("h1", {}, "Library"), tools, el("div", { class: "split" }, items, detail));
};

async function showItem(id, box) {
  const d = await api(`/ui/api/library/item/${id}`, { query: { project: state.project } });
  const it = d.item;
  const ids = Object.entries(it.ids).filter(([, v]) => v);
  fill(box, el("h2", {}, it.title),
  el("p", { class: "meta" }, [it.creators.map((c) => [c.given, c.family].filter(Boolean).join(" ")).join(", "), it.year, it.venue].filter(Boolean).join(" — ")),
  badgeList(d.badges),
  ids.length ? el("p", { class: "hint" }, ids.map(([k, v]) => `${k}: ${v}`).join("   ")) : null,
  el("h2", {}, "Files"),
  d.attachments.length
    ? el("ul", {}, d.attachments.map((a) => el("li", {},
        el("a", { href: `/ui/api/files/${encodeURIComponent(state.project)}/${a.id}`, target: "_blank", rel: "noopener" }, a.kind === "pdf" ? "PDF" : "Saved page"),
        ` — ${a.origin.replaceAll("_", " ")}`, a.access_note ? el("div", { class: "hint" }, a.access_note) : null)))
    : el("p", { class: "empty" }, "No file yet. The Reader fetches open-access text first, then the library page in the browser."),
  el("h2", {}, "Notes"),
  d.notes.length
    ? el("div", {}, d.notes.map((n) => el("blockquote", { class: `note ${n.kind === "quote" && !n.verified?.quote?.ok ? "unverified" : ""}` },
        n.text, el("div", { class: "hint" }, `${n.kind}${n.locator ? `, page ${n.locator.page}` : ""}`))))
    : el("p", { class: "empty" }, "No notes yet."));
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
    already_done: "Two judges of different vendors say published work already answers this question.",
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
  return [
    facts([["Back-edge", (o.edge || "").replace("->", " → ")], ["Allow one more", `${words(o.to)} runs again`], ["Stop", "the stage ends here (an active run fails)"]]),
    (o.failures || []).length ? [el("h4", {}, "Why"), el("ul", {}, o.failures.map((f) => el("li", {}, f)))] : null,
    el("div", { class: "toolbar" },
      el("button", { type: "button", onclick: () => answer({ decision: "allow_one_more" }) }, "Allow one more use"),
      el("button", { type: "button", class: "secondary", onclick: () => answer({ decision: "stop" }) }, "Stop")),
  ];
}

const CARDS = { topic_review: topicReviewCard, rq_approval: rqCard, significance_novelty: rqCard, stage_blocked: blockedCard };
const KINDS = { topic_review: "Topic review", rq_approval: "Research question approval",
  significance_novelty: "Already answered? The judges split", stage_blocked: "Stage blocked at its back-edge limit" };

pages.checkpoints = async (root) => {
  if (needProject()) return;
  const [open, answered] = await Promise.all([
    api("/ui/api/checkpoints", { query: { project: state.project, status: "open" } }),
    api("/ui/api/checkpoints", { query: { project: state.project, status: "answered" } })]);
  setOpenCount(open.checkpoints.length);
  const card = async (c) => {
    const special = CARDS[c.kind];
    const long = special && c.kind !== "stage_blocked";  // its card shows everything the prompt says, laid out
    const li = el("li", { "data-kind": c.kind, class: c.status === "answered" ? "answered" : "" },
      el("div", { class: "kind" }, KINDS[c.kind] || words(c.kind)),
      long ? el("details", { class: "prompt" }, el("summary", {}, "The checkpoint's full text"), el("p", {}, c.prompt)) : el("p", { class: "prompt" }, c.prompt));
    if (c.kind === "captcha_wait" || c.kind === "login_wait")
      li.append(el("p", { class: "hint" }, "Handle it in the built-in browser window, then press Done. The Kernel never solves CAPTCHAs or sees your password."));
    if (c.status === "answered") {
      li.append(el("p", { class: "hint" }, `Answered: ${JSON.stringify(c.answer)}`));
      return li;
    }
    const answer = async (body) => {
      li.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      try {
        await api(`/ui/api/checkpoints/${c.id}/answer`, { method: "POST", body: { project: state.project, answer: body } });
        toast("Answer recorded");
        route();
      } catch (err) {
        li.querySelectorAll("button").forEach((b) => { b.disabled = false; });
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
  // A checkpoint that opened, or one shown open that was answered, redraws the page, unless you are writing an
  // answer: then a notice offers it.
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
      if ([...document.querySelectorAll("#page textarea")].some((t) => t.value.trim())) notice.hidden = false;
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

// Redraw `box` keeping what a keyboard user had: the focused control and the open disclosures.
function redrawKeeping(box, redraw) {
  const had = box.contains(document.activeElement) ? focusKey(document.activeElement) : null;
  const stageOf = (d) => d.closest("[data-stage]")?.dataset.stage;
  const open = new Set([...box.querySelectorAll("details[open]")].map(stageOf));
  redraw();
  box.querySelectorAll("details").forEach((d) => { if (open.has(stageOf(d))) d.open = true; });
  restoreFocus(box, had);
}

pages.progress = async (root) => {
  if (needProject()) return;
  const [p, first] = await Promise.all([api("/ui/api/progress", { query: { project: state.project } }),
    api("/ui/api/stages", { query: { project: state.project } })]);
  const graphBox = el("div", {});
  const runStatus = el("div", { "aria-live": "polite" });  // only the status line is announced
  const runButtons = el("div", {});
  let asked = 0, timer = null, graphShown = null, runShown = null;
  const refresh = async () => {
    const n = ++asked;
    try {
      const g = await api("/ui/api/stages", { query: { project: state.project } });
      if (n === asked) draw(g);  // a slower, older answer does not draw over a newer one
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
        el("p", { class: "hint" }, g.paper_type ? `Paper type: ${words(g.paper_type)}. ` : "No paper type yet. ", "The stages after the research question are not available yet."),
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
  const feed = el("ul", { class: "feed", "aria-live": "polite" });
  const row = (e) => el("li", {}, el("time", { datetime: e.ts }, e.ts.replace("T", " ").slice(0, 19)), `${e.type} `, el("span", { class: "hint" }, `${e.actor}${e.entity_id ? ` · ${e.entity_id}` : ""}`));
  feed.append(...p.events.map(row));
  const later = () => { clearTimeout(timer); timer = setTimeout(refresh, 200); };
  root.onLeave(() => clearTimeout(timer));
  root.listen((msg) => {
    if (msg.type === "event") {
      feed.prepend(row(msg.event));
      if (/^(stage|run|edge)\./.test(msg.event.type)) later();
    } else if (msg.type === "run") later();
  });
  fill(root, el("h1", {}, "Progress"),
    el("p", { class: "lead" }, "Where discovery stands, and what the agents and you did in this project, newest first."),
    el("h2", {}, "Stages"), graphBox,
    el("h2", {}, "Run"), el("div", { class: "run-controls" }, runStatus, runButtons),
    el("h2", {}, "Tasks"),
    p.tasks.length ? el("table", {}, el("thead", {}, el("tr", {}, ["Task", "Role", "Driver", "Status", "Tries", "Error"].map((h) => el("th", {}, h)))),
      el("tbody", {}, p.tasks.map((t) => el("tr", {}, el("td", {}, t.id), el("td", {}, t.role), el("td", {}, t.driver), el("td", {}, t.status), el("td", {}, t.attempts), el("td", {}, t.error || "")))))
      : el("p", { class: "empty" }, "No agent tasks yet."),
    el("h2", {}, "Open checkpoints"),
    p.open_checkpoints.length ? el("p", {}, el("a", { href: "#/checkpoints" }, `${p.open_checkpoints.length} waiting for you`)) : el("p", { class: "empty" }, "None."),
    el("h2", {}, "Activity"), feed);
};

pages.drafts = async (root, params) => {
  if (needProject()) return;
  const list = (await api("/ui/api/drafts", { query: { project: state.project } })).drafts;
  if (!list.length) {
    fill(root, el("h1", {}, "Drafts"), el("p", { class: "empty" }, "No drafts yet. Drafts appear here once the Writer has written a section."));
    return;
  }
  const section = params.get("section") || list[0].section;
  const d = await api(`/ui/api/drafts/${encodeURIComponent(section)}`, { query: { project: state.project, version: params.get("version") } });
  const picker = el("form", { class: "toolbar" },
    el("label", { class: "field" }, "Section", el("select", { name: "section" }, list.map((s) => el("option", { value: s.section, selected: s.section === section }, s.section)))),
    el("label", { class: "field" }, "Version", el("select", { name: "version" }, list.find((s) => s.section === section).versions.map((v) => el("option", { value: v, selected: v === d.version }, `v${v}`)))),
    el("button", { type: "submit" }, "Show"));
  picker.addEventListener("submit", (e) => { e.preventDefault(); location.hash = `#/drafts?${new URLSearchParams(new FormData(picker))}`; });
  const body = el("article", { class: "draft" });
  body.innerHTML = d.html; // server-rendered with raw HTML escaped (markdown-it, html=False)
  const note = el("aside", { class: "margin-note", "aria-live": "polite" }, el("p", { class: "hint" }, "Choose a source marker in the text to see what supports the sentence."));
  const showMarker = (span) => {
    body.querySelectorAll(".mk").forEach((m) => m.setAttribute("aria-expanded", "false"));
    span.setAttribute("aria-expanded", "true");
    const m = d.markers[Number(span.dataset.i)];
    note.className = `margin-note${m.missing ? " missing" : ""}`;
    fill(note, el("strong", {}, m.label || m.kind),
      m.status ? el("div", { class: "hint" }, `Claim status: ${m.status}`) : null,
      (m.evidence || []).map((ev) => el("blockquote", { class: "note" }, ev.text,
        el("div", { class: "hint" }, `${ev.item_title || ev.item_id}${ev.locator ? `, page ${ev.locator.page}` : ""}`))),
      m.missing ? el("p", { class: "hint" }, "This marker points to nothing in the project. The sentence is not supported until it does.") : null);
  };
  body.querySelectorAll(".mk").forEach((span) => {
    span.tabIndex = 0;
    span.setAttribute("role", "button");
    span.setAttribute("aria-expanded", "false");
    span.addEventListener("click", () => showMarker(span));
    span.addEventListener("mouseenter", () => showMarker(span));
    span.addEventListener("keydown", (e) => { if (e.key === "Enter") showMarker(span); });
  });
  fill(root, el("h1", {}, "Drafts"), picker, el("div", { class: "draft-layout" }, body, note));
};

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
  fill(root, el("h1", {}, "Settings"), form,
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
