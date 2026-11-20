# Vendor Onboarding Agent — Process Map (PS-2)

> **AI reads the documents. Code applies the policy. People handle the judgement calls.**
> And whenever something is missing, the vendor is told exactly what to send.

## 1. The person behind the workflow
A procurement / vendor-master analyst at **Brightline Manufacturing Inc.** (Columbus, OH). A buyer
sponsors a new supplier → the supplier fills in a registration form and attaches documents → the
analyst checks it, chases what's missing, and finally creates the vendor record that AP pays against
(the same vendor master the PS-1 invoice agent uses).

Today the review is manual, the follow-up is manual, and the only audit trail is an inbox. Two kinds
of failure are expensive:
* **Letting a bad vendor through** — payment fraud (third-party / impersonated bank accounts),
  sanctions exposure, duplicate vendor records that enable duplicate payments.
* **Slow, vague follow-up** — "please resend your documents" emails that go back and forth for weeks
  while the buyer can't issue a PO.

Goal: **approve the clean submissions without a human, and turn every other one into a precise list
of asks (for the vendor) or a routed, explained review (for the right internal team).**

## 2. Input
A **registration submission** = form fields (company, tax, address, contact, banking, engagement,
attestations) + **PDF attachments** (W-9 / W-8BEN-E, bank verification letter, certificate of
insurance, company-registration extract — or whatever the vendor attached by mistake).

## 3. Stages (each visible live in the UI)

| # | Stage | What happens | Decision points |
|---|-------|--------------|-----------------|
| 1 | **Intake** | Fingerprint submission + every file; look for an earlier *pending* case for the same tax ID / company | Earlier pending case ⇒ treat as a **resubmission**, link the case, compare later |
| 2 | **Document reading** | Per PDF: text layer or scan → **classify** (what is this document really?) → **extract** fields. Claude (structured JSON, vision for scans) or the rule parser | Image-only scan without AI ⇒ "unreadable", ask for a legible copy |
| 3 | **Completeness** | Required fields + required documents **for this vendor's profile** (US vs foreign, on-site services or not) | Missing field / missing document / **wrong document attached** (an invoice where a bank letter should be; a W-9 from a German GmbH) |
| 4 | **Format & validity** | Tax-ID format per country (EIN prefix, DE USt-IdNr check digit, UAE TRN, UK VAT…), IBAN mod-97, ABA checksum, SWIFT, email/phone; document dates (W-9 signed, bank letter < 90 days, COI not expired, ≥ $1M cover) | Anything the vendor can fix ⇒ a specific ask, never a rejection |
| 5 | **Cross-document consistency** | Legal name (form ↔ tax form ↔ bank letter ↔ registry extract, with DBA + suffix normalisation), legal form (Inc ≠ LLC), tax ID across docs, bank account form ↔ letter, country of address ↔ tax ↔ bank ↔ IBAN, email domain ↔ website | **Bank account holder ≠ vendor** ⇒ hold (third-party payee). Typo'd ID that the attached registry extract shows correctly ⇒ suggest the value, ask vendor to confirm |
| 6 | **Registry, duplicates & watchlist** | Business-registry lookup (status, incorporation date, registered name); vendor-master duplicates (tax ID, bank account, name); watchlist screening (fuzzy) | Tax ID already on an **active vendor** ⇒ reject as duplicate; if bank/contact differ ⇒ **suspected impersonation** → Security. Dissolved company ⇒ reject. Watchlist ≥ 80 % ⇒ Compliance hold (vendor is **not** told why) |
| 7 | **Risk scoring** | Additive, explainable factors (company age, free-mail domain, bank country ≠ entity country, foreign, spend, on-site access) | Low ⇒ standard approval. Medium ⇒ approve **with conditions** (call-back before first payment). High ⇒ Compliance enhanced due diligence |
| 8 | **Decision & communication** | APPROVED / PENDING / REJECTED, owner, reasons, internal next actions, **drafted vendor email**, vendor-master record on approval | Resubmission ⇒ shows which earlier issues are now resolved |

## 4. Decision policy
* **REJECTED** — things a resubmission cannot fix: tax ID already belongs to an active vendor
  (duplicate record / impersonation), company dissolved in the registry, strong watchlist match.
* **PENDING · awaiting vendor** — anything fixable: missing/wrong/expired/unsigned documents, bad
  formats, inconsistent names or IDs. The vendor email lists every ask, numbered, in plain language.
* **PENDING · internal review** — needs a person's judgement: Compliance (watchlist potential match,
  high risk), Treasury (third-party bank account). Reviewer approves/rejects with a mandatory note.
* **APPROVED** — every blocking check passes. A vendor record (`V-10xx`) is created in the vendor
  master, with conditions for medium risk. A welcome email with the vendor ID is drafted.

Owner priority when several things are open: Security > Compliance > Treasury > Master data > Vendor.

## 5. Edge cases (deliberately chosen)
| # | Scenario | Why it's realistic | Expected |
|---|----------|--------------------|----------|
| ✓ | Kestrel Freight Partners LLC — clean US vendor; bank letter says "KESTREL FREIGHT PARTNERS, L.L.C." and the form uses the DBA in places | Names never match character-for-character | APPROVED, V-1007 created |
| 1 | Harborview Packaging — bank account held by a **factoring company** | Legit (receivables factoring) *or* fraud — can't auto-approve either way | PENDING → vendor + Treasury; ask for own-name letter or Notice of Assignment |
| 1b | Harborview resubmits with a bank letter in its own name | The follow-up loop | APPROVED, "resolved since previous submission" |
| 2 | Rheinwerk Präzisionsteile GmbH — German, **8-digit USt-IdNr** and a **W-9 instead of W-8BEN-E** | Tax ID wrong for the country + wrong form; the attached Handelsregister extract shows the correct VAT ID | PENDING → vendor, with the suggested correction |
| 3 | "Apex Industrial Supplies LLC" — perfect documents, but the EIN is already **active vendor V-1001**, new bank, look-alike email domain | Vendor impersonation via the onboarding back door | REJECTED → Security; do **not** reply to the submitter |
| 4 | Corvane Marine Logistics FZE (UAE) — 92 % match to a watchlist entry | Fuzzy screening hits are mostly false positives, but never auto-cleared | PENDING → Compliance; neutral "under review" email (no tipping-off) |
| + | Bluegill Facility Services — invoice attached instead of bank letter, scanned COI expired & under-insured, unsigned W-9, form says Inc but W-9 says LLC, Gmail | The everyday messy submission | PENDING → vendor, one email with every ask |
| ↻ | Re-run the happy path after approval | Duplicate vendor record | REJECTED — already onboarded as V-1007 |

## 6. Assumptions
* Buyer is a single US entity; payments in USD, EUR or AED.
* The business registry, vendor master and watchlist are seeded JSON standing in for an IRS TIN-match /
  OpenCorporates API, the ERP vendor table, and OFAC/EU/UN consolidated lists. The watchlist is fictional.
* Documents are PDFs. W-9 has no expiry, but policy requires a signature dated within 3 years.
* Bank verification letters older than 90 days are not accepted; COI must show ≥ $1M general liability
  for on-site service vendors.
* No email is ever sent automatically — drafts are for a human to send.
