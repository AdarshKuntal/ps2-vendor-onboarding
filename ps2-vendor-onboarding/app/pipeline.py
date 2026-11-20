"""The 8-stage onboarding pipeline. Each stage emits start/complete events (persisted = audit trail,
streamed = live run view). A failing stage degrades to a hold for a human instead of crashing the run."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, documents, rules, store

STAGES = [
    ("intake", "Intake", "Fingerprint the submission, link resubmissions"),
    ("documents", "Document reading", "Classify every attachment, extract its fields"),
    ("completeness", "Completeness", "Required fields & documents for this vendor's profile"),
    ("formats", "Format & validity", "Tax ID per country, IBAN / ABA / SWIFT, document dates"),
    ("consistency", "Cross-document consistency", "Names, IDs, bank details, countries"),
    ("screening", "Registry, duplicates & watchlist", "Business registry, vendor master, sanctions"),
    ("risk", "Risk scoring", "Explainable risk factors decide the approval path"),
    ("decision", "Decision & communication", "Approve, pend or reject - and tell the vendor what's needed"),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Runner:
    def __init__(self, run_id: str, submission: dict, docs: list[dict], engine: str):
        self.run_id, self.sub, self.docs_in, self.engine_pref = run_id, submission, docs, engine
        self.flags: list[dict] = []
        self.results: dict[str, dict] = {}
        self.ctx: dict = {}
        self.t0 = time.time()

    def stage(self, key: str, fn):
        label = next(s[1] for s in STAGES if s[0] == key)
        store.add_event(self.run_id, "stage_started", key, {"label": label})
        t = time.time()
        try:
            res = fn() or {}
        except Exception as e:
            traceback.print_exc()
            res = {"status": "fail", "summary": f"Stage error: {e}", "checks": [rules.chk(label, "fail", str(e))]}
            self.flags.append(rules.flag("STAGE_ERROR", "hold", "masterdata", f"{label} failed: {e}", action="Review this submission manually."))
        elapsed = (time.time() - t) * 1000
        if elapsed < config.MIN_STAGE_MS:
            time.sleep((config.MIN_STAGE_MS - elapsed) / 1000)
        res["duration_ms"] = int(elapsed)
        self.results[key] = res
        store.add_event(self.run_id, "stage_completed", key, res)
        return res

    def progress(self, key: str, msg: str):
        store.add_event(self.run_id, "stage_progress", key, {"message": msg})

    def run(self):
        store.add_event(self.run_id, "run_started", None, {"stages": [{"key": k, "label": l, "hint": h} for k, l, h in STAGES]})
        try:
            self._run()
        except Exception as e:
            traceback.print_exc()
            store.update_run(self.run_id, status="failed", headline=f"Pipeline error: {e}")
            store.add_event(self.run_id, "run_completed", None, {"error": str(e)})

    # ------------------------------------------------------------------------------------------
    def _run(self):
        rid, sub, ctx = self.run_id, self.sub, self.ctx

        def intake():
            fp = hashlib.sha256(json.dumps(sub, sort_keys=True).encode()).hexdigest()
            store.update_run(rid, fingerprint=fp)
            prev = None
            for r in store.open_cases(rid):
                same_tid = rules.alnum(r.get("tax_id")) and rules.alnum(r.get("tax_id")) == rules.alnum(sub.get("tax_id"))
                same_name = r.get("country") == sub.get("country") and rules.name_score(r.get("legal_name"), sub.get("legal_name")) >= config.NAME_MATCH_STRONG
                if same_tid or same_name:
                    prev = r
                    break
            kv = [{"label": "Company", "value": f"{sub.get('legal_name') or '(no name)'} · {sub.get('country') or '?'}"},
                  {"label": "Sponsor", "value": sub.get("requested_by") or "-"},
                  {"label": "Type of supply", "value": {"goods": "Goods", "services_onsite": "Services - on site",
                                                        "services_remote": "Services - remote"}.get(sub.get("category"), sub.get("category") or "-")},
                  {"label": "Attachments", "value": f"{len(self.docs_in)} file(s)"},
                  {"label": "Fingerprint", "value": fp[:16] + "..."}]
            checks = [rules.chk("Submission received", "pass", f"{sum(1 for v in _flat(sub) if v not in (None, '', False))} fields completed")]
            if prev:
                ctx["previous"] = prev
                store.update_run(rid, case_id=prev["case_id"], previous_run=prev["id"])
                store.update_run(prev["id"], superseded_by=rid, final_status="SUPERSEDED")
                kv.append({"label": "Resubmission of", "value": f"{prev['id']} (case {prev['case_id']}) - pending since {time.strftime('%d %b %H:%M', time.localtime(prev['created_at']))}"})
                checks.append(rules.chk("Linked to open case", "pass", f"Earlier submission {prev['id']} had {len(prev.get('reasons') or [])} open item(s); it is now superseded"))
            else:
                checks.append(rules.chk("Open cases", "pass", "No pending submission for this company - new case"))
            return {"status": "pass", "summary": ("Resubmission - linked to case " + prev["case_id"]) if prev else
                    f"New submission · {len(self.docs_in)} attachment(s)", "kv": kv, "checks": checks}
        self.stage("intake", intake)

        def read_docs():
            out = []

            def one(i_d):
                i, d = i_d
                path = Path(d["path"])
                preview = config.FILES_DIR / f"{rid}_{d['id']}.png"
                reading = documents.read_pdf(path, preview)
                want_claude = self.engine_pref in ("auto", "claude") and config.anthropic_available()
                notes, data = [], None
                if want_claude:
                    try:
                        data = documents.extract_with_claude(reading, d["filename"])
                    except Exception as e:
                        notes.append(f"Claude unavailable ({type(e).__name__}: {str(e)[:100]}); rule parser used")
                entry = {"id": d["id"], "filename": d["filename"], "pages": reading["pages"], "scanned": reading["scanned"],
                         "preview": f"/api/runs/{rid}/docs/{d['id']}/preview", "file": f"/api/runs/{rid}/docs/{d['id']}", "notes": notes}
                if data is None and reading["scanned"]:
                    entry.update(readable=False, doc_type=None, type_label="Unreadable scan", engine="none",
                                 why="scanned image, AI vision engine not available", confidence=0, data={})
                else:
                    if data is None:
                        data = documents.extract_with_rules(reading, d["filename"])
                    meta = data.pop("_meta", {})
                    entry.update(readable=True, doc_type=data.get("doc_type", "other"), type_label=documents.DOC_TYPES.get(data.get("doc_type"), "Other"),
                                 engine=meta.get("engine"), meta=meta, confidence=data.get("confidence", 0), data=data)
                self.progress("documents", f"Read {d['filename']} -> {entry['type_label']}")
                return i, entry

            with ThreadPoolExecutor(max_workers=4) as ex:
                for i, e in sorted(ex.map(one, enumerate(self.docs_in))):
                    out.append(e)
            ctx["docs"] = out
            store.update_run(rid, documents=[{**src, **{k: v for k, v in e.items() if k not in ("data", "meta")}} for src, e in zip(self.docs_in, out)])
            checks, rows = [], []
            for e in out:
                dd = e.get("data") or {}
                keyf = _key_fields(e)
                eng = {"claude": f"Claude ({(e.get('meta') or {}).get('mode')})", "rules": "Rule parser", "none": "-"}.get(e["engine"], e["engine"])
                rows.append([e["filename"], e["type_label"], eng, keyf, f"{e['confidence']:.0%}" if e.get("readable") else "-"])
                if not e["readable"]:
                    checks.append(rules.chk(e["filename"], "fail", e["why"]))
                elif e["confidence"] < config.MIN_DOC_CONFIDENCE:
                    checks.append(rules.chk(e["filename"], "warn", f"Low read confidence ({e['confidence']:.0%})"))
                    self.flags.append(rules.flag("DOC_LOW_CONFIDENCE", "hold", "masterdata", f"'{e['filename']}' read with low confidence ({e['confidence']:.0%})",
                                                 action=f"Check the extracted values of '{e['filename']}' against the document."))
                else:
                    checks.append(rules.chk(e["filename"], "pass", f"{e['type_label']}" + (f" - {'; '.join(dd.get('notes') or [])}" if dd.get("notes") else "")))
                for n in e.get("notes") or []:
                    checks.append(rules.chk(e["filename"], "warn", n))
            engines = sorted({e["engine"] for e in out if e["engine"] != "none"})
            store.update_run(rid, engine="+".join(engines) or "none")
            types = ", ".join(e["type_label"] for e in out)
            return {"status": rules.rollup(checks) if checks else "warn", "summary": f"{len(out)} file(s): {types}" if out else "No attachments",
                    "checks": checks, "tables": [{"title": "Attachments", "columns": ["File", "Detected as", "Read by", "Key fields", "Confidence"], "rows": rows}],
                    "docs": [{k: v for k, v in e.items() if k not in ("meta",)} for e in out],
                    "engine": engines[0] if engines else None}
        self.stage("documents", read_docs)
        docs = ctx.get("docs", [])

        def simple(fn, *args, key=None):
            def run():
                res, fl = fn(*args)
                self.flags.extend(fl)
                if key:
                    ctx[key] = res.get("data", {})
                return res
            return run

        self.stage("completeness", simple(rules.completeness, sub, docs))
        fres = self.stage("formats", simple(rules.formats, sub, docs, key="formats"))
        tax_ok = (ctx.get("formats", {}).get("tax_id") or {}).get("ok", True)
        self.stage("consistency", simple(rules.consistency, sub, docs, tax_ok, key="consistency"))
        self.stage("screening", simple(rules.screening, sub, docs, store.registry(), store.vendors(), store.watchlist(), key="screening"))

        def risk():
            res, fl = rules.risk(sub, ctx, self.flags)
            self.flags.extend(fl)
            ctx["risk"] = res["data"]
            store.update_run(rid, risk_score=res["data"]["score"], risk_tier=res["data"]["tier"])
            return res
        self.stage("risk", risk)

        def decision():
            d = rules.decide(self.flags)
            run = store.get_run(rid)
            case_ref = run["case_id"]
            resub = None
            prev = ctx.get("previous")
            if prev:
                before = {f["code"]: f for f in (prev.get("reasons") or []) if f["severity"] != "warn"}
                now = {f["code"] for f in d["blocking"]}
                resub = {"previous_run": prev["id"], "resolved": [f["message"] for c, f in before.items() if c not in now],
                         "resolved_codes": [c for c in before if c not in now],
                         "still_open": [c for c in before if c in now], "new": [c for c in now if c not in before]}
            vid = None
            if d["decision"] == "APPROVED":
                vid = store.create_vendor(rules.vendor_record(sub, docs, ctx.get("risk", {}), rid), rid)
            msg = rules.vendor_message(d, sub, self.flags, case_ref, vid, resub)
            actions = []
            if d["decision"] == "APPROVED":
                actions.append(f"Vendor record {vid} created in the vendor master - AP can now match invoices and pay")
                actions += ctx.get("risk", {}).get("conditions", [])
                actions += [f["action"] for f in d["warnings"] if f.get("action") and f["action"] not in actions]
                actions.append("Send the welcome email below (draft - not sent automatically)")
            else:
                actions += [f["action"] for f in d["blocking"] if f.get("action")]
                actions += [f["action"] for f in d["warnings"] if f.get("action") and f["owner"] in ("treasury", "security")]
                if msg and any(f.get("ask") and not f["confidential"] for f in d["blocking"]):
                    actions.append(f"Send the drafted email to {rules.g(sub, 'contact.email')} - {sum(1 for f in d['blocking'] if f.get('ask') and not f['confidential'])} item(s) requested")
                elif msg:
                    actions.append("Send the holding email below - it deliberately does not mention the reason")
                if d["pending_with"] == "vendor":
                    actions.append("Case waits for the vendor; a resubmission is linked to this case automatically")
            checks = [rules.chk(f["code"], "fail" if f["severity"] in ("reject", "hold") else "warn", f["message"]) for f in d["blocking"] + d["warnings"]]
            if not checks:
                checks = [rules.chk("All checks passed", "pass", "No exceptions raised")]
            headline = _headline(d, sub, vid, ctx)
            out = {"status": {"APPROVED": "pass", "PENDING": "warn", "REJECTED": "fail"}[d["decision"]], "summary": headline,
                   "decision": d["decision"], "route": d["route"], "owner": d["owner"], "pending_with": d["pending_with"],
                   "also_notify": d["also_notify"], "checks": checks, "actions": actions, "vendor_message": msg,
                   "vendor_id": vid, "resubmission": resub, "flags": self.flags, "risk": ctx.get("risk")}
            if vid:
                out["vendor_record"] = next((v for v in store.vendors() if v["vendor_id"] == vid), None)
            ctx["decision"] = out
            return out
        self.stage("decision", decision)

        out = ctx["decision"]
        dur = int((time.time() - self.t0) * 1000)
        store.update_run(rid, status="completed", decision=out["decision"], final_status=out["decision"] if out["decision"] != "PENDING" else None,
                         pending_with=out["pending_with"], route=out["route"], headline=out["summary"], duration_ms=dur,
                         vendor_id=out["vendor_id"], reasons=[f for f in self.flags if f["severity"] != "warn"] or self.flags,
                         result={"stages": self.results})
        store.add_event(rid, "run_completed", None, {"decision": out["decision"], "duration_ms": dur})


def _flat(d, prefix=""):
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            yield from _flat(v, prefix + k + ".")
        else:
            yield v


def _key_fields(e: dict) -> str:
    d = e.get("data") or {}
    t = e.get("doc_type")
    pick = {"w9": ["legal_name", "tax_id", "signed"], "w8bene": ["legal_name", "country", "signed"],
            "bank_letter": ["account_holder", "bank_name", "document_date"], "coi": ["legal_name", "general_liability_limit", "policy_expiry"],
            "registration_extract": ["legal_name", "registration_number", "tax_id"], "invoice": ["legal_name", "invoice_number", "invoice_total"]}.get(t, ["legal_name"])
    parts = []
    for k in pick:
        v = d.get(k)
        if v is None:
            continue
        if k == "signed":
            v = "signed" if v else "UNSIGNED"
        elif k == "general_liability_limit":
            v = f"GL {rules.money(v)}"
        elif k == "policy_expiry":
            v = f"expires {v}"
        elif k == "document_date":
            v = f"dated {v}"
        elif k == "invoice_total":
            v = rules.money(v)
        parts.append(str(v))
    return " · ".join(parts) or "-"


def _headline(d, sub, vid, ctx) -> str:
    name = sub.get("legal_name") or "Unnamed vendor"
    if d["decision"] == "APPROVED":
        tier = ctx.get("risk", {}).get("tier", "low")
        return f"Approved - {name} created as {vid} ({tier} risk" + (", with conditions)" if tier == "medium" else ")")
    top = next((f for f in d["blocking"] if f["owner"] == d["owner"]), d["blocking"][0])["message"]
    if d["decision"] == "REJECTED":
        return f"Rejected: {top}"
    n = sum(1 for f in d["blocking"] if f.get("ask") and not f["confidential"])
    if d["pending_with"] == "vendor":
        return f"Pending vendor - {n} item(s) requested: {top}"
    extra = f" (+{n} item(s) requested from vendor)" if n else ""
    return f"Pending {rules.OWNERS[d['owner']].split(' - ')[0]}: {top}{extra}"


def start_run(submission: dict, docs: list[dict], source: str, scenario: str | None, engine: str) -> str:
    rid = store.create_run(source, scenario, engine, submission, [{"id": d["id"], "filename": d["filename"], "path": d["path"]} for d in docs])
    threading.Thread(target=Runner(rid, submission, docs, engine).run, daemon=True).start()
    return rid
