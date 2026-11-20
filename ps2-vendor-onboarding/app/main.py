"""HTTP API + static UI.

  POST /api/runs                       multipart: submission (JSON), files[] (PDFs), sample_docs (JSON list), scenario, engine
  GET  /api/runs/{id}/stream           Server-Sent Events: live stage events for the run view
  GET  /api/runs | /api/runs/{id}
  GET  /api/runs/{id}/docs/{doc}       original attachment (and /preview for the page-1 image)
  POST /api/runs/{id}/review           human decision on a submission pending internal review
  GET  /api/samples                    demo scenarios (form + attachments)
  GET  /api/reference                  vendor master, business registry, watchlist
  POST /api/reset                      restore seed data
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, pipeline, rules, store

app = FastAPI(title="Vendor Onboarding Agent")
store.init()

MAX_FILES, MAX_BYTES = 8, 15 * 1024 * 1024


@app.get("/api/health")
def health():
    return {"ok": True, "claude_available": config.anthropic_available(), "model": config.CLAUDE_MODEL,
            "policy_date": config.today().isoformat(),
            "policy": {"bank_letter_days": config.BANK_LETTER_MAX_AGE_DAYS, "coi_min": config.COI_MIN_GENERAL_LIABILITY,
                       "watchlist_potential": config.WATCHLIST_POTENTIAL, "watchlist_strong": config.WATCHLIST_STRONG,
                       "risk_medium": config.RISK_MEDIUM, "risk_high": config.RISK_HIGH, "name_strong": config.NAME_MATCH_STRONG}}


def _manifest() -> list[dict]:
    return json.loads((config.SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))


def _sample_path(rel: str) -> Path:
    p = (config.SAMPLES_DIR / rel).resolve()
    if config.SAMPLES_DIR.resolve() not in p.parents or not p.exists():
        raise HTTPException(404, f"Unknown sample file {rel}")
    return p


@app.get("/api/samples")
def samples():
    out = []
    for s in _manifest():
        sub = json.loads(_sample_path(f"{s['id']}/submission.json").read_text(encoding="utf-8"))
        out.append({**s, "submission": sub})
    return out


@app.get("/api/samples/{scenario}/{name}")
def sample_file(scenario: str, name: str):
    p = _sample_path(f"{scenario}/{name}")
    if p.suffix != ".pdf":
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


@app.post("/api/runs")
async def create_run(submission: str = Form(...), files: list[UploadFile] | None = File(None),
                     sample_docs: str = Form("[]"), scenario: str | None = Form(None), engine: str = Form("auto")):
    if engine not in ("auto", "claude", "rules"):
        raise HTTPException(400, "engine must be auto|claude|rules")
    try:
        sub = json.loads(submission)
        assert isinstance(sub, dict)
    except Exception:
        raise HTTPException(400, "submission must be a JSON object")
    docs = []

    def add(data: bytes, name: str):
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, f"'{name}' is not a PDF")
        if len(data) > MAX_BYTES:
            raise HTTPException(400, f"'{name}' is larger than 15 MB")
        did = uuid.uuid4().hex[:8]
        dest = config.FILES_DIR / f"{did}__{Path(name).name}"
        dest.write_bytes(data)
        docs.append({"id": did, "filename": Path(name).name, "path": str(dest), "sha256": hashlib.sha256(data).hexdigest()})

    for rel in json.loads(sample_docs or "[]"):
        p = _sample_path(rel)
        add(p.read_bytes(), p.name)
    for f in files or []:
        if f.filename:
            add(await f.read(), f.filename)
    if len(docs) > MAX_FILES:
        raise HTTPException(400, f"At most {MAX_FILES} attachments")
    source = "sample" if scenario else "form"
    rid = pipeline.start_run(sub, docs, source, scenario, engine)
    return {"run_id": rid}


@app.get("/api/runs")
def list_runs():
    return store.list_runs()


@app.get("/api/runs/{rid}")
def get_run(rid: str):
    r = store.get_run(rid)
    if not r:
        raise HTTPException(404)
    r["events"] = store.events_after(rid, 0)
    for d in r.get("documents") or []:
        d.pop("path", None)
    r["case_history"] = [{k: x.get(k) for k in ("id", "created_at", "decision", "final_status", "headline")}
                         for x in store.list_runs() if x.get("case_id") == r.get("case_id")]
    return r


@app.get("/api/runs/{rid}/stream")
async def stream(rid: str):
    if not store.get_run(rid):
        raise HTTPException(404)

    async def gen():
        last, started = 0, time.time()
        while time.time() - started < 600:
            evs = store.events_after(rid, last)
            for e in evs:
                last = e["id"]
                yield f"event: {e['type']}\ndata: {json.dumps({'stage': e['stage'], 'payload': e['payload'], 'ts': e['ts']}, default=str)}\n\n"
                if e["type"] == "run_completed":
                    return
            if not evs:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _doc(rid: str, did: str) -> dict:
    r = store.get_run(rid)
    d = next((d for d in (r or {}).get("documents") or [] if d["id"] == did), None)
    if not d:
        raise HTTPException(404)
    return d


@app.get("/api/runs/{rid}/docs/{did}")
def doc_file(rid: str, did: str):
    d = _doc(rid, did)
    if not Path(d["path"]).exists():
        raise HTTPException(404)
    return FileResponse(d["path"], media_type="application/pdf", filename=d["filename"], content_disposition_type="inline")


@app.get("/api/runs/{rid}/docs/{did}/preview")
def doc_preview(rid: str, did: str):
    _doc(rid, did)
    p = config.FILES_DIR / f"{rid}_{did}.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


class Review(BaseModel):
    action: str  # approve | reject
    note: str = ""
    reviewer: str = "Onboarding reviewer"


@app.post("/api/runs/{rid}/review")
def review(rid: str, body: Review):
    r = store.get_run(rid)
    if not r:
        raise HTTPException(404)
    if r["decision"] != "PENDING" or r.get("final_status"):
        raise HTTPException(400, "Only open pending submissions can be reviewed")
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve|reject")
    if not body.note.strip():
        raise HTTPException(400, "A note is required for the audit trail")
    reasons = r.get("reasons") or []
    if body.action == "approve" and any(f["owner"] == "vendor" and f["severity"] == "hold" for f in reasons):
        raise HTTPException(400, "The vendor still owes information - approve after their resubmission, or reject")
    rev = {"action": body.action, "note": body.note.strip(), "reviewer": body.reviewer, "at": time.time()}
    upd = {"final_status": "APPROVED" if body.action == "approve" else "REJECTED", "review": rev}
    if body.action == "approve":
        stages = (r.get("result") or {}).get("stages", {})
        docs = stages.get("documents", {}).get("docs", [])
        risk = stages.get("risk", {}).get("data", {})
        vid = store.create_vendor({**rules.vendor_record(r["submission"], docs, risk, rid), "approved_by_review": rev}, rid)
        upd["vendor_id"] = vid
        rev["vendor_id"] = vid
    store.update_run(rid, **upd)
    store.add_event(rid, "human_review", None, rev)
    return store.get_run(rid)


@app.get("/api/reference")
def reference():
    def mask(v):
        b = dict(v.get("bank") or {})
        for k in ("account_number", "iban"):
            if b.get(k):
                b[k] = "****" + "".join(b[k].split())[-4:]
        return {**v, "bank": b}
    return {"vendors": [mask(v) for v in store.vendors()], "registry": store.registry(), "watchlist": store.watchlist()}


@app.post("/api/reset")
def reset():
    store.init(reset=True)
    return {"ok": True}


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
