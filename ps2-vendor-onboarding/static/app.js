/* Vendor Onboarding Agent - single-page UI (vanilla JS, no build step). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null ? "-" : "$" + Math.round(Number(v)).toLocaleString("en-US"));
const ago = (t) => { const s = Date.now() / 1000 - t; if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago"; if (s < 86400) return Math.floor(s / 3600) + "h ago"; return new Date(t * 1000).toLocaleDateString(); };
const api = async (url, opts) => { const r = await fetch(url, opts); if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); } return r.json(); };
const toast = (m) => { const t = $("#toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), 2800); };
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

const ICON = {
  pass: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 10.5l3.2 3L15 7"/></svg>',
  warn: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M10 5.5v5.5M10 14.5v.01"/></svg>',
  fail: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l8 8M14 6l-8 8"/></svg>',
  skip: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 10h10"/></svg>',
  chev: '<svg class="chev" width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 4l6 6-6 6"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3"/></svg>',
};
const VERDICT = {
  APPROVED: { label: "Approved", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M7.5 12.5l3 3 6-6.5"/></svg>' },
  PENDING: { label: "Pending", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 7v5l3 2"/></svg>' },
  REJECTED: { label: "Rejected", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M8.5 8.5l7 7M15.5 8.5l-7 7"/></svg>' },
};
const BADGE = { APPROVED: "Approved", PENDING: "Pending", REJECTED: "Rejected", SUPERSEDED: "Resubmitted", running: "Running", failed: "Failed" };
const OWNER_SHORT = { security: "Security", compliance: "Compliance", treasury: "Treasury", masterdata: "Master data", vendor: "Vendor" };
const CATS = { goods: "Goods", services_onsite: "Services - on site", services_remote: "Services - remote" };
const COUNTRIES = [["US", "United States"], ["DE", "Germany"], ["AE", "United Arab Emirates"], ["GB", "United Kingdom"], ["CA", "Canada"], ["FR", "France"], ["NL", "Netherlands"], ["IN", "India"], ["MX", "Mexico"]];

let health = null, currentStream = null, engine = lsGet("engine") || "auto";

/* ------------------------------------------------------------------ router */
async function route() {
  if (currentStream) { currentStream.close(); currentStream = null; }
  const [, page, id] = (location.hash || "#/new").split("/");
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === (page === "run" ? "dashboard" : page || "new")));
  window.scrollTo(0, 0);
  try {
    if (page === "run" && id) return await renderRun(id);
    if (page === "dashboard") return await renderDashboard();
    if (page === "queue") return await renderQueue();
    if (page === "vendors") return await renderVendors();
    if (page === "about") return renderAbout();
    return await renderNew();
  } catch (e) { view.innerHTML = `<div class="card empty">Something went wrong: ${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);

async function refreshChrome() {
  try {
    health = await api("/api/health");
    const p = $("#engine-pill");
    p.className = "pill " + (health.claude_available ? "ok" : "off");
    p.textContent = health.claude_available ? `AI: ${health.model}` : "AI offline · rule engine";
    p.title = health.claude_available ? "Claude classifies and reads every attachment (text + vision)" : "No ANTHROPIC_API_KEY - digital PDFs use the rule-based parser; scans go to manual keying";
    const runs = await api("/api/runs");
    const n = runs.filter((r) => isOpen(r) && r.pending_with === "internal").length;
    const c = $("#q-count"); c.textContent = n; c.classList.toggle("hidden", !n);
  } catch {}
}
const isOpen = (r) => r.status === "completed" && r.decision === "PENDING" && !r.final_status;
const finalOf = (r) => r.final_status || r.decision;

/* ------------------------------------------------------------------ new registration */
let samplesCache = null;
let attached = [];   // {kind: "sample"|"file", rel?, file?, name}
let loadedScenario = null;

const F = (path, label, cls = "c3", opts = {}) => {
  const req = opts.optional ? ' <span class="opt">(optional)</span>' : "";
  if (opts.select) return `<div class="field ${cls}"><label>${label}${req}</label><select name="${path}"><option value="">Select…</option>${opts.select.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select></div>`;
  return `<div class="field ${cls}"><label>${label}${req}</label><input name="${path}" ${opts.type ? `type="${opts.type}"` : ""} placeholder="${esc(opts.ph || "")}" autocomplete="off"></div>`;
};

async function renderNew() {
  samplesCache = samplesCache || (await api("/api/samples"));
  const samples = samplesCache;
  view.innerHTML = `
    <div class="page-head"><div><h1>New supplier registration</h1>
      <p>What a supplier submits through the portal: a form plus documents. Load a prepared scenario or fill it in yourself, then submit - every check runs live.</p></div></div>
    <div class="reg-grid">
      <form class="card" id="reg" autocomplete="off">
        <div id="loaded-note"></div>
        <div class="form-sec"><h3><span class="n">1</span>Company</h3><div class="fgrid">
          ${F("legal_name", "Legal company name", "c4", { ph: "e.g. Kestrel Freight Partners LLC" })}${F("entity_type", "Entity type", "c2", { ph: "LLC, Corporation, GmbH…" })}
          ${F("dba", "Trading name / DBA", "c3", { optional: true })}${F("country", "Country of incorporation", "c3", { select: COUNTRIES })}
          ${F("tax_id", "Tax ID (EIN / VAT / TRN)", "c3", { ph: "12-3456789" })}${F("website", "Website", "c3", { optional: true, ph: "example.com" })}
        </div></div>
        <div class="form-sec"><h3><span class="n">2</span>Registered address</h3><div class="fgrid">
          ${F("address.line1", "Street address", "c6")}${F("address.city", "City", "c2")}${F("address.region", "State / region", "c2", { optional: true })}${F("address.postal_code", "Postal code", "c2")}
        </div></div>
        <div class="form-sec"><h3><span class="n">3</span>Contact</h3><div class="fgrid">
          ${F("contact.name", "Name", "c3")}${F("contact.title", "Title", "c3", { optional: true })}${F("contact.email", "Email", "c3", { type: "email" })}${F("contact.phone", "Phone", "c3")}
        </div></div>
        <div class="form-sec"><h3><span class="n">4</span>Banking (where we pay you)</h3><div class="fgrid">
          ${F("bank.account_holder", "Account holder name", "c3")}${F("bank.bank_name", "Bank name", "c3")}
          ${F("bank.account_number", "Account number", "c2", { optional: true })}${F("bank.routing_number", "ABA routing (US)", "c2", { optional: true })}${F("bank.country", "Bank country", "c2", { select: COUNTRIES })}
          ${F("bank.iban", "IBAN (outside US)", "c4", { optional: true })}${F("bank.swift", "SWIFT / BIC", "c2", { optional: true })}
        </div></div>
        <div class="form-sec"><h3><span class="n">5</span>Engagement</h3><div class="fgrid">
          ${F("category", "Type of supply", "c2", { select: Object.entries(CATS) })}${F("expected_annual_spend", "Expected annual spend (USD)", "c2", { type: "number" })}${F("requested_by", "Brightline sponsor", "c2")}
          ${F("description", "What will you supply?", "c6", { optional: true })}
        </div></div>
        <div class="form-sec"><h3><span class="n">6</span>Documents</h3>
          <label class="attach" id="drop">${ICON.upload}<div style="font-weight:600;margin-top:4px">Attach PDFs</div>
            <div class="muted" style="font-size:12.5px">Tax form (W-9 / W-8BEN-E), bank verification letter, certificate of insurance, registration extract</div>
            <input type="file" id="files" accept="application/pdf,.pdf" multiple hidden></label>
          <div class="att-list" id="att"></div>
        </div>
        <div class="form-sec"><h3><span class="n">7</span>Declarations</h3><div class="checks-row">
          <label><input type="checkbox" name="attestations.info_accurate"> The information in this registration is accurate and complete.</label>
          <label><input type="checkbox" name="attestations.no_conflict_of_interest"> No Brightline employee has a financial interest in our company.</label></div></div>
        <div class="submit-bar">
          <div class="engine-row" style="margin:0"><span class="muted">Document reader</span>
            <div class="seg" id="seg">${["auto", "claude", "rules"].map((e) => `<button type="button" data-e="${e}" class="${e === engine ? "on" : ""}">${{ auto: "Auto", claude: "Claude AI", rules: "Rule parser" }[e]}</button>`).join("")}</div></div>
          <div style="display:flex;gap:8px"><button type="button" class="btn" id="clear">Clear</button><button type="submit" class="btn primary" id="submit">Submit registration →</button></div>
        </div>
      </form>
      <div class="reg-side">
        <div class="card card-pad">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h2>Demo scenarios</h2><span class="muted" style="font-size:12.5px">Run in order for the full story</span></div>
          <div class="samples" style="margin-top:12px">
            ${samples.map((s, i) => `
              <div class="sample ${s.tag}" data-id="${esc(s.id)}">
                <div class="num">${s.tag === "happy" ? "✓" : s.tag === "bonus" ? "+" : esc((s.title.match(/Edge (\w+)/) || [, i])[1])}</div>
                <div><div class="t">${esc(s.title)}</div><div class="d">${esc(s.description)}</div>
                  <span class="tag">Expected: ${esc(!health?.claude_available && s.expected_no_ai ? s.expected_no_ai : s.expected)}</span></div>
                <div class="acts"><button class="btn sm primary" data-run="${esc(s.id)}">Run →</button><button class="btn sm" data-load="${esc(s.id)}">Load</button></div>
              </div>`).join("")}
          </div>
          <p class="muted" style="font-size:12px;margin:12px 0 0">"Load" fills the form so you can see (and edit) exactly what the supplier sent. "Run" submits it straight away.</p>
        </div>
        <div class="card card-pad"><div style="display:flex;justify-content:space-between;align-items:center"><h2>Recent submissions</h2><a href="#/dashboard" style="font-size:12.5px">View all →</a></div><div id="recent" style="margin-top:8px"></div></div>
      </div>
    </div>`;

  const form = $("#reg");
  $("#seg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; engine = b.dataset.e; lsSet("engine", engine); $$("#seg button").forEach((x) => x.classList.toggle("on", x === b)); });
  $$("[data-load]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); loadScenario(b.dataset.load); }));
  $$("[data-run]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); loadScenario(b.dataset.run, true); submitForm(); }));
  $$(".sample").forEach((c) => c.addEventListener("click", () => loadScenario(c.dataset.id)));
  $("#clear").onclick = () => { form.reset(); attached = []; loadedScenario = null; drawAttachments(); $("#loaded-note").innerHTML = ""; $$(".sample").forEach((x) => x.classList.remove("active")); };
  form.addEventListener("submit", (e) => { e.preventDefault(); submitForm(); });
  const drop = $("#drop"), input = $("#files");
  input.addEventListener("change", () => { [...input.files].forEach((f) => attached.push({ kind: "file", file: f, name: f.name })); input.value = ""; drawAttachments(); });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => { [...e.dataTransfer.files].forEach((f) => attached.push({ kind: "file", file: f, name: f.name })); drawAttachments(); });
  if (loadedScenario) loadScenario(loadedScenario.id, true); else drawAttachments();
  api("/api/runs").then((runs) => {
    const el = $("#recent"); if (!el) return;
    el.innerHTML = runs.length ? `<table class="runs"><tbody>${runs.slice(0, 6).map((r) => `<tr data-id="${esc(r.id)}"><td>${esc(r.legal_name || "(unnamed)")}<div class="muted" style="font-size:12px">${esc(r.country || "")} · ${ago(r.created_at)}</div></td>
      <td style="text-align:right">${badge(r)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted" style="font-size:13px">No submissions yet. Start with the happy path.</p>';
    $$("tr[data-id]", el).forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/run/${tr.dataset.id}`)));
  });
}

function loadScenario(id, quiet) {
  const s = samplesCache.find((x) => x.id === id); if (!s) return;
  const form = $("#reg"); form.reset();
  const flat = (o, p = "") => Object.entries(o || {}).flatMap(([k, v]) => (v && typeof v === "object" && !Array.isArray(v) ? flat(v, p + k + ".") : [[p + k, v]]));
  flat(s.submission).forEach(([k, v]) => { const el = form.elements[k]; if (!el) return; if (el.type === "checkbox") el.checked = !!v; else { el.value = v ?? ""; el.classList.toggle("filled", v != null && v !== ""); } });
  attached = s.files.map((rel) => ({ kind: "sample", rel, name: rel.split("/").pop() }));
  loadedScenario = s;
  drawAttachments();
  $$(".sample").forEach((x) => x.classList.toggle("active", x.dataset.id === id));
  $("#loaded-note").innerHTML = `<div class="loaded"><b>Loaded:</b> ${esc(s.title)} <span class="muted" style="margin-left:auto">edit anything before submitting</span></div>`;
  if (!quiet) { form.scrollIntoView({ behavior: "smooth", block: "start" }); toast("Scenario loaded into the form"); }
}

function drawAttachments() {
  const el = $("#att"); if (!el) return;
  el.innerHTML = attached.map((a, i) => `<div class="att"><span class="pdf">PDF</span><span class="nm">${esc(a.name)}</span><span class="src">${a.kind === "sample" ? "sample" : Math.round(a.file.size / 1024) + " KB"}</span><button type="button" data-i="${i}" title="Remove">×</button></div>`).join("")
    || '<div class="muted" style="font-size:12.5px">No documents attached yet.</div>';
  $$("button[data-i]", el).forEach((b) => b.addEventListener("click", () => { attached.splice(Number(b.dataset.i), 1); drawAttachments(); }));
}

function formToSubmission() {
  const form = $("#reg"), out = {};
  [...form.elements].filter((e) => e.name).forEach((e) => {
    let v = e.type === "checkbox" ? e.checked : e.value.trim();
    if (e.type !== "checkbox" && v === "") v = null;
    if (e.name === "expected_annual_spend" && v != null) v = Number(v);
    const parts = e.name.split("."); let o = out;
    parts.slice(0, -1).forEach((p) => (o = o[p] = o[p] || {}));
    o[parts.at(-1)] = v;
  });
  if (out.address) out.address.country = out.country;
  out.currency = loadedScenario?.submission?.currency || "USD";
  return out;
}

async function submitForm() {
  const sub = formToSubmission();
  if (!sub.legal_name) { toast("Enter at least the legal company name"); return; }
  const fd = new FormData();
  fd.append("submission", JSON.stringify(sub));
  fd.append("sample_docs", JSON.stringify(attached.filter((a) => a.kind === "sample").map((a) => a.rel)));
  attached.filter((a) => a.kind === "file").forEach((a) => fd.append("files", a.file, a.name));
  if (loadedScenario) fd.append("scenario", loadedScenario.id);
  fd.append("engine", engine);
  const btn = $("#submit"); if (btn) btn.disabled = true;
  try { const { run_id } = await api("/api/runs", { method: "POST", body: fd }); location.hash = `#/run/${run_id}`; }
  catch (e) { toast(e.message); if (btn) btn.disabled = false; }
}

/* ------------------------------------------------------------------ run view */
const STAGES = [
  ["intake", "Intake", "Fingerprint the submission, link resubmissions"], ["documents", "Document reading", "Classify every attachment, extract its fields"],
  ["completeness", "Completeness", "Required fields & documents for this vendor's profile"], ["formats", "Format & validity", "Tax ID per country, IBAN / ABA / SWIFT, document dates"],
  ["consistency", "Cross-document consistency", "Names, IDs, bank details, countries"], ["screening", "Registry, duplicates & watchlist", "Business registry, vendor master, sanctions"],
  ["risk", "Risk scoring", "Explainable risk factors decide the approval path"], ["decision", "Decision & communication", "Approve, pend or reject - and tell the vendor what's needed"],
];

async function renderRun(id) {
  const run = await api(`/api/runs/${id}`);
  const sub = run.submission || {};
  view.innerHTML = `
    <div class="run-head">
      <a href="#/dashboard" class="btn sm">← All submissions</a>
      <div><h1 style="font-size:19px">${esc(sub.legal_name || "Unnamed vendor")}</h1>
        <div class="meta"><span class="mono">${esc(run.id)}</span>${run.case_id !== run.id ? `<span>case <span class="mono">${esc(run.case_id)}</span></span>` : ""}<span>${new Date(run.created_at * 1000).toLocaleString()}</span>
        <span id="run-engine">${esc(run.engine || "")}</span><span id="run-dur"></span></div></div>
      <div style="margin-left:auto;display:flex;gap:8px"><a class="btn sm primary" href="#/new">New registration</a></div>
    </div>
    <div class="progress"><div id="prog"></div></div>
    <div class="run-grid">
      <div class="timeline">${STAGES.map(([k, l, h], i) => `
        <div class="stage pending" id="st-${k}">
          <div class="stage-row"><div class="dot">${i + 1}</div><div><div class="label">${l}</div><div class="summary hint">${h}</div></div><div class="dur"><span class="t"></span>${ICON.chev}</div></div>
          <div class="stage-body"></div></div>`).join("")}
      </div>
      <div class="side">
        <div id="decision" class="decision pending"><div class="eyebrow">Decision</div><div class="verdict"><span class="spin"></span> Processing…</div>
          <div class="headline muted" id="pending-msg">Waiting for the pipeline to start</div></div>
        <div class="card"><div class="card-title">Attachments</div><div class="docs-grid" id="docs">${(run.documents || []).map((d) => `<div class="doc"><div class="ph" style="height:130px;display:grid;place-items:center;color:var(--text-3);font-size:11px;background:var(--surface-2)">reading…</div><div class="m"><b>${esc(d.filename)}</b></div></div>`).join("") || '<p class="muted" style="margin:0;font-size:13px">No attachments.</p>'}</div></div>
        <div class="card"><div class="card-title">Submission</div><div class="sub-sum">${submissionSummary(sub)}</div></div>
      </div>
    </div>`;
  $$(".stage-row").forEach((row) => row.addEventListener("click", () => { const st = row.parentElement; if (!st.classList.contains("pending") && !st.classList.contains("running")) st.classList.toggle("open"); }));

  let done = 0;
  const es = new EventSource(`/api/runs/${id}/stream`);
  currentStream = es;
  es.addEventListener("stage_started", (e) => {
    const { stage } = JSON.parse(e.data); const el = $(`#st-${stage}`); if (!el) return;
    el.className = "stage running"; $(".dot", el).innerHTML = '<span class="spin"></span>';
    const idx = STAGES.findIndex((s) => s[0] === stage);
    const pm = $("#pending-msg"); if (pm) pm.textContent = `Stage ${idx + 1} of ${STAGES.length} - ${STAGES[idx][1]}…`;
  });
  es.addEventListener("stage_progress", (e) => { const { stage, payload } = JSON.parse(e.data); const el = $(`#st-${stage} .summary`); if (el) { el.className = "summary"; el.textContent = payload.message; } });
  es.addEventListener("stage_completed", (e) => {
    const { stage, payload } = JSON.parse(e.data);
    fillStage(stage, payload); done++; $("#prog").style.width = (done / STAGES.length) * 100 + "%";
    if (stage === "documents") { drawDocs(payload.docs || []); if (payload.engine) $("#run-engine").textContent = payload.engine === "claude" ? "reader: Claude" : "reader: rule parser"; }
  });
  es.addEventListener("run_completed", async (e) => {
    es.close(); currentStream = null;
    const { payload } = JSON.parse(e.data);
    if (payload.duration_ms) $("#run-dur").textContent = `${(payload.duration_ms / 1000).toFixed(1)}s total`;
    const r = await api(`/api/runs/${id}`);
    renderDecision(r); refreshChrome();
  });
}

function submissionSummary(s) {
  const b = s.bank || {}, a = s.address || {}, c = s.contact || {};
  const rows = [["Legal name", s.legal_name], ["DBA", s.dba], ["Country · entity", [s.country, s.entity_type].filter(Boolean).join(" · ")], ["Tax ID", s.tax_id],
    ["Address", [a.line1, a.city, a.region, a.postal_code].filter(Boolean).join(", ")], ["Contact", [c.name, c.email, c.phone].filter(Boolean).join(" · ")],
    ["Bank", [b.account_holder, b.bank_name].filter(Boolean).join(" @ ")], ["Account", b.iban || [b.account_number, b.routing_number && "ABA " + b.routing_number].filter(Boolean).join(" · ")],
    ["Supply", [CATS[s.category], s.expected_annual_spend && money(s.expected_annual_spend) + "/yr"].filter(Boolean).join(" · ")], ["Sponsor", s.requested_by]];
  return `<dl class="kv">${rows.filter(([, v]) => v).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
}

function drawDocs(docs) {
  const el = $("#docs"); if (!el) return;
  el.innerHTML = docs.map((d) => {
    const cls = !d.readable ? "unk" : ["invoice", "other"].includes(d.doc_type) ? "bad" : "";
    return `<a class="doc" href="${d.file}" target="_blank" title="Open ${esc(d.filename)}"><img src="${d.preview}" alt="" loading="lazy"><div class="m"><b>${esc(d.filename)}</b><span class="t ${cls}">${esc(d.type_label)}</span></div></a>`;
  }).join("") || '<p class="muted" style="margin:0;font-size:13px">No attachments.</p>';
}

function fillStage(key, p) {
  const el = $(`#st-${key}`); if (!el) return;
  const i = STAGES.findIndex((s) => s[0] === key);
  el.className = `stage ${p.status || "pass"}`;
  $(".dot", el).innerHTML = ICON[p.status] || i + 1;
  $(".summary", el).className = "summary"; $(".summary", el).textContent = p.summary || "";
  $(".dur .t", el).textContent = p.status === "skip" ? "" : `${p.duration_ms ?? 0} ms`;
  let html = "";
  if (p.kv?.length) html += `<dl class="kv">${p.kv.map((k) => `<dt>${esc(k.label)}</dt><dd>${esc(k.value)}</dd>`).join("")}</dl>`;
  if (p.checks?.length) html += `<ul class="checks">${p.checks.map((c) => `<li><span class="ic ${c.status}">${ICON[c.status] || ICON.skip}</span><span class="n">${esc(c.name)}</span><span class="x">${esc(c.detail)}</span></li>`).join("")}</ul>`;
  (p.tables || []).forEach((t) => { if (!t.rows?.length) return;
    html += `<div class="tbl-wrap"><div class="tbl-title">${esc(t.title)}</div><table><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${t.rows.map((r) => `<tr>${r.map((c, j) => { const res = t.columns[j] === "Result" ? ` class="res-${esc(c === "same" ? "pass" : String(c).startsWith("DIFF") ? "fail" : c)}"` : ""; return `<td${res}>${esc(c)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>`; });
  if (key === "documents" && p.docs?.length) html += `<details><summary>Show extracted fields (JSON)</summary><div class="text-sample">${esc(JSON.stringify(p.docs.map((d) => ({ file: d.filename, type: d.doc_type, ...Object.fromEntries(Object.entries(d.data || {}).filter(([, v]) => v !== null && !(Array.isArray(v) && !v.length))) })), null, 2))}</div></details>`;
  $(".stage-body", el).innerHTML = html || '<p class="muted" style="margin:12px 0 0">No details.</p>';
  if (["warn", "fail"].includes(p.status) || key === "decision") el.classList.add("open");
}

function renderDecision(run) {
  const box = $("#decision");
  if (run.status === "failed") { box.className = "decision REJECTED"; box.innerHTML = `<div class="eyebrow">Decision</div><div class="verdict">Run failed</div><div class="headline">${esc(run.headline)}</div>`; return; }
  const d = run.result?.stages?.decision || {};
  const v = VERDICT[run.decision];
  const flags = d.flags || [];
  const blocking = flags.filter((f) => f.severity !== "warn"), warns = flags.filter((f) => f.severity === "warn");
  const sublabel = run.decision === "PENDING" ? (run.pending_with === "vendor" ? "Awaiting vendor" : "Internal review") : run.decision === "APPROVED" ? "Straight-through" : "Closed";
  const rs = d.resubmission, msg = d.vendor_message, vr = d.vendor_record;
  const reason = (f) => `<div class="reason ${f.severity}"><code>${esc(f.code)}</code>${esc(f.message)}<span class="owner ${esc(f.owner)}">${esc(OWNER_SHORT[f.owner] || f.owner)}</span>${f.confidential ? '<span class="conf">internal only</span>' : ""}</div>`;
  box.className = `decision ${run.decision}`;
  box.innerHTML = `
    <div class="eyebrow">Decision</div>
    <div class="verdict">${v.icon}${v.label}</div>
    <span class="verdict-sub">${sublabel}</span>
    <div class="headline">${esc(run.headline)}</div>
    <div class="route">→ <span>${run.decision === "APPROVED" ? "" : "Owner: "}<b>${esc(run.route)}</b></span></div>
    ${d.also_notify?.length ? `<div class="muted" style="font-size:12.5px;margin-top:6px">Also notify: ${d.also_notify.map(esc).join(", ")}</div>` : ""}
    ${rs ? `<div class="sec"><h3>Since previous submission</h3><div class="resub">
      ${rs.resolved.length ? rs.resolved.map((m) => `<div class="ok">✓ Resolved: ${esc(m)}</div>`).join("") : '<div class="open">Nothing from the earlier submission was resolved.</div>'}
      ${rs.still_open.length ? `<div class="open" style="margin-top:4px">Still open: ${rs.still_open.map(esc).join(", ")}</div>` : ""}
      <div class="muted" style="font-size:12px;margin-top:4px">Previous: <a href="#/run/${esc(rs.previous_run)}" class="mono">${esc(rs.previous_run)}</a></div></div></div>` : ""}
    ${vr ? `<div class="sec"><h3>Vendor record created</h3><div class="vrec">
      <div><div class="l">Vendor ID</div><div class="v mono" style="font-size:14px">${esc(vr.vendor_id)}</div></div><div><div class="l">Risk tier</div><div class="v"><span class="risk-pill ${esc(vr.risk_tier)}">${esc(vr.risk_tier)}</span></div></div>
      <div><div class="l">Payment terms</div><div class="v">${esc(vr.payment_terms)}</div></div><div><div class="l">Pays to</div><div class="v">${esc(vr.bank?.bank_name || "")} ····${esc(String(vr.bank?.account_number || vr.bank?.iban || "").slice(-4))}</div></div></div>
      ${vr.conditions?.length ? `<div class="muted" style="font-size:12.5px;margin-top:6px">Conditions: ${vr.conditions.map(esc).join("; ")}</div>` : ""}</div>` : ""}
    ${blocking.length ? `<div class="sec"><h3>Why</h3><div class="reasons">${blocking.map(reason).join("")}</div></div>` : ""}
    ${warns.length ? `<div class="sec"><h3>Recorded (not blocking)</h3><div class="reasons">${warns.map(reason).join("")}</div></div>` : ""}
    ${d.actions?.length ? `<div class="sec"><h3>Next actions</h3><ol class="actions">${d.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol></div>` : ""}
    ${msg ? `<div class="sec"><h3>Email to vendor (draft - not sent)</h3><div class="email"><div class="h"><span>To: ${esc(msg.to)}<br>Subject: ${esc(msg.subject)}</span><button class="btn sm" id="copy">Copy</button></div><pre>${esc(msg.body)}</pre></div></div>`
      : `<div class="sec"><h3>Email to vendor</h3><div class="nomail">No email drafted - do not reply to the submitter. Contact the vendor only through the details already on file.</div></div>`}
    <div class="sec" id="review-box"></div>
    ${run.case_history?.length > 1 ? `<div class="sec"><h3>Case history</h3><div class="history">${run.case_history.slice().reverse().map((h) => `<a href="#/run/${esc(h.id)}" class="${h.id === run.id ? "cur" : ""}"><span class="mono">${esc(h.id)}</span><span>${badge(h)}</span></a>`).join("")}</div></div>` : ""}`;
  if (msg) $("#copy").onclick = () => { navigator.clipboard?.writeText(`Subject: ${msg.subject}\n\n${msg.body}`); toast("Draft copied"); };
  renderReview(run);
}

function renderReview(run) {
  const box = $("#review-box"); if (!box) return;
  if (run.review) {
    box.innerHTML = `<h3>Human review</h3><div class="reviewed"><b>${run.final_status === "APPROVED" ? "Approved" : "Rejected"}</b> by ${esc(run.review.reviewer)} · ${new Date(run.review.at * 1000).toLocaleString()}${run.review.vendor_id ? ` · vendor <b>${esc(run.review.vendor_id)}</b> created` : ""}<br>"${esc(run.review.note)}"</div>`;
    return;
  }
  if (run.final_status === "SUPERSEDED") { box.innerHTML = `<h3>Status</h3><div class="reviewed">Superseded by a resubmission - <a href="#/run/${esc(run.superseded_by)}">${esc(run.superseded_by)}</a></div>`; return; }
  if (run.decision !== "PENDING") { box.innerHTML = ""; return; }
  const vendorOwes = (run.reasons || []).some((f) => f.owner === "vendor" && f.severity === "hold");
  box.innerHTML = `<h3>Reviewer decision</h3><div class="review">
    ${vendorOwes ? '<p class="muted" style="font-size:12.5px;margin:0 0 8px">The vendor still owes information - approval unlocks after their resubmission (it links to this case automatically).</p>' : ""}
    <textarea id="note" placeholder="Note for the audit trail (required) - e.g. 'Hit is a different entity: different registration no. and owners; cleared'"></textarea>
    <div class="row">${vendorOwes ? "" : '<button class="btn good" data-a="approve">Clear & approve</button>'}<button class="btn bad" data-a="reject">Reject</button></div></div>`;
  $$("button[data-a]", box).forEach((b) => b.addEventListener("click", async () => {
    const note = $("#note").value.trim(); if (!note) { toast("Add a note for the audit trail"); $("#note").focus(); return; }
    try { const r = await api(`/api/runs/${run.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: b.dataset.a, note }) });
      toast(b.dataset.a === "approve" ? `Approved - vendor ${r.vendor_id} created` : "Rejected"); renderReview(r); refreshChrome(); } catch (e) { toast(e.message); }
  }));
}

function badge(r) {
  const f = r.status && r.status !== "completed" ? r.status : finalOf(r);
  let extra = "";
  if (r.review) extra = ` <span class="badge reviewed" title="${esc(r.review.note)}">by reviewer</span>`;
  if (f === "PENDING") extra = ` <span class="muted" style="font-size:11.5px">${r.pending_with === "vendor" ? "vendor" : "internal"}</span>`;
  return `<span class="badge ${esc(f)}">${BADGE[f] || esc(f)}</span>${extra}`;
}

/* ------------------------------------------------------------------ dashboard */
let dashFilter = "all", dashQuery = "";
async function renderDashboard() {
  const runs = await api("/api/runs");
  const done = runs.filter((r) => r.status === "completed");
  const cnt = (fn) => done.filter(fn).length;
  const pct = (x) => (done.length ? Math.round((x / done.length) * 100) : 0);
  const auto = cnt((r) => r.decision === "APPROVED");
  const waitingVendor = cnt((r) => isOpen(r) && r.pending_with === "vendor"), internal = cnt((r) => isOpen(r) && r.pending_with === "internal");
  const rejected = cnt((r) => finalOf(r) === "REJECTED");
  const created = cnt((r) => r.vendor_id);
  const avg = done.length ? done.reduce((a, r) => a + (r.duration_ms || 0), 0) / done.length / 1000 : 0;
  const reasons = {};
  done.forEach((r) => (r.reasons || []).filter((f) => f.severity !== "warn").forEach((f) => { (reasons[f.code] = reasons[f.code] || { n: 0, sev: f.severity }).n++; }));
  const rs = Object.entries(reasons).sort((a, b) => b[1].n - a[1].n); const maxR = rs[0]?.[1].n || 1;
  const owners = {};
  done.filter(isOpen).forEach((r) => { owners[r.route] = (owners[r.route] || 0) + 1; });
  const mix = [["APPROVED", "pass", cnt((r) => finalOf(r) === "APPROVED")], ["PENDING", "warn", cnt((r) => finalOf(r) === "PENDING")], ["REJECTED", "fail", rejected], ["SUPERSEDED", "skip", cnt((r) => finalOf(r) === "SUPERSEDED")]];

  view.innerHTML = `
    <div class="page-head"><div><h1>Dashboard</h1><p>Every submission, where it stands, and why.</p></div><a class="btn primary" href="#/new">New registration</a></div>
    <div class="kpis">
      <div class="card kpi"><div class="l">Submissions processed</div><div class="v">${done.length}</div><div class="s">${runs.length - done.length ? runs.length - done.length + " running" : "avg " + avg.toFixed(1) + "s each"}</div></div>
      <div class="card kpi"><div class="l">Approved straight-through</div><div class="v" style="color:var(--pass)">${pct(auto)}%</div><div class="s">${auto} with no human touch</div></div>
      <div class="card kpi"><div class="l">Waiting on vendor</div><div class="v" style="color:var(--accent)">${waitingVendor}</div><div class="s">email drafted with exact asks</div></div>
      <div class="card kpi"><div class="l">Internal review</div><div class="v" style="color:var(--warn)">${internal}</div><div class="s">Compliance / Treasury / Security</div></div>
      <div class="card kpi"><div class="l">Rejected</div><div class="v" style="color:var(--fail)">${rejected}</div><div class="s">duplicates, impersonation</div></div>
      <div class="card kpi"><div class="l">Vendors created</div><div class="v">${created}</div><div class="s">incl. reviewer approvals</div></div>
    </div>
    <div class="dash-grid">
      <div class="card card-pad"><h2>Outcome mix</h2>
        <div class="split" style="margin-top:12px">${mix.map(([, c, n]) => `<div style="width:${pct(n)}%;background:var(--${c})"></div>`).join("")}</div>
        <div class="legend">${mix.map(([k, c, n]) => `<span><i style="background:var(--${c})"></i>${BADGE[k]} ${n}</span>`).join("")}</div>
        <h2 style="margin-top:22px">Open cases by owner</h2>
        ${Object.keys(owners).length ? `<table style="margin-top:8px"><tbody>${Object.entries(owners).sort((a, b) => b[1] - a[1]).map(([o, n]) => `<tr><td>${esc(o)}</td><td class="num-r"><b>${n}</b> open</td></tr>`).join("")}</tbody></table>` : '<p class="muted">No open cases.</p>'}
      </div>
      <div class="card card-pad"><h2>Why submissions stop</h2>
        ${rs.length ? `<div class="bars" style="margin-top:12px">${rs.map(([code, v]) => `<div class="bar-row"><div class="top"><span class="mono">${esc(code)}</span><span>${v.n}</span></div><div class="track"><div class="fill ${v.sev}" style="width:${(v.n / maxR) * 100}%"></div></div></div>`).join("")}</div>` : '<p class="muted">No exceptions yet.</p>'}
      </div>
    </div>
    <div class="card" style="margin-top:20px">
      <div class="card-head"><h2>Submissions</h2>
        <div class="toolbar">${["all", "APPROVED", "PENDING", "REJECTED"].map((f) => `<button class="chip ${dashFilter === f ? "on" : ""}" data-f="${f}">${f === "all" ? "All" : BADGE[f]}</button>`).join("")}
        <input class="search" id="q" placeholder="Search company, tax ID, vendor ID…" value="${esc(dashQuery)}"></div></div>
      <div style="overflow-x:auto"><table class="runs"><thead><tr><th>When</th><th>Company</th><th>Country</th><th>Tax ID</th><th>Status</th><th>Owner / outcome</th><th>Risk</th><th>Reader</th><th class="num-r">Time</th></tr></thead><tbody id="rows"></tbody></table></div>
    </div>`;
  const draw = () => {
    const q = dashQuery.toLowerCase();
    const list = runs.filter((r) => (dashFilter === "all" || finalOf(r) === dashFilter) && (!q || [r.legal_name, r.tax_id, r.vendor_id, r.id, r.country].join(" ").toLowerCase().includes(q)));
    $("#rows").innerHTML = list.length ? list.map((r) => `<tr data-id="${esc(r.id)}">
      <td title="${new Date(r.created_at * 1000).toLocaleString()}">${ago(r.created_at)}</td><td>${esc(r.legal_name || "(unnamed)")}${r.previous_run ? ' <span class="muted" style="font-size:11.5px">resubmission</span>' : ""}</td>
      <td>${esc(r.country || "")}</td><td class="mono">${esc(r.tax_id || "-")}</td><td>${badge(r)}</td>
      <td style="font-size:12.5px">${r.vendor_id ? `created <b class="mono">${esc(r.vendor_id)}</b>` : esc(r.route || "")}</td>
      <td>${r.risk_tier ? `<span class="risk-pill ${esc(r.risk_tier)}">${esc(r.risk_score)} ${esc(r.risk_tier)}</span>` : ""}</td>
      <td class="muted" style="font-size:12.5px">${esc(r.engine || "")}</td><td class="num-r mono">${r.duration_ms ? (r.duration_ms / 1000).toFixed(1) + "s" : ""}</td></tr>`).join("")
      : `<tr><td colspan="9" class="empty">${runs.length ? "No submissions match this filter." : 'No submissions yet - <a href="#/new">run the first scenario</a>.'}</td></tr>`;
    $$("#rows tr[data-id]").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/run/${tr.dataset.id}`)));
  };
  draw();
  $$(".chip").forEach((c) => c.addEventListener("click", () => { dashFilter = c.dataset.f; $$(".chip").forEach((x) => x.classList.toggle("on", x === c)); draw(); }));
  $("#q").addEventListener("input", (e) => { dashQuery = e.target.value; draw(); });
  if (runs.some((r) => r.status === "running")) setTimeout(() => location.hash === "#/dashboard" && renderDashboard(), 2000);
}

/* ------------------------------------------------------------------ review queue */
async function renderQueue() {
  const runs = (await api("/api/runs")).filter((r) => r.status === "completed");
  const internal = runs.filter((r) => isOpen(r) && r.pending_with === "internal");
  const vendor = runs.filter((r) => isOpen(r) && r.pending_with === "vendor");
  const closed = runs.filter((r) => r.decision === "PENDING" && r.final_status && r.final_status !== "SUPERSEDED");
  const card = (r, cta) => `<div class="card exc"><div><div class="route-tag">${esc(r.route)}</div><div class="h">${esc(r.headline)}</div>
    <div class="m">${esc(r.legal_name)} · ${esc(r.country || "")} · ${esc(r.tax_id || "")} · ${ago(r.created_at)}${r.review ? ` · <b>${r.final_status === "APPROVED" ? "approved" : "rejected"}</b> by ${esc(r.review.reviewer)}: "${esc(r.review.note)}"` : ""}</div></div>
    <a class="btn ${cta ? "primary" : ""} sm" href="#/run/${esc(r.id)}">${cta || "View"}</a></div>`;
  view.innerHTML = `<div class="page-head"><div><h1>Review queue</h1><p>Submissions the process would not approve on its own - each with an owner and the next step.</p></div></div>
    <div class="queue-cols">
      <div><h2 style="margin-bottom:10px">Needs internal review (${internal.length})</h2><div class="exc-list">${internal.map((r) => card(r, "Review →")).join("") || '<div class="card empty">Nothing waiting on Compliance, Treasury or Security.</div>'}</div></div>
      <div><h2 style="margin-bottom:10px">Waiting on vendor (${vendor.length})</h2><div class="exc-list">${vendor.map((r) => card(r)).join("") || '<div class="card empty">No vendors owe us information.</div>'}</div></div>
      ${closed.length ? `<div><h2 style="margin-bottom:10px">Resolved by reviewer (${closed.length})</h2><div class="exc-list">${closed.map((r) => card(r)).join("")}</div></div>` : ""}
    </div>`;
}

/* ------------------------------------------------------------------ vendor master & reference */
async function renderVendors() {
  const d = await api("/api/reference");
  view.innerHTML = `<div class="page-head"><div><h1>Vendor master & reference data</h1><p>What the process checks against. Approved submissions create vendor records here - the same master the AP invoice process pays against.</p></div>
    <button class="btn bad" id="reset">Reset demo data</button></div>
    <div class="ref-grid">
      <div class="card"><div class="card-head"><h2>Vendor master</h2><span class="muted">${d.vendors.length} vendors · ${d.vendors.filter((v) => v.source_run !== "seed").length} created by this process</span></div><div style="overflow-x:auto"><table>
        <thead><tr><th>ID</th><th>Vendor</th><th>Tax ID</th><th>Status</th><th>Risk</th><th>Bank</th><th>Contact on file</th><th>Source</th></tr></thead><tbody>
        ${d.vendors.map((v) => `<tr><td class="mono">${esc(v.vendor_id)}</td><td>${esc(v.name)}${v.dba ? `<div class="muted" style="font-size:11.5px">dba ${esc(v.dba)}</div>` : ""}</td><td class="mono">${esc(v.tax_id)}</td>
          <td><span class="status-dot ${esc(v.status)}"></span>${esc(v.status)}${v.block_reason ? `<div class="muted" style="font-size:11.5px">${esc(v.block_reason)}</div>` : ""}</td>
          <td>${v.risk_tier ? `<span class="risk-pill ${esc(v.risk_tier)}">${esc(v.risk_tier)}</span>` : ""}${v.conditions?.length ? `<div class="muted" style="font-size:11px">${v.conditions.length} condition(s)</div>` : ""}</td>
          <td style="font-size:12.5px">${esc(v.bank?.bank_name || "")} ${esc(v.bank?.account_number || v.bank?.iban || "")}</td><td style="font-size:12.5px">${esc(v.contact?.name || "")}<br>${esc(v.contact?.phone || "")}<br>${esc(v.contact?.email || "")}</td>
          <td>${v.source_run === "seed" ? '<span class="muted">existing</span>' : `<a href="#/run/${esc(v.source_run)}" class="mono">${esc(v.source_run)}</a>`}</td></tr>`).join("")}
        </tbody></table></div></div>
      <div class="card"><div class="card-head"><h2>Business registry</h2><span class="muted">stand-in for Secretary of State / Handelsregister / IRS TIN-match</span></div><div style="overflow-x:auto"><table>
        <thead><tr><th>Country</th><th>Identifiers</th><th>Registered name</th><th>Status</th><th>Incorporated</th><th>Jurisdiction</th></tr></thead><tbody>
        ${d.registry.map((r) => `<tr><td>${esc(r.country)}</td><td class="mono">${r.ids.map(esc).join("<br>")}</td><td>${esc(r.name)}</td><td><span class="status-dot ${esc(r.status)}"></span>${esc(r.status)}</td><td>${esc(r.incorporated)}</td><td style="font-size:12.5px">${esc(r.jurisdiction)}</td></tr>`).join("")}
        </tbody></table></div></div>
      <div class="card"><div class="card-head"><h2>Watchlist</h2><span class="muted">fictional entries - stand-in for OFAC / EU / UN consolidated lists</span></div><div style="overflow-x:auto"><table>
        <thead><tr><th>Entry</th><th>Name & aliases</th><th>Country</th><th>List · programme</th><th>Listed</th></tr></thead><tbody>
        ${d.watchlist.map((w) => `<tr><td class="mono">${esc(w.entry_id)}</td><td>${esc(w.name)}${w.aliases.length ? `<div class="muted" style="font-size:11.5px">aka ${w.aliases.map(esc).join(", ")}</div>` : ""}</td><td>${esc(w.country)}</td><td style="font-size:12.5px">${esc(w.list)} · ${esc(w.program)}</td><td>${esc(w.listed)}</td></tr>`).join("")}
        </tbody></table></div></div>
    </div>`;
  $("#reset").onclick = async () => { if (!confirm("Clear all submissions and restore the seed vendor master?")) return; await api("/api/reset", { method: "POST" }); toast("Demo data reset"); refreshChrome(); renderVendors(); };
}

/* ------------------------------------------------------------------ about */
function renderAbout() {
  const p = health?.policy || {};
  view.innerHTML = `<div class="about">
    <div class="page-head"><div><h1>How it works</h1><p>AI reads the documents. Code applies the policy. People make the judgement calls - and the vendor always hears exactly what's needed.</p></div></div>
    <div class="card card-pad"><h2 style="margin-bottom:12px">The pipeline</h2><div class="flow">
      ${STAGES.map(([, l, h], i) => `<div class="f"><span class="n">${String(i + 1).padStart(2, "0")}</span><b>${l}</b><span>${h}</span></div>`).join("")}</div></div>
    <div class="policy">
      <div class="card"><h3 style="color:var(--pass)">Approved</h3><p class="prose" style="margin:0;font-size:13px">Every blocking check passes. A vendor record is created in the vendor master and a welcome email with the supplier number is drafted. Medium-risk vendors are approved <b>with conditions</b> (call-back before first payment, 90-day re-review).</p></div>
      <div class="card"><h3 style="color:var(--warn)">Pending - with an owner</h3><p class="prose" style="margin:0;font-size:13px"><b>Vendor</b>: anything the supplier can fix - one email lists every item. <b>Compliance</b>: watchlist potential match, high risk. <b>Treasury</b>: third-party bank account. <b>Master data</b>: unreadable documents, registry not found. A resubmission links to the open case and shows what's resolved.</p></div>
      <div class="card"><h3 style="color:var(--fail)">Rejected</h3><p class="prose" style="margin:0;font-size:13px">What a resubmission can't fix: tax ID already belongs to an active vendor (duplicate - or impersonation, routed to Security with <b>no reply</b> to the submitter), a dissolved company, a strong watchlist match, a blocked vendor trying again.</p></div>
    </div>
    <div class="card card-pad prose"><h2 style="color:var(--text);margin-bottom:8px">Design choices</h2><ul>
      <li><b>Classify by content, not by file name.</b> Claude decides what each attachment really is - so an invoice uploaded as "bank info.pdf" is caught, and the vendor is told precisely which document is missing.</li>
      <li><b>The model reads; it never approves.</b> Claude returns schema-constrained JSON (structured outputs, vision for scans). Every decision is made by deterministic, explainable rules - the same input always gets the same answer, and every reason is shown.</li>
      <li><b>Country-aware formats.</b> EIN prefixes, the German USt-IdNr. check digit, UAE TRNs, IBAN mod-97, ABA checksums, SWIFT country codes. A typo becomes a precise ask - and if another attached document shows a valid ID, it's suggested, never silently auto-corrected.</li>
      <li><b>Names are compared the way people read them.</b> "KESTREL FREIGHT PARTNERS, L.L.C." = "Kestrel Freight Partners LLC"; a DBA is accepted. But "Inc." vs "LLC" is flagged - they're different legal entities.</li>
      <li><b>The bank account must belong to the vendor.</b> A third-party payee is the classic diversion pattern. Factoring is legitimate, so the ask offers both paths: an own-name account, or a Notice of Assignment that Treasury verifies.</li>
      <li><b>Perfect documents are not proof.</b> The vendor master is checked for the same tax ID / bank account. Re-registering an existing vendor with a new bank and a look-alike domain is treated as impersonation.</li>
      <li><b>Don't tip off.</b> A watchlist hit is never auto-cleared and never disclosed - the vendor gets a neutral "under review" note while Compliance dispositions it.</li>
      <li><b>One email, every ask.</b> Vendor follow-up is generated from the same flags that drove the decision, numbered and in plain language, so nothing is asked twice or forgotten.</li></ul></div>
    <div class="card card-pad prose"><h2 style="color:var(--text);margin-bottom:8px">Policy thresholds</h2><ul>
      <li>Bank letter ≤ ${p.bank_letter_days ?? 90} days old · W-9 / W-8BEN-E signed within 3 years · COI ≥ $${(p.coi_min ?? 1000000).toLocaleString()} general liability for on-site work, not expired.</li>
      <li>Name match ≥ ${p.name_strong ?? 92}% after normalisation · watchlist ≥ ${p.watchlist_potential ?? 80}% = Compliance review, ≥ ${p.watchlist_strong ?? 95}% + same country = reject.</li>
      <li>Risk: medium ≥ ${p.risk_medium ?? 30} points (approve with conditions), high ≥ ${p.risk_high ?? 60} (enhanced due diligence).</li>
      <li>Document dates are checked against policy date <b>${esc(health?.policy_date || "")}</b> (pinned so the demo documents behave the same on any day).</li></ul></div>
    <div class="card card-pad prose"><h2 style="color:var(--text);margin-bottom:8px">Assumptions</h2><ul>
      <li>Single US buyer entity. Registry, vendor master and watchlist are seeded JSON standing in for real APIs; the watchlist entries are fictional.</li>
      <li>No email is ever sent automatically - drafts are for a person to send. "Reset demo data" restores the seed vendor master.</li></ul></div>
  </div>`;
}

refreshChrome().then(route);
