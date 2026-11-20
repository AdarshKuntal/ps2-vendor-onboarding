# Vendor Onboarding Agent — PS-2: from submission to approval

A working onboarding process. A supplier submits a registration form and PDF documents. The submission runs
through **8 visible stages**: document classification and reading, completeness for the vendor's profile,
country-aware format checks, cross-document consistency, registry / duplicate / watchlist screening, and risk
scoring. It comes out **APPROVED** (a vendor record is created), **PENDING** (with an owner, plus a drafted email
listing every item the vendor must send), or **REJECTED**, with the reasons visible.

> **AI reads the documents. Code applies the policy. People make the judgement calls.**
> Claude classifies each attachment by content and returns schema-constrained JSON (vision for scans).
> Deterministic, explainable rules make every decision, and the vendor email is generated from the same flags.

## Run it locally

```bash
cd ps2-vendor-onboarding
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
set ANTHROPIC_API_KEY=sk-ant-...        # optional; macOS/Linux: export ANTHROPIC_API_KEY=...
uvicorn app.main:app --port 8000
```
Open http://localhost:8000. The app also runs without an API key. Digital PDFs then go through the rule-based
parser, and the scanned insurance certificate is routed to manual keying. The top-right pill shows which engine is
active.

## Deploy (live link)
`Dockerfile` + `render.yaml` are in this folder. On [Render](https://render.com): *New → Web Service → Docker*, pick
the repo, set **Root Directory** to `ps2-vendor-onboarding` and the health check to `/api/health`. You can optionally
add `ANTHROPIC_API_KEY`. Storage is SQLite. On free tiers it resets on restart, and the seed data is restored
automatically.

## Demo inputs (`samples/`, also one click each in the UI)
| # | Scenario | Expected |
|---|----------|----------|
| ✓ | Kestrel Freight Partners — clean US carrier; bank letter says "KESTREL FREIGHT PARTNERS, L.L.C.", insurer uses the DBA | APPROVED, vendor V-1007 created |
| 1 | Harborview Packaging — bank account held by a **factoring company** | PENDING → vendor (own-name letter *or* Notice of Assignment), Treasury notified |
| 1b | Harborview resubmits with an own-name bank letter | APPROVED, linked to the case, "resolved since previous submission" |
| 2 | Rheinwerk Präzisionsteile GmbH — **8-digit USt-IdNr.** + **W-9 instead of W-8BEN-E**; registry extract shows the right VAT ID | PENDING → vendor, with the suggested correction |
| 3 | "Apex Industrial Supplies LLC" — perfect docs, but the EIN is **active vendor V-1001**, new bank, look-alike domain | REJECTED → Security, **no reply** to the submitter |
| 4 | Corvane Marine Logistics FZE (UAE) — 92 % match to a (fictional) watchlist entry | PENDING → Compliance, neutral "under review" email; reviewer can clear it |
| + | Bluegill Facility Services — invoice instead of bank letter, scanned COI expired & under-insured, unsigned W-9, Inc vs LLC, Gmail, no phone | PENDING, one email with every item |
| ↻ | Re-run the happy path | REJECTED — already onboarded |

Regenerate the documents with `python scripts/generate_samples.py`. Run every scenario headless as a regression
check with `python scripts/run_scenarios.py` (add `rules` to force the rule parser, `-v` to print flags and emails).

Document dates are checked against a pinned policy date (`DEMO_TODAY`, default 2026-11-20), so the demo behaves the
same on any day. Set `DEMO_TODAY=real` to use the system date.

## Project layout
```
app/
  main.py        FastAPI: submit, SSE live stream, documents, review, reference data, reset
  pipeline.py    the 8 stages; each emits persisted events (= audit trail + live view); resubmission linking
  documents.py   PDF reading, Claude classification + extraction (text + vision), rule-parser fallback
  rules.py       completeness, tax-ID/IBAN/ABA/SWIFT validation, consistency, screening, risk, decision, vendor emails
  store.py       SQLite: runs, events, vendor master
  config.py      every policy threshold in one place
static/          single-page UI (registration form, live run view, dashboard, review queue, vendor master, how it works)
data/seed/       vendor master (shared with PS-1), business registry, fictional watchlist
samples/         7 scenarios: submission.json + PDFs, manifest.json
scripts/         sample generator, headless scenario runner
docs/            process map, demo script
```
