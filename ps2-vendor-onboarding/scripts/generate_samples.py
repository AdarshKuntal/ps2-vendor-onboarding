"""Generate the demo scenarios in samples/<scenario>/: a submission.json (the vendor's form) + PDF attachments.

Every document type has its own layout, the way real onboarding packs arrive: a tax form, a letter on bank
letterhead, an insurance certificate, a registry extract. One certificate is rasterised, skewed and noised
into an image-only "scan" (no text layer) to exercise the vision path.

Run:  python scripts/generate_samples.py
"""
from __future__ import annotations

import io
import json
import random
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
W, H = LETTER
BUYER = ("Brightline Manufacturing Inc.", "2200 Foundry Road, Columbus, OH 43215")
FOOT = "Generated test document - fictional data for a demo of an automated onboarding process."


def _foot(c):
    c.setFont("Helvetica-Oblique", 7)
    c.setFillColor(colors.HexColor("#888888"))
    c.drawCentredString(W / 2, 22, FOOT)
    c.setFillColor(colors.black)


def _kv(c, x, y, rows, label_w=190, size=10, gap=17, bold_labels=True):
    for lab, val in rows:
        c.setFont("Helvetica-Bold" if bold_labels else "Helvetica", size)
        c.drawString(x, y, f"{lab}:")
        c.setFont("Helvetica", size)
        c.drawString(x + label_w, y, str(val))
        y -= gap
    return y


# ------------------------------------------------------------------------- document layouts
def w9(c, d):
    c.setLineWidth(1.2)
    c.rect(36, H - 100, W - 72, 64)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(46, H - 70, "Form W-9")
    c.setFont("Helvetica", 8)
    c.drawString(46, H - 84, "(Rev. March 2024)   Department of the Treasury - Internal Revenue Service")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(200, H - 62, "Request for Taxpayer")
    c.drawString(200, H - 76, "Identification Number and Certification")
    c.setFont("Helvetica", 8)
    c.drawRightString(W - 46, H - 62, "Give form to the requester.")
    c.drawRightString(W - 46, H - 74, "Do not send to the IRS.")
    y = H - 132
    rows = [("1  Name (as shown on your income tax return)", d["name"]),
            ("2  Business name/disregarded entity name", d.get("dba") or ""),
            ("3a Federal tax classification", d["classification"]),
            ("5  Address", d["address"]), ("6  City, state, ZIP code", d["city"])]
    for lab, val in rows:
        c.setFont("Helvetica", 7.5)
        c.drawString(46, y + 11, lab.split(None, 1)[0])
        c.setFont("Helvetica-Bold", 9)
        c.drawString(62, y, lab.split(None, 1)[1].strip() + ":")
        c.setFont("Helvetica", 10.5)
        c.drawString(300, y, val)
        c.setStrokeColor(colors.HexColor("#BBBBBB"))
        c.line(46, y - 6, W - 46, y - 6)
        c.setStrokeColor(colors.black)
        y -= 34
    c.setFillColor(colors.HexColor("#EEEEEE"))
    c.rect(36, y - 6, W - 72, 18, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Part I     Taxpayer Identification Number (TIN)")
    y -= 30
    c.setFont("Helvetica-Bold", 10)
    c.drawString(62, y, "Employer identification number:")
    c.setFont("Courier-Bold", 13)
    c.drawString(300, y, d.get("ein") or "")
    y -= 36
    c.setFillColor(colors.HexColor("#EEEEEE"))
    c.rect(36, y - 6, W - 72, 18, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Part II     Certification")
    y -= 22
    c.setFont("Helvetica", 8)
    for ln in ["Under penalties of perjury, I certify that: 1. The number shown on this form is my correct taxpayer identification number;",
               "2. I am not subject to backup withholding; 3. I am a U.S. citizen or other U.S. person; and 4. The FATCA code(s) entered",
               "on this form (if any) indicating that I am exempt from FATCA reporting is correct."]:
        c.drawString(46, y, ln)
        y -= 11
    y -= 18
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Signature of U.S. person:")
    if d.get("signer"):
        c.setFont("Times-Italic", 15)
        c.drawString(200, y, f"/s/ {d['signer']}")
    else:
        c.setFont("Helvetica", 10)
        c.drawString(200, y, "______________________________")
    y -= 24
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Date:")
    c.setFont("Helvetica", 10)
    c.drawString(200, y, d.get("date") or "")
    _foot(c)


def w8bene(c, d):
    c.setLineWidth(1.2)
    c.rect(36, H - 104, W - 72, 68)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(46, H - 66, "Form W-8BEN-E")
    c.setFont("Helvetica", 8)
    c.drawString(46, H - 80, "(Rev. October 2021)  Department of the Treasury - Internal Revenue Service")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(250, H - 58, "Certificate of Status of Beneficial Owner for")
    c.drawString(250, H - 71, "United States Tax Withholding and Reporting (Entities)")
    c.setFont("Helvetica", 8)
    c.drawString(250, H - 86, "For use by entities. Individuals must use Form W-8BEN.")
    y = H - 134
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Part I     Identification of Beneficial Owner")
    y -= 28
    for lab, val in [("1  Name of organization", d["name"]), ("2  Country of incorporation", d["country"]),
                     ("4  Chapter 3 status", d["status"]), ("6  Address", d["address"]), ("9b Foreign TIN", d["tin"])]:
        c.setFont("Helvetica", 7.5)
        c.drawString(46, y + 11, lab.split("  ", 1)[0].strip())
        c.setFont("Helvetica-Bold", 9)
        c.drawString(62, y, lab.split(" ", 1)[1].strip() + ":")
        c.setFont("Helvetica", 10.5)
        c.drawString(300, y, val)
        c.setStrokeColor(colors.HexColor("#BBBBBB"))
        c.line(46, y - 6, W - 46, y - 6)
        c.setStrokeColor(colors.black)
        y -= 34
    y -= 10
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Part XXX     Certification")
    y -= 18
    c.setFont("Helvetica", 8)
    c.drawString(46, y, "Under penalties of perjury, I declare that I have examined the information on this form and to the best of my knowledge it is true.")
    y -= 34
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Signature of individual authorized to sign:")
    c.setFont("Times-Italic", 15)
    c.drawString(290, y, f"/s/ {d['signer']}")
    y -= 24
    c.setFont("Helvetica-Bold", 10)
    c.drawString(46, y, "Date:")
    c.setFont("Helvetica", 10)
    c.drawString(290, y, d["date"])
    _foot(c)


def bank_letter(c, d):
    accent = colors.HexColor(d.get("color", "#0B3D2E"))
    c.setFillColor(accent)
    c.rect(0, H - 8, W, 8, stroke=0, fill=1)
    c.setFont("Times-Bold", 22)
    c.drawString(54, H - 60, d["bank"].upper())
    c.setFillColor(colors.HexColor("#555555"))
    c.setFont("Helvetica", 8.5)
    c.drawString(54, H - 76, d["bank_address"])
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    y = H - 120
    c.drawString(54, y, f"Letter date: {d['date']}")
    y -= 30
    c.drawString(54, y, "To whom it may concern,")
    y -= 24
    c.setFont("Helvetica-Bold", 11)
    c.drawString(54, y, "Re: Account verification")
    y -= 24
    c.setFont("Helvetica", 10)
    c.drawString(54, y, f"We confirm that the following account is maintained with {d['bank']} and is in good standing.")
    y -= 14
    c.drawString(54, y, "This letter is issued at the request of the account holder for supplier-registration purposes.")
    y -= 30
    rows = [("Account holder", d["holder"]), ("Bank name", d["bank"])]
    if d.get("iban"):
        rows += [("IBAN", d["iban"]), ("SWIFT/BIC", d["swift"])]
    else:
        rows += [("Account number", d["account"]), ("ABA routing number", d["routing"])]
    rows += [("Account type", d.get("type", "Business current account")), ("Currency", d.get("currency", "USD"))]
    c.setStrokeColor(colors.HexColor("#CCCCCC"))
    c.rect(48, y - len(rows) * 18 + 6, W - 96, len(rows) * 18 + 12)
    y = _kv(c, 60, y, rows, label_w=160, gap=18)
    y -= 34
    c.drawString(54, y, "Yours faithfully,")
    y -= 28
    c.setFont("Times-Italic", 14)
    c.drawString(54, y, d["officer"])
    y -= 16
    c.setFont("Helvetica", 9)
    c.drawString(54, y, d["officer_title"])
    _foot(c)


def coi(c, d):
    c.setLineWidth(1)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(40, H - 50, "CERTIFICATE OF LIABILITY INSURANCE")
    c.setFont("Helvetica", 7.5)
    c.drawString(40, H - 64, "This certificate is issued as a matter of information only and confers no rights upon the certificate holder.")
    c.setFont("Helvetica", 9.5)
    y = H - 92
    c.drawString(40, y, f"Certificate date: {d['cert_date']}")
    y -= 26
    c.rect(36, y - 60, W - 72, 74)
    y = _kv(c, 46, y, [("Producer", d["producer"]), ("Insured", d["insured"]), ("Address", d["address"]), ("Insurer A", d["insurer"])],
            label_w=110, size=9.5, gap=16)
    y -= 26
    c.setFillColor(colors.HexColor("#E8E8E8"))
    c.rect(36, y - 4, W - 72, 16, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(46, y, "COVERAGES")
    y -= 26
    y = _kv(c, 46, y, [("Type of insurance", "Commercial general liability - occurrence"), ("Policy number", d["policy"]),
                       ("Policy effective", d["eff"]), ("Policy expiration", d["exp"]),
                       ("General liability - each occurrence", d["gl"]), ("General aggregate", d["agg"]),
                       ("Workers compensation", d.get("wc", "Statutory limits"))], label_w=200, size=9.5, gap=17)
    y -= 26
    c.rect(36, y - 40, 300, 56)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(46, y, "Certificate holder:")
    c.setFont("Helvetica", 9.5)
    c.drawString(146, y, d["holder"])
    c.drawString(146, y - 14, d.get("holder_address", BUYER[1]))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(360, y, "Authorized representative")
    c.setFont("Times-Italic", 13)
    c.drawString(360, y - 18, d["rep"])
    _foot(c)


def handelsregister(c, d):
    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, H - 54, "Handelsregister B des Amtsgerichts Stuttgart")
    c.setFont("Helvetica", 9)
    c.drawString(50, H - 68, "Aktueller Ausdruck / Current printout - Commercial register extract")
    y = H - 104
    y = _kv(c, 50, y, [("Abruf vom", d["date"]), ("Registernummer", d["hrb"]), ("Firma", d["name"]), ("Sitz", d["address"]),
                       ("Rechtsform", "Gesellschaft mit beschränkter Haftung (GmbH)"), ("Stammkapital", "250.000,00 EUR"),
                       ("Eintragung", d["inc"]), ("Geschäftsführer", d["md"])], label_w=140, gap=20)
    y -= 16
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Steuerliche Angaben (vom Unternehmen ergänzt) / Tax details supplied by the company")
    y -= 24
    _kv(c, 50, y, [("USt-IdNr.", d["vat"]), ("Country", "Germany")], label_w=140, gap=20)
    _foot(c)


def trade_licence(c, d):
    c.setFillColor(colors.HexColor("#7A5C1E"))
    c.rect(30, 30, W - 60, H - 60, stroke=1, fill=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(W / 2, H - 80, "COMMERCIAL LICENCE")
    c.setFont("Helvetica", 9)
    c.drawCentredString(W / 2, H - 96, "Issued by the free-zone licensing authority, Dubai, United Arab Emirates")
    y = H - 140
    _kv(c, 70, y, [("Licence number", d["lic"]), ("Licensee", d["name"]), ("Legal form", "Free Zone Establishment (FZE)"),
                   ("Address", d["address"]), ("Country", "United Arab Emirates"), ("Date of registration", d["inc"]),
                   ("Tax registration number", d["trn"]), ("Issue date", d["issue"]), ("Licensed activities", d["activities"]),
                   ("Manager", d["manager"])], label_w=170, gap=22)
    _foot(c)


def invoice(c, d):
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, H - 60, d["vendor"].upper())
    c.setFont("Helvetica", 9)
    c.drawString(50, H - 74, d["vendor_address"])
    c.setFont("Helvetica-Bold", 24)
    c.drawRightString(W - 50, H - 62, "INVOICE")
    y = H - 120
    y = _kv(c, 50, y, [("Invoice number", d["number"]), ("Invoice date", d["date"]), ("Bill to", BUYER[0])], label_w=110)
    y -= 20
    c.setFillColor(colors.HexColor("#EEF3F8"))
    c.rect(46, y - 4, W - 92, 18, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(52, y, "Description")
    c.drawRightString(W - 52, y, "Amount")
    y -= 22
    c.setFont("Helvetica", 9.5)
    for desc, amt in d["lines"]:
        c.drawString(52, y, desc)
        c.drawRightString(W - 52, y, f"${amt:,.2f}")
        y -= 16
    y -= 14
    c.setFont("Helvetica-Bold", 11)
    c.drawString(W - 260, y, f"Amount due:   ${sum(a for _, a in d['lines']):,.2f}")
    y -= 40
    c.setFont("Helvetica", 9)
    c.drawString(50, y, d["remit"])
    _foot(c)


# ------------------------------------------------------------------------- pdf helpers
def make_pdf(path: Path, fn, data):
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setTitle(path.stem)
    c.setAuthor("demo-generator")
    fn(c, data)
    c.showPage()
    c.save()


def to_scan(path: Path, seed=7):
    """Rasterise to a skewed, noisy, image-only PDF (no text layer)."""
    rnd = random.Random(seed)
    src = fitz.open(path)
    pix = src[0].get_pixmap(dpi=130)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    img = img.rotate(-1.1, expand=True, fillcolor=245)
    px = img.load()
    for _ in range(int(img.width * img.height * 0.012)):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.choice([90, 140, 200])
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=55)
    out = fitz.open()
    page = out.new_page(width=W, height=H)
    page.insert_image(page.rect, stream=buf.getvalue())
    src.close()
    out.save(path)


# ------------------------------------------------------------------------- scenarios
def base(**kw):
    s = {"legal_name": None, "dba": None, "entity_type": None, "country": None, "tax_id": None,
         "address": {"line1": None, "city": None, "region": None, "postal_code": None, "country": None},
         "website": None, "contact": {"name": None, "title": None, "email": None, "phone": None},
         "category": None, "description": None, "expected_annual_spend": None, "currency": "USD", "requested_by": None,
         "bank": {"account_holder": None, "bank_name": None, "country": None, "account_number": None, "routing_number": None,
                  "iban": None, "swift": None},
         "attestations": {"info_accurate": True, "no_conflict_of_interest": True}}
    for k, v in kw.items():
        s[k] = {**s[k], **v} if isinstance(v, dict) and isinstance(s.get(k), dict) else v
    return s


KESTREL_W9 = dict(name="Kestrel Freight Partners LLC", dba="Kestrel Freight", classification="Limited liability company - C corporation",
                  address="2875 Rickenbacker Pkwy", city="Columbus, OH 43217", ein="84-3920175", signer="Maya Okafor", date="11/02/2026")

SCENARIOS = [
    {
        "id": "01_happy_kestrel", "tag": "happy", "title": "Happy path - Kestrel Freight Partners",
        "description": "Clean US carrier for on-site yard logistics. W-9, bank letter, insurance certificate. The bank letter writes the "
                       "name as 'KESTREL FREIGHT PARTNERS, L.L.C.' and the insurer uses the DBA - names never match character for character.",
        "expected": "APPROVED - vendor record created",
        "submission": base(legal_name="Kestrel Freight Partners LLC", dba="Kestrel Freight", entity_type="LLC", country="US", tax_id="84-3920175",
                           address={"line1": "2875 Rickenbacker Pkwy", "city": "Columbus", "region": "OH", "postal_code": "43217", "country": "US"},
                           website="kestrelfreight.example",
                           contact={"name": "Maya Okafor", "title": "Controller", "email": "ap@kestrelfreight.example", "phone": "+1 (614) 555-0136"},
                           category="services_onsite", description="Yard and dock logistics at the Columbus plant", expected_annual_spend=180000,
                           requested_by="Jordan Lee - Plant logistics",
                           bank={"account_holder": "Kestrel Freight Partners LLC", "bank_name": "First Buckeye Bank", "country": "US",
                                 "account_number": "7300418826", "routing_number": "044200015"}),
        "docs": [("W9_Kestrel_Freight_2026.pdf", w9, KESTREL_W9),
                 ("Bank_confirmation_Kestrel.pdf", bank_letter, dict(bank="First Buckeye Bank", bank_address="41 S High St, Columbus, OH 43215  |  Member FDIC",
                                                                     date="November 5, 2026", holder="KESTREL FREIGHT PARTNERS, L.L.C.", account="7300418826",
                                                                     routing="044200015", type="Business checking", officer="R. Castellano",
                                                                     officer_title="Vice President, Treasury Services")),
                 ("COI_Kestrel_2026-27.pdf", coi, dict(cert_date="10/28/2026", producer="Great Lakes Risk Partners LLC",
                                                       insured="Kestrel Freight Partners LLC dba Kestrel Freight", address="2875 Rickenbacker Pkwy, Columbus, OH 43217",
                                                       insurer="Midwest Mutual Casualty Co.", policy="GL-88213-04", eff="07/01/2026", exp="06/30/2027",
                                                       gl="$2,000,000", agg="$4,000,000", holder=BUYER[0], rep="L. Martens"))],
    },
    {
        "id": "02_edge_third_party_bank_harborview", "tag": "edge", "title": "Edge 1 - Bank account held by a factoring company",
        "description": "Harborview Packaging's documents are all genuine, but its bank letter is for an account held by Meridian Receivables "
                       "Finance LLC. Could be receivables factoring (legitimate) or payment diversion (fraud) - either way it can't be auto-approved.",
        "expected": "PENDING - vendor asked for own-name account or Notice of Assignment; Treasury notified",
        "submission": base(legal_name="Harborview Packaging Inc.", entity_type="Corporation", country="US", tax_id="36-4418203",
                           address={"line1": "1500 S Columbus Blvd", "city": "Philadelphia", "region": "PA", "postal_code": "19147", "country": "US"},
                           website="harborviewpack.example",
                           contact={"name": "Elena Brooks", "title": "Credit Manager", "email": "credit@harborviewpack.example", "phone": "+1 (215) 555-0172"},
                           category="goods", description="Corrugated shipping cartons", expected_annual_spend=320000, requested_by="Priya Nair - Procurement",
                           bank={"account_holder": "Meridian Receivables Finance LLC", "bank_name": "Keystone Commerce Bank", "country": "US",
                                 "account_number": "5502981177", "routing_number": "031900247"}),
        "docs": [("Harborview_W-9.pdf", w9, dict(name="Harborview Packaging Inc.", classification="C corporation", address="1500 S Columbus Blvd",
                                                 city="Philadelphia, PA 19147", ein="36-4418203", signer="Elena Brooks", date="10/21/2026")),
                 ("Harborview_bank_letter.pdf", bank_letter, dict(bank="Keystone Commerce Bank", bank_address="1 Penn Square, Philadelphia, PA 19107",
                                                                  date="November 3, 2026", holder="Meridian Receivables Finance LLC", account="5502981177",
                                                                  routing="031900247", officer="D. Albright", officer_title="Commercial Banking Officer",
                                                                  color="#1F3A68"))],
    },
    {
        "id": "03_edge_resubmission_harborview", "tag": "edge", "title": "Edge 1b - Harborview resubmits",
        "description": "The vendor's reply to the email from Edge 1: the same registration with a new bank letter for an account in its own name. "
                       "Linked to the open case automatically; shows which earlier issues are now resolved.",
        "expected": "APPROVED - 'resolved since previous submission'", "after": "02_edge_third_party_bank_harborview",
        "submission": None,  # filled from scenario 2 below
        "docs": [("Harborview_W-9.pdf", w9, dict(name="Harborview Packaging Inc.", classification="C corporation", address="1500 S Columbus Blvd",
                                                 city="Philadelphia, PA 19147", ein="36-4418203", signer="Elena Brooks", date="10/21/2026")),
                 ("Harborview_bank_letter_own_account.pdf", bank_letter, dict(bank="Keystone Commerce Bank", bank_address="1 Penn Square, Philadelphia, PA 19107",
                                                                              date="November 18, 2026", holder="Harborview Packaging Inc.", account="5502730419",
                                                                              routing="031900247", officer="D. Albright", officer_title="Commercial Banking Officer",
                                                                              color="#1F3A68"))],
    },
    {
        "id": "04_edge_foreign_wrong_form_rheinwerk", "tag": "edge", "title": "Edge 2 - German supplier: wrong tax form, malformed VAT ID",
        "description": "Rheinwerk Präzisionsteile GmbH entered an 8-digit USt-IdNr. (Germany needs DE + 9 digits) and attached a US Form W-9 "
                       "instead of a W-8BEN-E. Its Handelsregister extract shows the correct VAT ID. IBAN and SWIFT are valid.",
        "expected": "PENDING - vendor asked for W-8BEN-E and to confirm the suggested VAT ID",
        "submission": base(legal_name="Rheinwerk Präzisionsteile GmbH", entity_type="GmbH", country="DE", tax_id="DE 81234567",
                           address={"line1": "Industriestraße 14", "city": "Stuttgart", "region": "Baden-Württemberg", "postal_code": "70565", "country": "DE"},
                           website="rheinwerk-praezision.example",
                           contact={"name": "Jonas Albrecht", "title": "Geschäftsführer", "email": "j.albrecht@rheinwerk-praezision.example", "phone": "+49 711 555 0190"},
                           category="goods", description="CNC-machined precision spindle components", expected_annual_spend=600000, currency="EUR",
                           requested_by="Marco Diaz - Engineering procurement",
                           bank={"account_holder": "Rheinwerk Präzisionsteile GmbH", "bank_name": "Neckar Handelsbank AG", "country": "DE",
                                 "iban": "DE06 6005 0101 0004 4273 19", "swift": "NHBKDESSXXX"}),
        "docs": [("W9_Rheinwerk.pdf", w9, dict(name="Rheinwerk Präzisionsteile GmbH", classification="Other: GmbH", address="Industriestraße 14",
                                               city="70565 Stuttgart, Germany", ein="DE 81234567", signer="Jonas Albrecht", date="11/09/2026")),
                 ("Bankbestaetigung_Rheinwerk.pdf", bank_letter, dict(bank="Neckar Handelsbank AG", bank_address="Königstraße 3, 70173 Stuttgart, Deutschland",
                                                                      date="November 9, 2026", holder="Rheinwerk Präzisionsteile GmbH",
                                                                      iban="DE06 6005 0101 0004 4273 19", swift="NHBKDESSXXX", currency="EUR",
                                                                      type="Geschäftskonto / business current account", officer="S. Wagner",
                                                                      officer_title="Firmenkundenbetreuung", color="#7A1F1F")),
                 ("HRB_734211_Auszug.pdf", handelsregister, dict(date="12.11.2026", hrb="HRB 734211", name="Rheinwerk Präzisionsteile GmbH",
                                                                 address="Industriestraße 14, 70565 Stuttgart", inc="01.10.2008", md="Jonas Albrecht",
                                                                 vat="DE812345673"))],
    },
    {
        "id": "05_edge_impersonation_apex", "tag": "edge", "title": "Edge 3 - 'Apex Industrial' registers again",
        "description": "Every document is internally perfect - W-9, bank letter, matching names. But the EIN already belongs to active "
                       "vendor V-1001, the bank account is new and the email domain is a look-alike. Vendor impersonation through the onboarding back door.",
        "expected": "REJECTED - Security; no reply to the submitter",
        "submission": base(legal_name="Apex Industrial Supplies LLC", dba="Apex Industrial", entity_type="LLC", country="US", tax_id="47-1829304",
                           address={"line1": "4410 Harbor Way", "city": "Oakland", "region": "CA", "postal_code": "94607", "country": "US"},
                           website="apexindustrial-supplies.example",
                           contact={"name": "Dana Whitfield", "title": "AR Manager", "email": "ar@apexindustrial-supplies.example", "phone": "+1 (510) 555-0199"},
                           category="goods", description="MRO supplies - updated supplier profile after bank migration", expected_annual_spend=240000,
                           requested_by="Self-registration (supplier portal)",
                           bank={"account_holder": "Apex Industrial Supplies LLC", "bank_name": "Golden Gate Digital Bank", "country": "US",
                                 "account_number": "8812004471", "routing_number": "121142915"}),
        "docs": [("Apex_W9_2026.pdf", w9, dict(name="Apex Industrial Supplies LLC", dba="Apex Industrial", classification="Limited liability company - S corporation",
                                               address="4410 Harbor Way", city="Oakland, CA 94607", ein="47-1829304", signer="Dana Whitfield", date="11/12/2026")),
                 ("Apex_bank_verification.pdf", bank_letter, dict(bank="Golden Gate Digital Bank", bank_address="500 Mission St, San Francisco, CA 94105",
                                                                  date="November 12, 2026", holder="Apex Industrial Supplies LLC", account="8812004471",
                                                                  routing="121142915", officer="K. Tran", officer_title="Client Services", color="#B8860B"))],
    },
    {
        "id": "06_edge_watchlist_corvane", "tag": "edge", "title": "Edge 4 - Potential watchlist match",
        "description": "Corvane Marine Logistics FZE (UAE) submits a complete, consistent pack - W-8BEN-E, bank letter, trade licence. Screening "
                       "finds a 92 % match to a listed entity, 'Corvane Maritime Logistics FZCO'. Most fuzzy hits are false positives, but none is auto-cleared.",
        "expected": "PENDING - Compliance; neutral 'under review' email (no tipping-off)",
        "submission": base(legal_name="Corvane Marine Logistics FZE", entity_type="FZE", country="AE", tax_id="100234567800003",
                           address={"line1": "Unit 1904, Marina Plaza", "city": "Dubai", "region": None, "postal_code": "00000", "country": "AE"},
                           website="corvane-marine.example",
                           contact={"name": "Farid Haddad", "title": "Commercial Director", "email": "farid.haddad@corvane-marine.example", "phone": "+971 4 555 0147"},
                           category="services_remote", description="Ocean freight forwarding for Gulf export lanes", expected_annual_spend=450000,
                           requested_by="Jordan Lee - Plant logistics",
                           bank={"account_holder": "Corvane Marine Logistics FZE", "bank_name": "Gulf Meridian Bank PJSC", "country": "AE",
                                 "iban": "AE07 0331 2345 6789 0123 456", "swift": "GMBKAEADXXX"}),
        "docs": [("W-8BEN-E_Corvane.pdf", w8bene, dict(name="Corvane Marine Logistics FZE", country="United Arab Emirates", status="Corporation",
                                                       address="Unit 1904, Marina Plaza, Dubai", tin="100234567800003", signer="Farid Haddad", date="2026-11-10")),
                 ("Corvane_bank_letter.pdf", bank_letter, dict(bank="Gulf Meridian Bank PJSC", bank_address="Sheikh Zayed Road, Dubai, United Arab Emirates",
                                                               date="November 10, 2026", holder="Corvane Marine Logistics FZE", iban="AE07 0331 2345 6789 0123 456",
                                                               swift="GMBKAEADXXX", currency="USD", officer="N. Rahman", officer_title="Relationship Manager",
                                                               color="#0E5E6F")),
                 ("Corvane_trade_licence.pdf", trade_licence, dict(lic="DMCC-187234", name="Corvane Marine Logistics FZE", address="Unit 1904, Marina Plaza, Dubai",
                                                                   inc="2019-02-27", trn="100234567800003", issue="2026-09-15",
                                                                   activities="Freight forwarding; ship agency", manager="Farid Haddad"))],
    },
    {
        "id": "07_bonus_messy_bluegill", "tag": "bonus", "title": "Bonus - The everyday messy submission",
        "description": "Bluegill Facility Services (on-site janitorial): an invoice attached where the bank letter should be, a scanned insurance "
                       "certificate that is expired and under-insured, an unsigned W-9, 'Inc.' on the form but 'LLC' on the W-9, a Gmail address, no phone.",
        "expected": "PENDING - one email to the vendor listing every item",
        "expected_no_ai": "PENDING - scanned certificate routed to manual keying (no AI engine)",
        "submission": base(legal_name="Bluegill Facility Services Inc.", entity_type="Corporation", country="US", tax_id="61-2297734",
                           address={"line1": "410 W Chestnut St", "city": "Louisville", "region": "KY", "postal_code": "40202", "country": "US"},
                           website="bluegillfacility.example",
                           contact={"name": "Tanya Morris", "title": "Owner", "email": "bluegillfacility@gmail.com", "phone": None},
                           category="services_onsite", description="Nightly janitorial service, Louisville warehouse", expected_annual_spend=95000,
                           requested_by="Sam Ortiz - Facilities",
                           bank={"account_holder": "Bluegill Facility Services", "bank_name": "Bluegrass Community Bank", "country": "US",
                                 "account_number": "4471902218", "routing_number": "083900512"}),
        "docs": [("bluegill w9.pdf", w9, dict(name="Bluegill Facility Services LLC", classification="Limited liability company - S corporation",
                                              address="410 W Chestnut St", city="Louisville, KY 40202", ein="61-2297734", signer=None, date="")),
                 ("bank info.pdf", invoice, dict(vendor="Bluegill Facility Services", vendor_address="410 W Chestnut St, Louisville, KY 40202",
                                                 number="BFS-1042", date="10/31/2026",
                                                 lines=[("Nightly janitorial service - October 2026 (trial month)", 3600.00), ("Floor stripping & waxing", 250.00)],
                                                 remit="Remit to: Bluegrass Community Bank, account 4471902218, routing 083900512")),
                 ("insurance_scan.pdf", coi, dict(cert_date="09/02/2025", producer="Ohio Valley Insurance Agency", insured="Bluegill Facility Services LLC",
                                                  address="410 W Chestnut St, Louisville, KY 40202", insurer="Commonwealth Casualty Co.", policy="CGL-2209-117",
                                                  eff="09/30/2025", exp="09/30/2026", gl="$500,000", agg="$1,000,000", holder="Bluegill Facility Services LLC",
                                                  holder_address="410 W Chestnut St, Louisville, KY 40202", rep="P. Lindqvist"))],
        "scan": ["insurance_scan.pdf"],
    },
]


def main():
    OUT.mkdir(exist_ok=True)
    by_id = {s["id"]: s for s in SCENARIOS}
    manifest = []
    for s in SCENARIOS:
        d = OUT / s["id"]
        d.mkdir(exist_ok=True)
        for old in d.glob("*.pdf"):
            old.unlink()
        sub = s["submission"]
        if sub is None:  # resubmission = previous form with corrected bank details
            prev = by_id[s["after"]]["submission"]
            sub = json.loads(json.dumps(prev))
            sub["bank"].update({"account_holder": "Harborview Packaging Inc.", "account_number": "5502730419"})
        (d / "submission.json").write_text(json.dumps(sub, indent=2, ensure_ascii=False), encoding="utf-8")
        for fname, fn, data in s["docs"]:
            make_pdf(d / fname, fn, data)
            if fname in s.get("scan", []):
                to_scan(d / fname)
        entry = {k: s[k] for k in ("id", "tag", "title", "description", "expected") if k in s}
        for k in ("expected_no_ai", "after"):
            if k in s:
                entry[k] = s[k]
        entry["files"] = [f"{s['id']}/{f}" for f, _, _ in s["docs"]]
        manifest.append(entry)
        print("wrote", s["id"], [f for f, _, _ in s["docs"]])
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
