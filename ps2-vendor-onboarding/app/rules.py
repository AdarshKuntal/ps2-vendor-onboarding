"""Deterministic onboarding policy. Every check returns structured, human-readable evidence.

A stage result looks like:
  {"status": pass|warn|fail|skip, "summary": str,
   "checks": [{"name","status","detail"}], "kv": [{"label","value"}], "tables": [...], "data": {...}}
and may raise *flags*, which drive the decision:
  {"code", "severity": reject|hold|warn, "owner", "message" (internal), "ask" (vendor-facing, optional),
   "action" (internal next step), "notify" [owners], "confidential" (never shown to the vendor)}
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from rapidfuzz import fuzz

from . import config

OWNERS = {
    "security": "Security - Fraud investigation",
    "compliance": "Compliance - Screening & due diligence",
    "treasury": "Treasury - Bank verification",
    "masterdata": "Vendor master data team",
    "vendor": "Vendor - awaiting information",
}
OWNER_PRIORITY = ["security", "compliance", "treasury", "masterdata", "vendor"]

IBAN_LENGTHS = {"DE": 22, "AE": 23, "GB": 22, "FR": 27, "NL": 18, "CH": 21, "ES": 24, "IT": 27, "BE": 16, "AT": 20,
                "IE": 22, "PL": 28, "SE": 24, "DK": 18, "PT": 25, "SA": 24, "QA": 29, "TR": 26}
COUNTRY_NAMES = {"US": "United States", "DE": "Germany", "AE": "United Arab Emirates", "GB": "United Kingdom",
                 "FR": "France", "NL": "Netherlands", "CA": "Canada", "IN": "India", "MX": "Mexico"}


def flag(code, severity, owner, message, ask=None, action=None, notify=None, confidential=False):
    return {"code": code, "severity": severity, "owner": owner, "message": message, "ask": ask, "action": action,
            "notify": notify or [], "confidential": confidential}


def chk(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


def rollup(checks) -> str:
    st = [c["status"] for c in checks]
    return "fail" if "fail" in st else "warn" if "warn" in st else "pass"


def money(v, cur="USD") -> str:
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur or "USD", "")
    return "-" if v is None else f"{sym}{v:,.0f}" + ("" if sym else f" {cur}")


def g(d: dict | None, path: str):
    for p in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d if d not in ("", []) else None


def alnum(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


# ============================================================================ names
_SUFFIXES = {
    "llc": "LLC", "l l c": "LLC", "inc": "INC", "incorporated": "INC", "corp": "CORP", "corporation": "CORP",
    "co": "CO", "company": "CO", "ltd": "LTD", "limited": "LTD", "plc": "PLC", "lp": "LP", "llp": "LLP",
    "gmbh": "GMBH", "ag": "AG", "kg": "KG", "fze": "FZE", "fzco": "FZCO", "fz llc": "FZLLC", "bv": "BV",
    "sa": "SA", "sarl": "SARL", "pvt": "PVT", "private": "PVT", "pte": "PTE", "holding": "", "holdings": "",
}


def _ascii(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def split_dba(s: str | None) -> tuple[str, str | None]:
    s = s or ""
    m = re.split(r"\s+(?:d/?b/?a|doing business as|t/a|trading as)\s+", s, maxsplit=1, flags=re.I)
    return m[0], (m[1] if len(m) > 1 else None)


_FORM_LABEL = {"INC": "Inc.", "LLC": "LLC", "CORP": "Corp.", "CO": "Co.", "LTD": "Ltd.", "GMBH": "GmbH", "FZE": "FZE", "FZCO": "FZCO"}


def name_parts(s: str | None) -> tuple[str, str | None]:
    """-> (normalised core name, legal-form suffix). 'KESTREL FREIGHT PARTNERS, L.L.C.' -> ('kestrel freight partners', 'LLC')"""
    base, _ = split_dba(s)
    t = _ascii(base).lower().replace("&", " and ")
    t = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", t)          # L.L.C. -> LLC
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\bl l c\b", "llc", t)
    words, suffix = t.split(), None
    while words:
        two = " ".join(words[-2:])
        if len(words) > 2 and two in _SUFFIXES:
            suffix = suffix or _SUFFIXES[two]
            words = words[:-2]
        elif words[-1] in _SUFFIXES and len(words) > 1:
            suffix = suffix or _SUFFIXES[words[-1]] or None
            words = words[:-1]
        else:
            break
    return " ".join(words), suffix


def name_score(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 0
    return int(round(fuzz.token_sort_ratio(name_parts(a)[0], name_parts(b)[0])))


def best_name_score(candidate: str | None, sub: dict) -> tuple[int, str]:
    """Compare a name from a document against the vendor's legal name and DBA; return the best."""
    opts = [(name_score(candidate, sub.get("legal_name")), "legal name")]
    if sub.get("dba"):
        opts.append((name_score(candidate, sub["dba"]), "DBA"))
    _, doc_dba = split_dba(candidate)
    if doc_dba:
        opts.append((name_score(doc_dba, sub.get("dba") or sub.get("legal_name")), "DBA on document"))
    return max(opts)


def domain_of(email_or_url: str | None) -> str | None:
    if not email_or_url:
        return None
    s = email_or_url.strip().lower()
    s = s.split("@", 1)[1] if "@" in s else re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    return s[4:] if s.startswith("www.") else s or None


# ============================================================================ format validators
def validate_tax_id(country: str | None, value: str | None) -> dict:
    """-> {ok, normalized, rule, example, detail}"""
    v = alnum(value)
    c = (country or "").upper()
    if not v:
        return {"ok": False, "normalized": None, "rule": "required", "example": None, "detail": "missing"}
    if c == "US":
        ok = bool(re.fullmatch(r"\d{9}", v))
        invalid_prefix = {"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "69", "70", "78", "79", "89", "96", "97"}
        if ok and v[:2] in invalid_prefix:
            return {"ok": False, "normalized": f"{v[:2]}-{v[2:]}", "rule": "EIN: 9 digits (NN-NNNNNNN), IRS-issued prefix",
                    "example": "12-3456789", "detail": f"prefix {v[:2]} is never issued by the IRS"}
        return {"ok": ok, "normalized": f"{v[:2]}-{v[2:]}" if ok else value, "rule": "EIN: 9 digits (NN-NNNNNNN)",
                "example": "12-3456789", "detail": "valid EIN format" if ok else f"{len(re.sub(r'[^0-9]', '', v))} digits"}
    if c == "DE":
        if not re.fullmatch(r"DE\d{9}", v):
            digits = len(re.sub(r"\D", "", v))
            return {"ok": False, "normalized": v, "rule": "USt-IdNr.: 'DE' + 9 digits", "example": "DE123456789",
                    "detail": f"'DE' + {digits} digits" if v.startswith("DE") else "must start with 'DE'"}
        ok = _de_check(v[2:])
        return {"ok": ok, "normalized": v, "rule": "USt-IdNr.: 'DE' + 9 digits, ISO 7064 check digit", "example": "DE123456789",
                "detail": "valid format and check digit" if ok else "check digit does not match"}
    if c == "AE":
        ok = bool(re.fullmatch(r"100\d{12}", v))
        return {"ok": ok, "normalized": v, "rule": "TRN: 15 digits starting 100", "example": "100123456700003",
                "detail": "valid TRN format" if ok else "not a 15-digit TRN starting with 100"}
    if c == "GB":
        ok = bool(re.fullmatch(r"GB(\d{9}|\d{12}|GD\d{3}|HA\d{3})", v))
        return {"ok": ok, "normalized": v, "rule": "VAT: 'GB' + 9 or 12 digits", "example": "GB123456789", "detail": "" if ok else "bad format"}
    if c == "CA":
        ok = bool(re.fullmatch(r"\d{9}(RT\d{4})?", v))
        return {"ok": ok, "normalized": v, "rule": "BN: 9 digits (+ RT0001)", "example": "123456789RT0001", "detail": "" if ok else "bad format"}
    if c == "IN":
        ok = bool(re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", v))
        return {"ok": ok, "normalized": v, "rule": "GSTIN: 15 characters", "example": "27AAPFU0939F1ZV", "detail": "" if ok else "bad format"}
    return {"ok": True, "normalized": v, "rule": "no format rule for this country", "example": None, "detail": "accepted - manual check", "unknown": True}


def _de_check(d9: str) -> bool:
    product = 10
    for ch in d9[:8]:
        s = (int(ch) + product) % 10 or 10
        product = (2 * s) % 11
    check = 11 - product
    return (0 if check == 10 else check) == int(d9[8])


def iban_valid(iban: str | None) -> tuple[bool, str]:
    v = alnum(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", v):
        return False, "not an IBAN"
    if IBAN_LENGTHS.get(v[:2]) and len(v) != IBAN_LENGTHS[v[:2]]:
        return False, f"{v[:2]} IBANs have {IBAN_LENGTHS[v[:2]]} characters, this has {len(v)}"
    n = int("".join(str(int(c, 36)) for c in v[4:] + v[:4]))
    return (n % 97 == 1), ("mod-97 checksum valid" if n % 97 == 1 else "mod-97 checksum fails (typo?)")


def aba_valid(r: str | None) -> tuple[bool, str]:
    v = re.sub(r"\D", "", r or "")
    if len(v) != 9:
        return False, f"{len(v)} digits (needs 9)"
    s = sum(int(d) * w for d, w in zip(v, [3, 7, 1] * 3))
    return (s % 10 == 0), ("ABA checksum valid" if s % 10 == 0 else "ABA checksum fails (typo?)")


def swift_valid(s: str | None) -> tuple[bool, str | None]:
    v = alnum(s)
    m = re.fullmatch(r"[A-Z]{4}([A-Z]{2})[A-Z0-9]{2}([A-Z0-9]{3})?", v)
    return (bool(m), m.group(1) if m else None)


IBAN_COUNTRIES = set(IBAN_LENGTHS)


# ============================================================================ helpers on documents
def doc_of(docs: list[dict], t: str) -> dict | None:
    return next((d for d in docs if d.get("readable") and d.get("doc_type") == t), None)


def _days(iso: str | None) -> int | None:
    try:
        return (config.today() - date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return None


def profile(sub: dict) -> dict:
    c = (sub.get("country") or "").upper()
    return {"us": c == "US", "country": c, "onsite": sub.get("category") == "services_onsite",
            "iban": c in IBAN_COUNTRIES, "tax_form": "w9" if c == "US" else "w8bene"}


# ============================================================================ stage 3: completeness
FIELD_LABELS = {
    "legal_name": "Legal company name", "entity_type": "Entity type", "country": "Country of incorporation",
    "tax_id": "Tax ID", "address.line1": "Street address", "address.city": "City", "address.postal_code": "Postal code",
    "contact.name": "Contact name", "contact.email": "Contact email", "contact.phone": "Contact phone",
    "category": "Type of supply", "bank.account_holder": "Bank account holder", "bank.bank_name": "Bank name",
    "bank.account_number": "Account number", "bank.routing_number": "ABA routing number", "bank.iban": "IBAN",
    "bank.swift": "SWIFT/BIC",
}


def completeness(sub: dict, docs: list[dict]):
    p, flags, checks = profile(sub), [], []
    req = ["legal_name", "entity_type", "country", "tax_id", "address.line1", "address.city", "address.postal_code",
           "contact.name", "contact.email", "contact.phone", "category", "bank.account_holder", "bank.bank_name"]
    req += ["bank.account_number", "bank.routing_number"] if p["us"] else (["bank.iban", "bank.swift"] if p["iban"] else ["bank.account_number", "bank.swift"])
    missing = [FIELD_LABELS[k] for k in req if not g(sub, k)]
    checks.append(chk(f"Form fields ({len(req) - len(missing)}/{len(req)})", "fail" if missing else "pass",
                      ("Missing: " + ", ".join(missing)) if missing else "All required fields completed"))
    if missing:
        flags.append(flag("MISSING_FIELDS", "hold", "vendor", f"Required form fields missing: {', '.join(missing)}",
                          ask=f"Complete the missing form fields: {', '.join(missing)}."))
    att = sub.get("attestations") or {}
    if not (att.get("info_accurate") and att.get("no_conflict_of_interest")):
        checks.append(chk("Attestations", "fail", "Accuracy / conflict-of-interest declarations not confirmed"))
        flags.append(flag("ATTESTATION_MISSING", "hold", "vendor", "Required declarations not confirmed",
                          ask="Confirm the two declarations at the end of the form (information is accurate; no conflict of interest)."))
    else:
        checks.append(chk("Attestations", "pass", "Accuracy and no-conflict-of-interest confirmed"))

    unreadable = [d for d in docs if not d.get("readable")]
    for d in unreadable:
        flags.append(flag("DOC_UNREADABLE", "hold", "masterdata", f"'{d['filename']}' could not be read automatically ({d.get('why')})",
                          action=f"Open '{d['filename']}' and key its fields manually, or configure the AI engine for scanned documents."))
    found = {d["doc_type"] for d in docs if d.get("readable")}
    wrong = [d for d in docs if d.get("readable") and d["doc_type"] in ("invoice", "other")]

    def need(t: str, label: str, why: str, ask: str):
        if t in found:
            checks.append(chk(label, "pass", f"Provided - {next(d['filename'] for d in docs if d.get('doc_type') == t)}"))
            return
        if wrong:
            w = wrong[0]
            what = "an invoice" if w["doc_type"] == "invoice" else "not a document we recognise"
            checks.append(chk(label, "fail", f"Missing - '{w['filename']}' was attached but is {what}"))
            flags.append(flag("WRONG_DOCUMENT", "hold", "vendor", f"{label} missing; '{w['filename']}' is {what}",
                              ask=f"The file '{w['filename']}' you attached is {what}, not a {label.lower()}. {ask}"))
            wrong.pop(0)
            return
        if unreadable:
            checks.append(chk(label, "warn", "Not found among readable files - may be in an unreadable attachment"))
            return
        checks.append(chk(label, "fail", f"Missing - {why}"))
        flags.append(flag("MISSING_DOCUMENT", "hold", "vendor", f"{label} not provided ({why})", ask=ask))

    # tax form - the right one for the vendor's country
    other_form = "w8bene" if p["tax_form"] == "w9" else "w9"
    if p["tax_form"] not in found and other_form in found:
        fn = next(d["filename"] for d in docs if d.get("doc_type") == other_form)
        if p["us"]:
            msg, ask = "US vendor submitted a W-8BEN-E", "As a US company, please submit a signed IRS Form W-9 instead of the W-8BEN-E."
        else:
            msg = f"{COUNTRY_NAMES.get(p['country'], p['country'])} entity submitted a W-9 (US persons only)"
            ask = (f"You sent a Form W-9 ('{fn}'). The W-9 is only for US persons; as a company incorporated in "
                   f"{COUNTRY_NAMES.get(p['country'], p['country'])}, please complete and sign IRS Form W-8BEN-E instead.")
        checks.append(chk("Tax form", "fail", f"Wrong form - {msg}"))
        flags.append(flag("WRONG_TAX_FORM", "hold", "vendor", msg, ask=ask))
    else:
        need(p["tax_form"], "Tax form " + ("(W-9)" if p["us"] else "(W-8BEN-E)"), "required for every vendor",
             "Attach a signed IRS Form " + ("W-9." if p["us"] else "W-8BEN-E."))
    need("bank_letter", "Bank verification letter", "required to pay any vendor",
         "Send a letter from your bank (on bank letterhead, dated within the last 90 days) confirming the account holder name and account details.")
    if p["onsite"]:
        need("coi", "Certificate of insurance", "required for on-site services",
             f"Send a current certificate of insurance showing at least {money(config.COI_MIN_GENERAL_LIABILITY)} general liability, with {config.BUYER_NAME} as certificate holder.")
    else:
        checks.append(chk("Certificate of insurance", "skip", "Not required - no on-site work"))
    if not p["us"]:
        need("registration_extract", "Company registration extract", "required for non-US vendors",
             "Send a current extract from your company register (or trade licence) showing your registered name and number.")
    for d in docs:
        if d.get("readable") and d["doc_type"] in ("invoice", "other") and d in wrong:
            checks.append(chk(f"'{d['filename']}'", "warn", f"Not needed for onboarding ({d['doc_type']}) - ignored"))
    status = rollup(checks)
    n_docs = sum(1 for c in checks if c["name"] != "Attestations" and not c["name"].startswith("Form fields") and c["status"] == "pass")
    return {"status": status, "summary": (f"{len(missing)} field(s) and " if missing else "") +
            f"{n_docs} required document(s) in order" + ("" if status == "pass" else " - gaps found"), "checks": checks}, flags


# ============================================================================ stage 4: format & validity
def formats(sub: dict, docs: list[dict]):
    p, flags, checks = profile(sub), [], []
    tv = validate_tax_id(p["country"], sub.get("tax_id"))
    data = {"tax_id": tv}
    if sub.get("tax_id"):
        if tv["ok"]:
            checks.append(chk(f"Tax ID ({tv['rule'].split(':')[0]})", "warn" if tv.get("unknown") else "pass", f"{sub['tax_id']} - {tv['detail']}"))
        else:
            # does an attached document show a well-formed ID for the same country? suggest it, never auto-correct
            suggestion = None
            for d in docs:
                cand = (d.get("data") or {}).get("tax_id")
                if cand and alnum(cand) != alnum(sub["tax_id"]) and validate_tax_id(p["country"], cand)["ok"]:
                    suggestion = (alnum(cand), d)
                    break
            detail = f"'{sub['tax_id']}' - {tv['detail']}; expected {tv['rule']}"
            ask = (f"The tax ID you entered ('{sub['tax_id']}') is not valid for {COUNTRY_NAMES.get(p['country'], p['country'])}: "
                   f"{tv['rule']} (e.g. {tv['example']}).")
            if suggestion:
                detail += f". '{suggestion[1]['filename']}' shows {suggestion[0]}"
                ask += f" Your {suggestion[1]['type_label'].lower()} shows {suggestion[0]} - please confirm this is the correct number."
                data["suggested_tax_id"] = suggestion[0]
            checks.append(chk("Tax ID format", "fail", detail))
            flags.append(flag("TAX_ID_FORMAT", "hold", "vendor", detail, ask=ask))
    b = sub.get("bank") or {}
    if p["us"]:
        if b.get("routing_number"):
            ok, why = aba_valid(b["routing_number"])
            checks.append(chk("ABA routing number", "pass" if ok else "fail", f"{b['routing_number']} - {why}"))
            if not ok:
                flags.append(flag("ROUTING_INVALID", "hold", "vendor", f"Routing number {b['routing_number']}: {why}",
                                  ask=f"The ABA routing number '{b['routing_number']}' is not valid ({why}). Please check it against your bank letter."))
    else:
        if b.get("iban"):
            ok, why = iban_valid(b["iban"])
            checks.append(chk("IBAN", "pass" if ok else "fail", f"{b['iban']} - {why}"))
            if not ok:
                flags.append(flag("IBAN_INVALID", "hold", "vendor", f"IBAN {b['iban']}: {why}", ask=f"The IBAN '{b['iban']}' is not valid ({why}). Please re-check it."))
        if b.get("swift"):
            ok, cc = swift_valid(b["swift"])
            checks.append(chk("SWIFT/BIC", "pass" if ok else "fail", f"{b['swift']} - " + (f"format valid, bank country {cc}" if ok else "not a valid SWIFT/BIC")))
            if not ok:
                flags.append(flag("SWIFT_INVALID", "hold", "vendor", f"SWIFT {b['swift']} invalid", ask=f"The SWIFT/BIC '{b['swift']}' is not valid (8 or 11 characters, e.g. DEUTDEFFXXX)."))
    email = g(sub, "contact.email")
    if email:
        ok = bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email))
        checks.append(chk("Contact email", "pass" if ok else "fail", email))
        if not ok:
            flags.append(flag("EMAIL_INVALID", "hold", "vendor", f"Email '{email}' invalid", ask=f"The contact email '{email}' is not a valid address."))
    phone = g(sub, "contact.phone")
    if phone:
        ok = len(re.sub(r"\D", "", phone)) >= 7
        checks.append(chk("Contact phone", "pass" if ok else "fail", phone))

    # document validity
    tf = doc_of(docs, p["tax_form"])
    if tf:
        lbl = "W-9" if p["tax_form"] == "w9" else "W-8BEN-E"
        dd = tf["data"]
        if dd.get("signed") is False:
            checks.append(chk(f"{lbl} signature", "fail", "Signature line is blank"))
            flags.append(flag("TAX_FORM_UNSIGNED", "hold", "vendor", f"{lbl} is not signed",
                              ask=f"Your Form {lbl} is not signed. Please sign and date it (an authorised signatory of the company)."))
        else:
            age = _days(dd.get("signature_date"))
            if age is not None and (age < -3 or age > config.W9_MAX_AGE_DAYS):
                checks.append(chk(f"{lbl} signature", "fail", f"Dated {dd['signature_date']} - outside policy (signed within 3 years)"))
                flags.append(flag("TAX_FORM_STALE", "hold", "vendor", f"{lbl} signed {dd['signature_date']}",
                                  ask=f"Your Form {lbl} is dated {dd['signature_date']}; please send a newly signed copy."))
            else:
                checks.append(chk(f"{lbl} signature", "pass", f"Signed {dd.get('signature_date') or '(date not printed)'}"))
    bl = doc_of(docs, "bank_letter")
    if bl:
        age = _days(bl["data"].get("document_date"))
        if age is None:
            checks.append(chk("Bank letter date", "warn", "No date found on the letter"))
        elif age > config.BANK_LETTER_MAX_AGE_DAYS:
            checks.append(chk("Bank letter date", "fail", f"{bl['data']['document_date']} - {age} days old (max {config.BANK_LETTER_MAX_AGE_DAYS})"))
            flags.append(flag("BANK_LETTER_STALE", "hold", "vendor", f"Bank letter is {age} days old",
                              ask=f"Your bank letter is dated {bl['data']['document_date']}; we need one issued within the last {config.BANK_LETTER_MAX_AGE_DAYS} days."))
        else:
            checks.append(chk("Bank letter date", "pass", f"{bl['data']['document_date']} - {age} days old"))
    coi = doc_of(docs, "coi")
    if coi and p["onsite"]:
        cd = coi["data"]
        left = -_days(cd.get("policy_expiry")) if _days(cd.get("policy_expiry")) is not None else None
        if left is None:
            checks.append(chk("Insurance expiry", "warn", "Expiry date not found"))
        elif left < 0:
            checks.append(chk("Insurance expiry", "fail", f"Expired {cd['policy_expiry']}"))
            flags.append(flag("COI_EXPIRED", "hold", "vendor", f"Certificate of insurance expired {cd['policy_expiry']}",
                              ask=f"Your certificate of insurance expired on {cd['policy_expiry']}. Please send the current certificate."))
        elif left <= config.COI_EXPIRY_WARNING_DAYS:
            checks.append(chk("Insurance expiry", "warn", f"Expires {cd['policy_expiry']} ({left} days)"))
            flags.append(flag("COI_EXPIRING", "warn", "masterdata", f"Insurance expires in {left} days", action="Diary a renewal request before the expiry date."))
        else:
            checks.append(chk("Insurance expiry", "pass", f"Valid until {cd['policy_expiry']}"))
        gl = cd.get("general_liability_limit")
        if gl is None:
            checks.append(chk("General liability cover", "warn", "Limit not found"))
        elif gl < config.COI_MIN_GENERAL_LIABILITY:
            checks.append(chk("General liability cover", "fail", f"{money(gl)} < required {money(config.COI_MIN_GENERAL_LIABILITY)}"))
            flags.append(flag("COI_INSUFFICIENT", "hold", "vendor", f"General liability {money(gl)} below {money(config.COI_MIN_GENERAL_LIABILITY)}",
                              ask=f"Your general liability cover is {money(gl)}; on-site suppliers need at least {money(config.COI_MIN_GENERAL_LIABILITY)} per occurrence."))
        else:
            checks.append(chk("General liability cover", "pass", f"{money(gl)} per occurrence"))
        holder = cd.get("certificate_holder") or ""
        if name_score(holder, config.BUYER_NAME) < config.NAME_MATCH_CLOSE:
            checks.append(chk("Certificate holder", "warn", f"'{holder or 'not stated'}' - expected {config.BUYER_NAME}"))
            flags.append(flag("COI_HOLDER", "warn", "masterdata", "Brightline is not named as certificate holder",
                              action="Request a re-issued certificate naming Brightline as holder at the next renewal."))
        else:
            checks.append(chk("Certificate holder", "pass", holder))
    status = rollup(checks)
    bad = sum(c["status"] == "fail" for c in checks)
    return {"status": status, "summary": f"{len(checks) - bad}/{len(checks)} format & validity checks passed", "checks": checks, "data": data}, flags


# ============================================================================ stage 5: consistency
def consistency(sub: dict, docs: list[dict], tax_format_ok: bool):
    p, flags, checks, rows = profile(sub), [], [], []
    legal = sub.get("legal_name")
    _, form_suffix = name_parts(legal)

    def compare_name(label, doc, field, code, owner="vendor", ask=None, severity="hold", notify=None, doc_label="document"):
        if not doc:
            return
        val = doc["data"].get(field)
        if not val:
            rows.append([label, "-", "not printed", "skip"])
            return
        sc, against = best_name_score(val, sub)
        res = "pass" if sc >= config.NAME_MATCH_STRONG else "warn" if sc >= config.NAME_MATCH_CLOSE else "fail"
        rows.append([label, val, f"{sc}% vs {against}", res])
        if res == "fail":
            flags.append(flag(code, severity, owner, f"{label}: '{val}' does not match '{legal}' ({sc}%)",
                              ask=(ask or "").format(val=val, legal=legal) or None, notify=notify))
        _, suf = name_parts(val)
        if res != "fail" and suf and form_suffix and suf != form_suffix:
            rows.append([f"{label} - legal form", f"{suf}", f"form says {form_suffix}", "fail"])
            flags.append(flag("LEGAL_FORM_MISMATCH", "hold", "vendor", f"Legal form differs: form '{legal}' vs {doc_label} '{val}'",
                              ask=f"Your registration form says '{legal}' but your {doc_label} says '{val}'. '{_FORM_LABEL.get(form_suffix, form_suffix)}' and "
                                  f"'{_FORM_LABEL.get(suf, suf)}' are different legal entities - please confirm the exact legal name of the company that "
                                  "will invoice us, and make the form and documents match."))

    tf = doc_of(docs, p["tax_form"]) or doc_of(docs, "w9") or doc_of(docs, "w8bene")
    compare_name("Tax form name", tf, "legal_name", "NAME_MISMATCH", doc_label=(tf or {}).get("type_label", "tax form").replace("IRS Form ", "Form "),
                 ask="The name on your tax form ('{val}') does not match the legal name on your form ('{legal}'). Please correct whichever is wrong.")
    bl = doc_of(docs, "bank_letter")
    compare_name("Bank account holder", bl, "account_holder", "BANK_HOLDER_MISMATCH", notify=["treasury"], doc_label="bank letter",
                 ask="Your bank letter shows the account is held by '{val}', not '{legal}'. We can only pay into an account held in your company's "
                     "legal name. Please send a bank letter for an account held by {legal} - or, if you have assigned your receivables to a "
                     "factoring company, a Notice of Assignment signed by both parties (our Treasury team will verify it with the factor).")
    reg = doc_of(docs, "registration_extract")
    compare_name("Registry extract name", reg, "legal_name", "NAME_MISMATCH", doc_label="registration extract",
                 ask="The registered name on your company extract ('{val}') does not match the legal name on your form ('{legal}').")
    coi = doc_of(docs, "coi")
    if coi and coi["data"].get("legal_name"):
        sc, _ = best_name_score(coi["data"]["legal_name"], sub)
        rows.append(["Insured party (COI)", coi["data"]["legal_name"], f"{sc}%", "pass" if sc >= config.NAME_MATCH_CLOSE else "fail"])
        if sc < config.NAME_MATCH_CLOSE:
            flags.append(flag("COI_NAME_MISMATCH", "hold", "vendor", f"Insured party '{coi['data']['legal_name']}' is not the vendor",
                              ask=f"Your certificate of insurance is issued to '{coi['data']['legal_name']}', not {legal}. Please send a certificate covering {legal}."))

    # tax ID across documents
    form_tid = alnum(sub.get("tax_id"))
    for label, d in (("Tax form", tf), ("Registry extract", reg)):
        if not d or not d["data"].get("tax_id"):
            continue
        dt = alnum(d["data"]["tax_id"])
        if not tax_format_ok:
            rows.append([f"Tax ID - {label.lower()}", d["data"]["tax_id"], "form value is malformed - see format check", "warn"])
            continue
        same = dt == form_tid or dt.lstrip(p["country"]) == form_tid.lstrip(p["country"])
        rows.append([f"Tax ID - {label.lower()}", d["data"]["tax_id"], "matches form" if same else f"form says {sub.get('tax_id')}", "pass" if same else "fail"])
        if not same:
            flags.append(flag("TAX_ID_MISMATCH", "hold", "vendor", f"Tax ID on {label.lower()} ({d['data']['tax_id']}) differs from form ({sub.get('tax_id')})",
                              ask=f"The tax ID on your {label.lower()} ({d['data']['tax_id']}) differs from the one on your form ({sub.get('tax_id')}). Please confirm the correct number."))

    # bank details form <-> letter
    b = sub.get("bank") or {}
    if bl:
        bd = bl["data"]
        pairs = [("Account number", b.get("account_number"), bd.get("account_number")), ("Routing number", b.get("routing_number"), bd.get("routing_number")),
                 ("IBAN", b.get("iban"), bd.get("iban")), ("SWIFT", b.get("swift"), bd.get("swift"))]
        for lbl, fv, dv in pairs:
            if fv and dv:
                same = alnum(fv) == alnum(dv) or (lbl == "SWIFT" and alnum(fv)[:8] == alnum(dv)[:8])
                rows.append([f"{lbl} - form vs letter", dv, "matches form" if same else f"form says {fv}", "pass" if same else "fail"])
                if not same:
                    flags.append(flag("BANK_DETAILS_MISMATCH", "hold", "vendor", f"{lbl} on bank letter ({dv}) differs from form ({fv})", notify=["treasury"],
                                      ask=f"The {lbl.lower()} on your bank letter ({dv}) differs from the one you entered ({fv}). Please correct the form or send the right letter."))
    # countries
    countries = {"Entity": p["country"]}
    if b.get("iban"):
        countries["IBAN"] = alnum(b["iban"])[:2]
    if b.get("swift") and swift_valid(b["swift"])[1]:
        countries["SWIFT"] = swift_valid(b["swift"])[1]
    if b.get("country"):
        countries["Bank"] = b["country"].upper()
    if g(sub, "address.country"):
        countries["Address"] = g(sub, "address.country").upper()
    bank_cc = {v for k, v in countries.items() if k in ("IBAN", "SWIFT", "Bank")}
    differs = bank_cc and bank_cc != {p["country"]}
    rows.append(["Countries", " / ".join(f"{k} {v}" for k, v in countries.items()), "bank outside entity country" if differs else "consistent", "warn" if differs else "pass"])
    data = {"bank_country_differs": bool(differs)}
    if differs:
        flags.append(flag("BANK_COUNTRY_DIFFERS", "warn", "treasury", f"Bank in {', '.join(sorted(bank_cc))}, entity in {p['country']}",
                          action="Treasury to confirm the business reason for an offshore account before first payment."))
    # email vs website
    ed, wd = domain_of(g(sub, "contact.email")), domain_of(sub.get("website"))
    if ed in config.FREE_EMAIL_DOMAINS:
        rows.append(["Email domain", ed, "free webmail address", "warn"])
        data["free_email"] = True
        flags.append(flag("FREE_EMAIL", "warn", "masterdata", f"Contact uses a free email domain ({ed})", action="Use the phone number to confirm the contact before first payment."))
    elif ed and wd and ed != wd:
        rows.append(["Email domain", ed, f"website is {wd}", "warn"])
        data["domain_mismatch"] = True
    elif ed:
        rows.append(["Email domain", ed, "matches website" if wd else "no website given", "pass"])

    checks = [chk(r[0], r[3], f"{r[1]} - {r[2]}") for r in rows]
    status = rollup(checks)
    fails, warns = sum(r[3] == "fail" for r in rows), sum(r[3] == "warn" for r in rows)
    verdict = f"{fails} inconsistency(ies)" if fails else f"consistent, {warns} to note" if warns else "consistent"
    return {"status": status, "summary": f"{len(rows)} cross-checks - {verdict}",
            "checks": [], "tables": [{"title": "Cross-document comparison", "columns": ["Field", "Document value", "Compared with", "Result"],
                                      "rows": rows}], "data": data}, flags


# ============================================================================ stage 6: registry, duplicates, watchlist
def screening(sub: dict, docs: list[dict], registry: list[dict], vendors: list[dict], watchlist: list[dict]):
    p, flags, checks, data = profile(sub), [], [], {}
    ids = {alnum(sub.get("tax_id"))}
    for d in docs:
        for k in ("tax_id", "registration_number"):
            if (d.get("data") or {}).get(k):
                ids.add(alnum(d["data"][k]))
    ids.discard("")
    hit = next((r for r in registry if r["country"] == p["country"] and ids & {alnum(i) for i in r["ids"]}), None)
    if hit:
        via = next(i for i in hit["ids"] if alnum(i) in ids)
        sc, _ = best_name_score(hit["name"], sub)
        data["registry"] = hit
        age_y = (_days(hit.get("incorporated")) or 0) / 365.25
        data["company_age_years"] = round(age_y, 1)
        if hit["status"] != "active":
            checks.append(chk("Business registry", "fail", f"{hit['name']} - status {hit['status'].upper()} since {hit.get('dissolved', '?')}"))
            flags.append(flag("COMPANY_DISSOLVED", "reject", "compliance", f"Registry shows {hit['name']} as {hit['status']}",
                              ask=f"Our registry check shows {hit['name']} as {hit['status']}. We cannot onboard an inactive company."))
        else:
            checks.append(chk("Business registry", "pass" if sc >= config.NAME_MATCH_CLOSE else "fail",
                              f"Found via {via}: {hit['name']} - active, incorporated {hit['incorporated']} ({age_y:.1f} yrs), {hit['jurisdiction']}"))
            if sc < config.NAME_MATCH_CLOSE:
                flags.append(flag("REGISTRY_NAME_MISMATCH", "hold", "masterdata", f"Registry name '{hit['name']}' does not match '{sub.get('legal_name')}'",
                                  action="Confirm the legal entity with the sponsoring buyer before contacting the vendor."))
    else:
        checks.append(chk("Business registry", "warn", f"No registry record for {', '.join(sorted(ids)) or 'the given IDs'} in {p['country']}"))
        data["registry"] = None
        flags.append(flag("REGISTRY_NOT_FOUND", "hold", "masterdata", "Company could not be verified in the business registry",
                          action="Verify the company manually (registry portal / IRS TIN match) and record the evidence."))

    # vendor master
    form_tid = alnum(sub.get("tax_id"))
    b = sub.get("bank") or {}
    acct = {alnum(b.get("account_number")), alnum(b.get("iban"))} - {""}
    ed = domain_of(g(sub, "contact.email"))
    same_tid = next((v for v in vendors if form_tid and alnum(v.get("tax_id")) == form_tid), None)
    if same_tid:
        vb = same_tid.get("bank") or {}
        same_bank = bool(acct & ({alnum(vb.get("account_number")), alnum(vb.get("iban"))} - {""}))
        od = domain_of(g(same_tid, "contact.email"))
        dom_sc = int(fuzz.ratio(ed or "", od or ""))
        data["existing_vendor"] = {"vendor_id": same_tid["vendor_id"], "name": same_tid["name"], "status": same_tid["status"],
                                   "same_bank": same_bank, "domain_on_file": od, "domain_submitted": ed, "domain_similarity": dom_sc}
        rows = [["Tax ID", same_tid["tax_id"], sub.get("tax_id"), "same"],
                ["Bank account", "****" + (vb.get("account_number") or vb.get("iban") or "")[-4:], "****" + (b.get("account_number") or b.get("iban") or "")[-4:],
                 "same" if same_bank else "DIFFERENT"],
                ["Email domain", od, ed, "same" if ed == od else f"DIFFERENT ({dom_sc}% similar)"],
                ["Phone", g(same_tid, "contact.phone"), g(sub, "contact.phone"), "same" if re.sub(r"\D", "", g(same_tid, "contact.phone") or "") == re.sub(r"\D", "", g(sub, "contact.phone") or "") else "DIFFERENT"]]
        data["existing_vendor"]["compare"] = rows
        if same_tid["status"] == "blocked":
            checks.append(chk("Vendor master", "fail", f"Tax ID belongs to BLOCKED vendor {same_tid['vendor_id']} ({same_tid.get('block_reason')})"))
            flags.append(flag("VENDOR_BLOCKED", "reject", "compliance", f"Re-onboarding attempt by blocked vendor {same_tid['vendor_id']}: {same_tid.get('block_reason')}",
                              action="Compliance decides whether the block can be lifted; the vendor is not re-created.", confidential=True))
        elif same_bank and ed == od:
            checks.append(chk("Vendor master", "fail", f"Already onboarded as {same_tid['vendor_id']} ({same_tid['name']}) with the same bank and contact"))
            flags.append(flag("ALREADY_ONBOARDED", "reject", "masterdata", f"Duplicate - vendor already exists as {same_tid['vendor_id']}",
                              ask=f"You are already registered with us as supplier {same_tid['vendor_id']}. No new registration is needed - "
                                  "to change any details, please use the supplier change-request process."))
        else:
            diffs = [r[0].lower() for r in rows[1:] if r[3] != "same"]
            lookalike = ed != od and dom_sc >= config.LOOKALIKE_DOMAIN_SCORE
            checks.append(chk("Vendor master", "fail", f"Tax ID belongs to ACTIVE vendor {same_tid['vendor_id']} ({same_tid['name']}) - but {', '.join(diffs)} differ"
                              + (f"; '{ed}' is a look-alike of '{od}'" if lookalike else "")))
            flags.append(flag("IMPERSONATION_SUSPECTED", "reject", "security",
                              f"Submission re-uses the tax ID of active vendor {same_tid['vendor_id']} with a different {', '.join(diffs)}"
                              + (f" (look-alike domain '{ed}' vs '{od}')" if lookalike else ""),
                              action=f"Do NOT reply to {g(sub, 'contact.email')}. Call {g(same_tid, 'contact.name')} on the number on file "
                                     f"({g(same_tid, 'contact.phone')}) to ask whether they submitted this.", confidential=True, notify=["treasury"]))
            flags.append(flag("AP_ALERT", "warn", "treasury", f"Alert AP: invoices or bank-change requests for {same_tid['vendor_id']} may be fraudulent",
                              action=f"Flag {same_tid['vendor_id']} in AP - any invoice quoting bank account ****{(b.get('account_number') or b.get('iban') or '')[-4:]} must be stopped."))
    else:
        checks.append(chk("Vendor master - tax ID", "pass", "Tax ID not on file for any vendor"))
        shared = next((v for v in vendors if acct & ({alnum((v.get("bank") or {}).get("account_number")), alnum((v.get("bank") or {}).get("iban"))} - {""})), None)
        if shared:
            checks.append(chk("Vendor master - bank account", "fail", f"Bank account already belongs to {shared['vendor_id']} ({shared['name']})"))
            flags.append(flag("BANK_ACCOUNT_SHARED", "hold", "security", f"Bank account is already used by vendor {shared['vendor_id']} ({shared['name']})",
                              action="Investigate the relationship between the two vendors before any record is created.", confidential=True))
        else:
            checks.append(chk("Vendor master - bank account", "pass", "Account not used by any other vendor"))
        near = max(((name_score(sub.get("legal_name"), v["name"]), v) for v in vendors), key=lambda x: x[0], default=(0, None))
        if near[0] >= config.DUPLICATE_NAME_SCORE:
            checks.append(chk("Vendor master - name", "warn", f"{near[0]}% similar to {near[1]['vendor_id']} {near[1]['name']} (different tax ID)"))
            flags.append(flag("POSSIBLE_DUPLICATE_VENDOR", "hold", "masterdata", f"Name {near[0]}% similar to existing {near[1]['vendor_id']} {near[1]['name']}",
                              action="Confirm with the buyer whether this is a related entity or the same supplier."))
        else:
            checks.append(chk("Vendor master - name", "pass", f"No similar vendor (closest {near[0]}%{' - ' + near[1]['name'] if near[1] else ''})"))

    # watchlist
    names = [n for n in (sub.get("legal_name"), sub.get("dba")) if n]
    best = (0, None, None)
    for e in watchlist:
        for wn in [e["name"]] + e.get("aliases", []):
            for n in names:
                sc = name_score(n, wn)
                if sc > best[0]:
                    best = (sc, e, wn)
    data["watchlist_best"] = best[0]
    if best[1] and best[0] >= config.WATCHLIST_POTENTIAL:
        e = best[1]
        same_cc = e["country"] == p["country"]
        detail = f"{best[0]}% match to {e['entry_id']} '{best[2]}' ({e['country']}, {e['list']}: {e['program']}, listed {e['listed']})"
        data["watchlist_hit"] = {**e, "score": best[0], "matched_name": best[2], "same_country": same_cc}
        if best[0] >= config.WATCHLIST_STRONG and same_cc:
            checks.append(chk("Watchlist screening", "fail", "STRONG " + detail))
            flags.append(flag("WATCHLIST_MATCH", "reject", "compliance", "Strong watchlist match: " + detail,
                              action="Compliance to confirm and file; no business may be conducted.", confidential=True))
        else:
            checks.append(chk("Watchlist screening", "fail", "POTENTIAL " + detail))
            flags.append(flag("WATCHLIST_POTENTIAL", "hold", "compliance", "Potential watchlist match: " + detail,
                              action="Analyst to disposition the hit (compare address, ownership, registration numbers) and record the evidence. "
                                     "Do not tell the vendor the reason for the delay.", confidential=True))
    else:
        checks.append(chk("Watchlist screening", "pass", f"No match above {config.WATCHLIST_POTENTIAL}% across {len(watchlist)} entries (best {best[0]}%)"))
    status = rollup(checks)
    mark = {"pass": "✓", "warn": "⚠", "fail": "✗"}
    groups = [("Registry", [c for c in checks if c["name"] == "Business registry"]),
              ("Vendor master", [c for c in checks if c["name"].startswith("Vendor master")]),
              ("Watchlist", [c for c in checks if c["name"] == "Watchlist screening"])]
    summary = " · ".join(f"{n} {mark[rollup(cs)]}" for n, cs in groups if cs)
    return {"status": status, "summary": summary, "checks": checks, "data": data,
            "tables": [{"title": f"Submitted vs vendor on file ({data['existing_vendor']['vendor_id']})", "columns": ["Field", "On file", "Submitted", "Result"],
                        "rows": data["existing_vendor"]["compare"]}] if data.get("existing_vendor") else []}, flags


# ============================================================================ stage 7: risk
def risk(sub: dict, ctx: dict, flags_so_far: list[dict]):
    p = profile(sub)
    cons, scr = ctx.get("consistency", {}), ctx.get("screening", {})
    factors = []
    age = scr.get("company_age_years")
    if age is not None and age < 1:
        factors.append(("Company incorporated < 1 year ago", 20))
    elif age is not None and age < 3:
        factors.append((f"Young company ({age:.1f} years)", 10))
    if scr.get("registry") is None:
        factors.append(("Not verified in the business registry", 15))
    if not p["us"]:
        factors.append((f"Foreign entity ({COUNTRY_NAMES.get(p['country'], p['country'])})", 10))
    if cons.get("bank_country_differs"):
        factors.append(("Bank account outside the entity's country", 25))
    if cons.get("free_email"):
        factors.append(("Free webmail contact address", 15))
    elif cons.get("domain_mismatch"):
        factors.append(("Email domain differs from website", 10))
    spend = sub.get("expected_annual_spend") or 0
    if spend > 1_000_000:
        factors.append((f"Expected spend {money(spend)}/yr", 20))
    elif spend > 250_000:
        factors.append((f"Expected spend {money(spend)}/yr", 10))
    if p["onsite"]:
        factors.append(("On-site access to Brightline facilities", 5))
    if any(f["code"] == "WATCHLIST_POTENTIAL" for f in flags_so_far):
        factors.append(("Unresolved watchlist potential match", 30))
    score = sum(pts for _, pts in factors)
    tier = "high" if score >= config.RISK_HIGH else "medium" if score >= config.RISK_MEDIUM else "low"
    flags = []
    conditions = []
    if tier == "high" and not any(f["owner"] == "compliance" and f["severity"] != "warn" for f in flags_so_far):
        flags.append(flag("HIGH_RISK", "hold", "compliance", f"Risk score {score} (high)", action="Enhanced due diligence: ownership, references, site visit if on-site."))
    if tier == "medium":
        conditions = ["Call back the contact on an independently sourced number before the first payment",
                      "Re-review the vendor after 90 days or the first 3 invoices"]
        flags.append(flag("RISK_MEDIUM", "warn", "masterdata", f"Risk score {score} (medium) - approve with conditions", action=conditions[0]))
    rows = [[f, f"+{pts}"] for f, pts in factors] or [["No risk factors", "0"]]
    return {"status": {"low": "pass", "medium": "warn", "high": "fail"}[tier],
            "summary": f"Risk score {score} - {tier.upper()}" + (" - approve with conditions" if tier == "medium" else ""),
            "kv": [{"label": "Score", "value": f"{score} (low < {config.RISK_MEDIUM} ≤ medium < {config.RISK_HIGH} ≤ high)"},
                   {"label": "Approval path", "value": {"low": "Standard - no human needed if all checks pass",
                                                         "medium": "Approve with conditions", "high": "Compliance enhanced due diligence"}[tier]}],
            "tables": [{"title": "Risk factors", "columns": ["Factor", "Points"], "rows": rows}], "checks": [],
            "data": {"score": score, "tier": tier, "factors": factors, "conditions": conditions}}, flags


# ============================================================================ stage 8: decision
def decide(flags: list[dict]) -> dict:
    rejects = [f for f in flags if f["severity"] == "reject"]
    holds = [f for f in flags if f["severity"] == "hold"]
    warns = [f for f in flags if f["severity"] == "warn"]
    blocking = rejects or holds
    if rejects:
        decision = "REJECTED"
    elif holds:
        decision = "PENDING"
    else:
        decision = "APPROVED"
    owners = {f["owner"] for f in blocking}
    owner = next((o for o in OWNER_PRIORITY if o in owners), "masterdata")
    notify = sorted({n for f in blocking for n in f.get("notify", [])} | owners - {owner})
    pending_with = None
    if decision == "PENDING":
        pending_with = "vendor" if owners == {"vendor"} else "internal"
    return {"decision": decision, "owner": owner, "route": OWNERS[owner] if blocking else "Vendor master - record created",
            "also_notify": [OWNERS[n] for n in notify if n in OWNERS], "pending_with": pending_with,
            "blocking": rejects + holds, "warnings": warns}


def vendor_message(d: dict, sub: dict, flags: list[dict], case_ref: str, vendor_id: str | None = None, resub: dict | None = None) -> dict | None:
    contact = g(sub, "contact.name") or "Supplier team"
    first = contact.split()[0] if contact else "there"
    to = g(sub, "contact.email") or "(no email on submission)"
    legal = sub.get("legal_name") or "your company"
    sig = f"\n\nKind regards,\nSupplier Onboarding\n{config.BUYER_NAME}\nRef: {case_ref}"
    if any(f["confidential"] and f["severity"] == "reject" and f["code"] in ("IMPERSONATION_SUSPECTED",) for f in flags):
        return None  # never reply into a channel we believe is fraudulent
    if d["decision"] == "APPROVED":
        return {"to": to, "subject": f"Welcome - {legal} is now an approved Brightline supplier ({vendor_id})",
                "body": f"Hi {first},\n\nGood news - your supplier registration is approved. Your supplier number is {vendor_id}.\n\n"
                        f"When you invoice us, please:\n  1. Quote your supplier number {vendor_id} and our purchase order number.\n"
                        f"  2. Send invoices as PDF to ap@brightline.example.\n  3. Payment terms are Net 30 to the bank account you registered.\n\n"
                        f"Any future change to your bank details must come through the supplier change-request process and will be verified by phone."
                        + sig}
    asks = [f["ask"] for f in d["blocking"] if f.get("ask") and not f["confidential"]]
    confidential_open = any(f["confidential"] for f in d["blocking"])
    if d["decision"] == "REJECTED":
        if any(f["code"] == "ALREADY_ONBOARDED" for f in d["blocking"]):
            return {"to": to, "subject": f"Your supplier registration - already on file ({case_ref})",
                    "body": f"Hi {first},\n\nThank you for registering. " + asks[0] + sig}
        if confidential_open:
            return {"to": to, "subject": f"Your supplier registration with {config.BUYER_NAME} ({case_ref})",
                    "body": f"Hi {first},\n\nThank you for your interest in supplying {config.BUYER_NAME}. After review we are unable to "
                            f"proceed with your registration at this time.{sig}"}
        return {"to": to, "subject": f"Your supplier registration with {config.BUYER_NAME} ({case_ref})",
                "body": f"Hi {first},\n\nThank you for registering. Unfortunately we cannot proceed:\n\n" +
                        "\n".join(f"  - {a}" for a in asks) + sig}
    # PENDING
    intro = f"Hi {first},\n\nThank you for submitting {legal}'s supplier registration."
    if resub and resub.get("resolved"):
        intro += f" Thanks for the updates - {len(resub['resolved'])} of the earlier points are now resolved."
    if asks:
        body = intro + (" Before we can set you up, we need the following:\n\n" if not resub else " We still need:\n\n")
        body += "\n".join(f"  {i}. {a}" for i, a in enumerate(asks, 1))
        body += ("\n\nReply to this email with the corrected information and documents - everything else in your registration is "
                 "complete, so there is no need to resend it.")
        if confidential_open:
            body += " Some checks are still running on our side; we will confirm once they are complete."
        return {"to": to, "subject": f"Action needed: your supplier registration with {config.BUYER_NAME} ({case_ref})", "body": body + sig}
    return {"to": to, "subject": f"Your supplier registration with {config.BUYER_NAME} is under review ({case_ref})",
            "body": intro + " Your documents are complete and your registration is now in our standard review. "
                    "There is nothing you need to do - we expect to confirm within 3 business days." + sig}


def vendor_record(sub: dict, docs: list[dict], risk_data: dict, run_id: str) -> dict:
    b = sub.get("bank") or {}
    return {"name": sub.get("legal_name"), "dba": sub.get("dba"), "tax_id": sub.get("tax_id"), "country": sub.get("country"),
            "status": "active", "since": config.today().isoformat(), "payment_terms": "Net 30", "risk_tier": risk_data.get("tier"),
            "conditions": risk_data.get("conditions", []), "category": sub.get("category"),
            "address": ", ".join(x for x in [g(sub, "address.line1"), g(sub, "address.city"), g(sub, "address.region"), g(sub, "address.postal_code")] if x),
            "bank": {"account_holder": b.get("account_holder"), "bank_name": b.get("bank_name"), "account_number": b.get("account_number"),
                     "routing_number": b.get("routing_number"), "iban": b.get("iban"), "swift": b.get("swift")},
            "contact": {"name": g(sub, "contact.name"), "phone": g(sub, "contact.phone"), "email": g(sub, "contact.email")},
            "documents": [d["filename"] for d in docs if d.get("readable") and d["doc_type"] not in ("invoice", "other")],
            "onboarding_run": run_id}
