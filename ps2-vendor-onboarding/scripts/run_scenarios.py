"""Headless regression run: every demo scenario through the real pipeline, against fresh seed data.

  python scripts/run_scenarios.py            # auto engine (Claude if ANTHROPIC_API_KEY is set)
  python scripts/run_scenarios.py rules      # force the rule-based document parser
  python scripts/run_scenarios.py -v         # also print flags and the vendor email
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("MIN_STAGE_MS", "0")
os.environ["RUNTIME_DIR"] = tempfile.mkdtemp(prefix="onb_test_")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, pipeline, store  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("-")]
verbose = "-v" in sys.argv
engine = args[0] if args else "auto"
ai = engine != "rules" and config.anthropic_available()
store.init(reset=True)
manifest = json.loads((config.SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))
order = manifest + [manifest[0]]  # finally: re-submit the happy path -> must be caught as already onboarded

ok = True
for i, s in enumerate(order):
    sub = json.loads((config.SAMPLES_DIR / s["id"] / "submission.json").read_text(encoding="utf-8"))
    docs = []
    for rel in s["files"]:
        src = config.SAMPLES_DIR / rel
        did = uuid.uuid4().hex[:8]
        dest = config.FILES_DIR / f"{did}__{src.name}"
        shutil.copy(src, dest)
        docs.append({"id": did, "filename": src.name, "path": str(dest)})
    rid = store.create_run("test", s["id"], engine, sub, docs)
    pipeline.Runner(rid, sub, docs, engine).run()
    r = store.get_run(rid)
    rerun = i == len(order) - 1
    exp_text = "REJECTED" if rerun else (s["expected"] if ai or "expected_no_ai" not in s else s["expected_no_ai"])
    exp = exp_text.split()[0]
    good = r["decision"] == exp
    ok &= good
    label = s["id"] + (" (re-run)" if rerun else "")
    print(f"{'OK ' if good else 'XX '}{label:<44} {r['decision'] or r['status']:<9} expected {exp:<9} [{r['engine']}] risk {r['risk_score']} {r['risk_tier']}")
    print(f"     {r['headline']}")
    print(f"     route: {r['route']}" + (f"  · pending with {r['pending_with']}" if r["pending_with"] else ""))
    dec = (r.get("result") or {}).get("stages", {}).get("decision", {})
    if dec.get("resubmission"):
        print(f"     resubmission: resolved {dec['resubmission']['resolved_codes']} · still open {dec['resubmission']['still_open']}")
    if verbose:
        for f in dec.get("flags", []):
            print(f"       [{f['severity']:<6}] {f['code']:<24} {f['owner']:<10} {f['message'][:110]}")
        m = dec.get("vendor_message")
        print("       email:", (m["subject"] if m else "(none - do not contact)"))
        if m:
            print("       " + m["body"].replace("\n", "\n       "))
    print()

print("ALL AS EXPECTED" if ok else "MISMATCHES FOUND")
sys.exit(0 if ok else 1)
