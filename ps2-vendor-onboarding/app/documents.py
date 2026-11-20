"""Document reading: text layer vs scan -> classify (what is this document really?) -> extract fields.

Two engines produce the *same* document dict:
  * claude - Claude reads layout text (digital PDFs) or the page images (scans) and returns JSON
             constrained by a schema (structured outputs). It only reads; it never decides.
  * rules  - deterministic keyword classifier + label/value parser over the text layer. Zero-cost
             fallback when no API key is configured. Cannot read scans.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from pathlib import Path

import pymupdf

from . import config

DOC_TYPES = {
    "w9": "IRS Form W-9",
    "w8bene": "IRS Form W-8BEN-E",
    "bank_letter": "Bank verification letter",
    "coi": "Certificate of insurance",
    "registration_extract": "Company registration extract",
    "invoice": "Invoice",
    "other": "Other / unrecognised",
}

# =============================================================================== reading


def read_pdf(pdf_path: Path, preview_path: Path) -> dict:
    doc = pymupdf.open(pdf_path)
    text = "\n\n".join(_layout_text(p) for p in doc).strip()
    scanned = len(re.sub(r"\s", "", text)) < config.SCANNED_TEXT_THRESHOLD
    doc[0].get_pixmap(dpi=150 if scanned else 90).save(preview_path)
    images = [p.get_pixmap(dpi=150).tobytes("png") for p in list(doc)[:3]] if scanned else []
    return {"pages": doc.page_count, "text": text, "chars": len(text), "scanned": scanned, "images": images}


def _layout_text(page) -> str:
    """Rebuild visual rows from word boxes so label/value pairs stay on one line."""
    words = page.get_text("words")
    rows: list[list] = []
    for w in sorted(words, key=lambda w: (round(w[3]), w[0])):
        if rows and abs(rows[-1][0][3] - w[3]) <= 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    lines = []
    for r in rows:
        r.sort(key=lambda w: w[0])
        out, last = "", None
        for w in r:
            if last is not None:
                out += "   " if w[0] - last > 12 else " "
            out += w[4]
            last = w[2]
        lines.append(out)
    return "\n".join(lines)


# =============================================================================== schema
_S = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_N = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_B = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
_D = {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "ISO YYYY-MM-DD"}

DOC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
        "issuer": {**_S, "description": "Who issued the document (bank, insurer, registry court, the vendor itself)"},
        "legal_name": {**_S, "description": "Legal name of the company the document is about (W-9 line 1, insured party, registered name, invoicing party)"},
        "dba_name": {**_S, "description": "Trade / DBA / disregarded-entity name if printed (W-9 line 2)"},
        "entity_type": {**_S, "description": "LLC, C corporation, GmbH, FZE, ... as printed"},
        "country": {**_S, "description": "ISO-3166 alpha-2 country of the company"},
        "address": _S,
        "tax_id": {**_S, "description": "EIN / VAT ID / TRN exactly as printed"},
        "registration_number": {**_S, "description": "Company register / licence number, e.g. 'HRB 734211'"},
        "incorporation_date": _D,
        "signed": {**_B, "description": "True only if a signature (or /s/ signature) is present on the signature line"},
        "signature_date": _D,
        "account_holder": {**_S, "description": "Bank letter: name the account is held in"},
        "bank_name": _S,
        "account_number": _S,
        "routing_number": {**_S, "description": "US ABA routing number"},
        "iban": _S,
        "swift": _S,
        "document_date": {**_D, "description": "Date the document was issued"},
        "insurer": _S,
        "policy_number": _S,
        "general_liability_limit": {**_N, "description": "Per-occurrence general liability limit, plain number"},
        "policy_effective": _D,
        "policy_expiry": _D,
        "certificate_holder": _S,
        "invoice_number": _S,
        "invoice_total": _N,
        "confidence": {"type": "number", "description": "0-1, confidence that every extracted value is correct"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}
DOC_SCHEMA["required"] = list(DOC_SCHEMA["properties"].keys())

SYSTEM_PROMPT = f"""You are the document-reading step of a vendor-onboarding pipeline at {config.BUYER_NAME} (the buyer).
A prospective supplier attached this document to its registration. First decide what the document actually is
(doc_type) - suppliers often attach the wrong file, so classify by content, never by file name. Then transcribe
the fields that apply to that document type.
Downstream code checks every value against policy and makes the decision, so faithful transcription matters more than completeness:
- Use null for anything not printed or not applicable. Never infer or invent a value.
- Copy tax IDs, registration numbers, account numbers, routing numbers, IBANs and SWIFT codes character-for-character, including any spacing or typos.
- signed is true only if the signature line actually carries a signature; a blank line is false.
- Dates as ISO YYYY-MM-DD. Money as plain numbers.
- Mention anything unusual (stamps, handwritten changes, 'sample' watermarks, illegible areas) in notes.
- Set confidence below 0.7 if any key value is hard to read.
"""


# =============================================================================== claude engine
def extract_with_claude(reading: dict, filename: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    if reading["scanned"]:
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.standard_b64encode(img).decode()}}
                   for img in reading["images"]]
        content.append({"type": "text", "text": f"Attachment '{filename}' is a scanned image (no text layer). Classify it and extract its fields."})
        effort = config.EFFORT_SCANNED
    else:
        content = [{"type": "text", "text": f"Attachment '{filename}' - text layer, layout preserved:\n\n"
                    f"<document>\n{reading['text']}\n</document>\n\nClassify it and extract its fields."}]
        effort = config.EFFORT_DIGITAL
    kwargs = dict(model=config.CLAUDE_MODEL, max_tokens=16000, system=SYSTEM_PROMPT,
                  messages=[{"role": "user", "content": content}],
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": DOC_SCHEMA}})
    try:
        # server-side refusal fallback: if the primary model declines, the API retries on a fallback model
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("Model declined to read this document")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Extraction output was truncated")
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    data["_meta"] = {"engine": "claude", "model": getattr(resp, "model", config.CLAUDE_MODEL), "effort": effort,
                     "mode": "vision" if reading["scanned"] else "text",
                     "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return data


# =============================================================================== rules engine
def parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().rstrip(".,")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(s):
    try:
        return float(re.sub(r"[^\d.]", "", s)) if s else None
    except ValueError:
        return None


def classify_rules(t: str) -> str:
    u = t.upper()
    if "W-8BEN-E" in u:
        return "w8bene"
    if re.search(r"FORM\s+W-9", u):
        return "w9"
    if "CERTIFICATE OF LIABILITY INSURANCE" in u or "CERTIFICATE OF INSURANCE" in u:
        return "coi"
    if "HANDELSREGISTER" in u or "CERTIFICATE OF INCORPORATION" in u or "COMMERCIAL LICENCE" in u or "COMMERCIAL LICENSE" in u:
        return "registration_extract"
    if re.search(r"\bINVOICE\b", u) and re.search(r"AMOUNT DUE|TOTAL DUE|BALANCE DUE", u):
        return "invoice"
    if re.search(r"ACCOUNT (VERIFICATION|CONFIRMATION)|BANK (CONFIRMATION|VERIFICATION) LETTER|CONFIRM THAT .* (HOLDS|MAINTAINS)", u):
        return "bank_letter"
    return "other"


def extract_with_rules(reading: dict, filename: str) -> dict:
    if reading["scanned"]:
        raise RuntimeError("Document has no text layer; the rule-based engine cannot read scans")
    t = reading["text"]

    def f(label: str, value: str = r"([^\n]+?)") -> str | None:
        m = re.search(r"(?im)^\s*(?:\d{1,2}[a-z]?\s+)?(?:" + label + r")\s*[:#]\s*" + value + r"\s*(?:\s{3,}|$)", t)
        return m.group(1).strip() if m else None

    doc_type = classify_rules(t)
    sig = f(r"Signature(?: of [^:]+)?")
    signed = None
    if doc_type in ("w9", "w8bene"):
        signed = bool(sig and re.search(r"[A-Za-z]{2,}", sig.replace("_", "")))
    country = f(r"Country(?: of (?:incorporation|organization))?")
    if country:
        country = {"united states": "US", "usa": "US", "germany": "DE", "deutschland": "DE",
                   "united arab emirates": "AE", "uae": "AE"}.get(country.lower().split("(")[0].strip(), country[:2].upper())
    gl = f(r"General liability[^:\n]*(?:limit|each occurrence)", r"\$?\s*([\d,]+)")
    out = {
        "doc_type": doc_type,
        "issuer": f(r"Issued by|Issuer|Insurer") or (t.strip().splitlines()[0].split("   ")[0] if t.strip() else None),
        "legal_name": f(r"Name(?: \(as shown on your income tax return\))?|Name of organization|Legal name|Firma|Registered name|Licensee|Insured|Account title|From"),
        "dba_name": f(r"Business name/disregarded entity name|DBA|Trade name"),
        "entity_type": f(r"Federal tax classification|Entity type|Chapter 3 status|Rechtsform|Legal form"),
        "country": country,
        "address": f(r"Address|Registered (?:office|address)|Sitz"),
        "tax_id": f(r"Employer identification number|EIN|Foreign TIN|U\.?S\.? TIN|VAT ID|USt-IdNr\.?|Tax registration number|TRN|Tax ID"),
        "registration_number": f(r"Register number|Registernummer|Licence number|License number|Registration number"),
        "incorporation_date": parse_date(f(r"Date of incorporation|Incorporated|Eintragung|Date of registration")),
        "signed": signed,
        "signature_date": parse_date(f(r"Date(?: signed)?")) if doc_type in ("w9", "w8bene") else None,
        "account_holder": f(r"Account holder|Account name|Account title"),
        "bank_name": f(r"Bank name|Bank"),
        "account_number": f(r"Account number|Account no\.?"),
        "routing_number": f(r"Routing number|ABA routing number|ABA"),
        "iban": f(r"IBAN"),
        "swift": f(r"SWIFT(?:/BIC)?|BIC"),
        "document_date": parse_date(f(r"Letter date|Date issued|Issue date|Date of issue|Abruf vom|Certificate date|Invoice date|Date")),
        "insurer": f(r"Insurer A|Insurer"),
        "policy_number": f(r"Policy number"),
        "general_liability_limit": _num(gl),
        "policy_effective": parse_date(f(r"Policy effective|Effective")),
        "policy_expiry": parse_date(f(r"Policy expiration|Policy expiry|Expiration|Expires")),
        "certificate_holder": f(r"Certificate holder"),
        "invoice_number": f(r"Invoice number|Invoice #|Invoice no\.?"),
        "invoice_total": _num(f(r"Amount due|Total due|Balance due", r"\$?\s*([\d,]+\.\d{2})")),
        "confidence": 0.9 if doc_type != "other" else 0.6,
        "notes": [],
        "_meta": {"engine": "rules", "mode": "text"},
    }
    if doc_type == "invoice" and not out["legal_name"]:
        out["legal_name"] = t.strip().splitlines()[0].split("   ")[0]
    return out
