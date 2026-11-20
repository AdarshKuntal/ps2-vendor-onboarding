"""Central configuration. Every policy threshold lives here so it can be explained and tuned."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "data" / "seed"
RUNTIME_DIR = Path(os.getenv("RUNTIME_DIR", ROOT / "data" / "runtime"))
FILES_DIR = RUNTIME_DIR / "files"
DB_PATH = RUNTIME_DIR / "onboarding.db"
SAMPLES_DIR = ROOT / "samples"
STATIC_DIR = ROOT / "static"

BUYER_NAME = "Brightline Manufacturing Inc."
BUYER_COUNTRY = "US"


def today() -> date:
    """'Today' for document-age checks. Pinned by default so the demo documents (dated Nov 2026) keep
    behaving the same on any day the demo is run; set DEMO_TODAY=real to use the system date."""
    v = os.getenv("DEMO_TODAY", "2026-11-20")
    return date.today() if v.lower() == "real" else date.fromisoformat(v)


# --- AI document reading ------------------------------------------------------------
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT_DIGITAL = os.getenv("EFFORT_DIGITAL", "low")
EFFORT_SCANNED = os.getenv("EFFORT_SCANNED", "medium")
SCANNED_TEXT_THRESHOLD = 40          # fewer extractable chars than this => treat as a scan
MIN_DOC_CONFIDENCE = 0.70            # below this a document goes to a human

# --- Policy -------------------------------------------------------------------------
NAME_MATCH_STRONG = 92               # normalised fuzzy score for "same name"
NAME_MATCH_CLOSE = 80                # 80-92 = probably the same, recorded
BANK_LETTER_MAX_AGE_DAYS = 90
W9_MAX_AGE_DAYS = 365 * 3
COI_MIN_GENERAL_LIABILITY = 1_000_000
COI_EXPIRY_WARNING_DAYS = 30
WATCHLIST_POTENTIAL = 80             # fuzzy score => compliance review
WATCHLIST_STRONG = 95                # + same country => reject
DUPLICATE_NAME_SCORE = 90            # vendor-master name similarity => possible duplicate
LOOKALIKE_DOMAIN_SCORE = 75          # email domain similar-but-different to the one on file
RISK_MEDIUM = 30                     # risk points: >= medium => approve with conditions
RISK_HIGH = 60                       # >= high => compliance enhanced due diligence

FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com",
                      "proton.me", "protonmail.com", "gmx.de", "mail.com", "yandex.com"}

# UI pacing: a minimum visible duration per stage so a human can follow the live view.
MIN_STAGE_MS = int(os.getenv("MIN_STAGE_MS", "550"))


def anthropic_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
