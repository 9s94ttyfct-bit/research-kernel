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
let unlisten = null;
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
  const add = (node) => { transcript.append(node); node.scrollIntoView({ block: "end" }); return node; };
  root.listen((msg) => {
    if (msg.type === "chat.opened" && msg.ref === ref) { state.chat = msg.chat_id; status.textContent = `Talking to ${msg.driver}.`; }
    if (msg.type === "chat.error" && msg.ref === ref) { status.textContent = `Chat unavailable: ${msg.message}`; }
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

pages.checkpoints = async (root) => {
  if (needProject()) return;
  const [open, answered] = await Promise.all([
    api("/ui/api/checkpoints", { query: { project: state.project, status: "open" } }),
    api("/ui/api/checkpoints", { query: { project: state.project, status: "answered" } })]);
  setOpenCount(open.checkpoints.length);
  const card = (c) => {
    const li = el("li", { "data-kind": c.kind, class: c.status === "answered" ? "answered" : "" },
      el("div", { class: "kind" }, c.kind.replaceAll("_", " ")), el("p", { class: "prompt" }, c.prompt));
    if (c.kind === "captcha_wait" || c.kind === "login_wait")
      li.append(el("p", { class: "hint" }, "Handle it in the built-in browser window, then press Done. The Kernel never solves CAPTCHAs or sees your password."));
    if (c.status === "answered") {
      li.append(el("p", { class: "hint" }, `Answered: ${JSON.stringify(c.answer)}`));
      return li;
    }
    const answer = async (body) => {
      try {
        await api(`/ui/api/checkpoints/${c.id}/answer`, { method: "POST", body: { project: state.project, answer: body } });
        toast("Answer recorded");
        route();
      } catch (err) { toast(err.message, "error"); }
    };
    const opts = Array.isArray(c.options) ? c.options : [];
    const buttons = el("div", { class: "toolbar" }, opts.map((o) => el("button", { type: "button", onclick: () => answer({ choice: o }) }, o === "done" ? "Done" : String(o))));
    const text = el("textarea", { "aria-label": "Your answer" });
    const form = el("form", {}, text, el("button", { type: "submit", class: "secondary" }, "Send this answer"));
    form.addEventListener("submit", (e) => { e.preventDefault(); if (text.value.trim()) answer({ text: text.value.trim() }); });
    li.append(buttons);
    if (c.kind !== "captcha_wait" && c.kind !== "login_wait") li.append(form);
    return li;
  };
  fill(root, el("h1", {}, "Checkpoints"), el("p", { class: "lead" }, "Questions only you can answer. Agents wait here; nothing is decided for you."),
    open.checkpoints.length ? el("ul", { class: "checkpoints" }, open.checkpoints.map(card)) : el("p", { class: "empty" }, "Nothing is waiting for you."),
    answered.checkpoints.length ? [el("h2", {}, "Answered"), el("ul", { class: "checkpoints" }, answered.checkpoints.slice(-20).reverse().map(card))] : null);
};

pages.progress = async (root) => {
  if (needProject()) return;
  const p = await api("/ui/api/progress", { query: { project: state.project } });
  const feed = el("ul", { class: "feed", "aria-live": "polite" });
  const row = (e) => el("li", {}, el("time", { datetime: e.ts }, e.ts.replace("T", " ").slice(0, 19)), `${e.type} `, el("span", { class: "hint" }, `${e.actor}${e.entity_id ? ` · ${e.entity_id}` : ""}`));
  feed.append(...p.events.map(row));
  root.listen((msg) => { if (msg.type === "event") feed.prepend(row(msg.event)); });
  fill(root, el("h1", {}, "Progress"),
    el("p", { class: "lead" }, "What the agents and you did in this project, newest first. The stage graph and run controls come with the orchestrator."),
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
  if (unlisten) { unlisten(); unlisten = null; }
  const render = ++renders;
  const current = () => render === renders;
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const name = pages[path] ? path : state.project ? "chat" : "projects";
  document.querySelectorAll(".rail a").forEach((a) => { if (a.dataset.page !== name) a.removeAttribute("aria-current"); else a.setAttribute("aria-current", "page"); });
  // Pages draw and subscribe through this root, so a page still awaiting its data when the user has moved on to
  // another page neither draws over that page nor takes its live updates.
  const page = $("#page");
  const root = {
    replaceChildren: (...nodes) => { if (current()) page.replaceChildren(...nodes); },
    listen: (fn) => { if (current()) unlisten = listen(fn); },
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
