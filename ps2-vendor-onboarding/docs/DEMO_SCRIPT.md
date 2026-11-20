# 5-minute demo script (PS-2)

Before recording: **Vendor master → Reset demo data**. Keep the scenario order below.

| Time | Do | Say (in your own words) |
|------|----|-------------------------|
| 0:00–0:30 | Show the New registration page | "A buyer sponsors a supplier, the supplier sends a form and documents, and someone checks it before we can pay them. The process reads every document, applies policy, and either creates the vendor, tells the vendor exactly what's missing, or routes it to the right team." |
| 0:30–1:30 | Happy path → **Load** (show the form), then **Submit** | Walk the stages as they light up. Document reading: "it classifies each file by content." Consistency: "the bank letter says 'KESTREL FREIGHT PARTNERS, L.L.C.' and it still matches; names never match character for character." End: vendor V-1007 created, welcome email drafted. |
| 1:30–2:20 | Edge 1 → **Run**, then Edge 1b → **Run** | "The bank account belongs to a factoring company. That's either legitimate factoring or payment diversion, so it can't be auto-approved. The email offers both paths." Then the resubmission: "it links to the open case and shows what's now resolved, then approves." |
| 2:20–3:10 | Edge 2 (Rheinwerk) → **Run** | "German VAT IDs are DE plus 9 digits; they typed 8. They also sent a W-9, which is for US persons. The Handelsregister extract has a valid ID, so we suggest it, but we never auto-correct a tax ID." Show the email with the two numbered asks. |
| 3:10–4:00 | Edge 3 (Apex) → **Run** | "Every document is perfect. The only thing that catches it is the vendor master: the EIN belongs to an active vendor, with a different bank and a look-alike domain. So it's rejected to Security, and there's deliberately no reply email; we call the number on file." Mention the AP alert, which links to the invoice process. |
| 4:00–4:40 | Edge 4 (Corvane) → **Run**, then clear it in the reviewer box | "A 92 % fuzzy match to a watchlist entry. Most are false positives, but none is auto-cleared, and the vendor gets a neutral 'under review' email so we don't tip anyone off." Write a note and approve; the vendor is created, with the reviewer's name in the audit trail. |
| 4:40–5:00 | Dashboard | "Every submission: straight-through rate, what's waiting on vendors vs internal teams, and why submissions stop." |

If time allows (or in the live interview): the Bluegill bonus (one email with 5–6 asks), and re-running the happy
path (caught as already onboarded).

Talking points for questions:
* Why the LLM doesn't decide: auditability and repeatability. The same input always gives the same decision, and every reason can be pointed to.
* Why PENDING has two flavours: "waiting on vendor" vs "internal review" are different queues with different SLAs.
* What I'd build next: real registry and sanctions APIs (IRS TIN match, OpenCorporates, OFAC), an e-mail/portal loop for resubmissions, bank-account ownership verification (e.g. micro-deposits or a bank-verification API), and periodic re-screening of the whole vendor master.
