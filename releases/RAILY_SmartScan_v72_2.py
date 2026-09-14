# SmartScan RAILY v72.2 — Structured OCR & Handwritten Field Intelligence
# Windows 10/11 desktop document organizer.
#
# Design:
#   1. Document TYPE decides filing. Dates never decide the folder.
#   2. Printed form structure is the primary learned-family signal.
#   3. One correction teaches the family, then queued siblings are re-checked
#      automatically so the user does not have to teach the same form repeatedly.
#   4. Duplicates are reviewed, never automatically deleted.
#   5. Dates are optional filename metadata only.
#   6. Existing v24+ clean-start family learning is migrated on first run.

from __future__ import annotations

import os
import re
import json
import time
import shutil
import hashlib
import threading
import sys
import subprocess
import zipfile
import base64
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher

APP_VERSION = "72.2"
APP_RELEASE = 72
APP_DISPLAY_NAME = "RAILY — SmartScan Railroad Document Intelligence"
RAILY_NAME = "RAILY"
RAILY_UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/heydunpehrson-byte/"
    "raily-smartscan-updates/main/update_manifest.json"
)


import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import pymupdf as fitz
except Exception:
    try:
        import fitz
    except Exception as e:
        raise SystemExit("PyMuPDF required: pip install pymupdf") from e

try:
    import pytesseract
except Exception as e:
    raise SystemExit("pytesseract required: pip install pytesseract") from e

try:
    from PIL import Image, ImageOps, ImageTk, ImageChops
except Exception as e:
    raise SystemExit("Pillow required: pip install pillow") from e


try:
    import pystray
except Exception:
    pystray = None

try:
    from send2trash import send2trash
except Exception:
    send2trash = None


# ============================================================================
# Paths / configuration
# ============================================================================

APP_DIR = Path(__file__).resolve().parent
SCAN_ROOT = Path.home() / "Documents" / "Scans"

# v67 clean application layout. Active state no longer clutters the program
# folder; it lives under Data/, logs under Logs/, and backups under Backups/.
DATA_DIR = APP_DIR / "Data"
SETTINGS_DIR = DATA_DIR / "Settings"
LEARNING_DIR = DATA_DIR / "Learning"
INDEXES_DIR = DATA_DIR / "Indexes"
DATES_DIR = DATA_DIR / "Dates"
HISTORY_DIR = DATA_DIR / "History"
LEGACY_DATA_DIR = DATA_DIR / "Legacy"
CONFLICT_DIR = LEGACY_DATA_DIR / "Root Conflicts"
LOG_DIR = APP_DIR / "Logs"
BACKUP_ROOT = APP_DIR / "Backups"

for _folder in (
    DATA_DIR, SETTINGS_DIR, LEARNING_DIR, INDEXES_DIR, DATES_DIR,
    HISTORY_DIR, LEGACY_DATA_DIR, CONFLICT_DIR, LOG_DIR, BACKUP_ROOT
):
    _folder.mkdir(parents=True, exist_ok=True)


def _safe_conflict_target(source):
    """Return a unique place to preserve an older duplicate during migration."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = CONFLICT_DIR / f"{stamp}_{Path(source).name}"
    n = 2
    while target.exists():
        target = CONFLICT_DIR / f"{stamp}_{n}_{Path(source).name}"
        n += 1
    return target


def _absorb_root_file(name, target):
    """Move a legacy root state file into v67's Data layout without data loss.

    If both copies exist, the newer file wins and the older copy is archived
    under Data/Legacy/Root Conflicts rather than being deleted.
    """
    source = APP_DIR / name
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        if not source.exists() or source.resolve() == target.resolve():
            return

        if not target.exists():
            shutil.move(str(source), str(target))
            return

        source_mtime = source.stat().st_mtime
        target_mtime = target.stat().st_mtime

        if source_mtime > target_mtime:
            preserved = _safe_conflict_target(target)
            shutil.move(str(target), str(preserved))
            shutil.move(str(source), str(target))
        else:
            preserved = _safe_conflict_target(source)
            shutil.move(str(source), str(preserved))
    except Exception:
        # SmartScan must still start even if Windows temporarily locks a file.
        pass


def _merge_old_backup_folder():
    old = APP_DIR / "SmartScan Backups"
    try:
        if not old.exists() or old.resolve() == BACKUP_ROOT.resolve():
            return
        for item in list(old.iterdir()):
            target = BACKUP_ROOT / item.name
            if not target.exists():
                shutil.move(str(item), str(target))
            else:
                preserved = BACKUP_ROOT / f"Legacy_{datetime.now():%Y%m%d_%H%M%S}_{item.name}"
                shutil.move(str(item), str(preserved))
        try:
            old.rmdir()
        except Exception:
            pass
    except Exception:
        pass


CONFIG_FILE = SETTINGS_DIR / "SMARTSCAN_V40_CONFIG.json"
LEARNING_FILE = LEARNING_DIR / "SMARTSCAN_V40_LEARNING.json"
INDEX_FILE = INDEXES_DIR / "SMARTSCAN_V40_INDEX.json"
LOG_FILE = LOG_DIR / "SMARTSCAN_V40.log"
DATE_PROFILE_FILE = DATES_DIR / "SMARTSCAN_V51_DATE_PROFILES.json"
DOCUMENT_DATE_FILE = DATES_DIR / "SMARTSCAN_V56_DOCUMENT_DATES.json"
FILING_HISTORY_FILE = HISTORY_DIR / "SMARTSCAN_V61_FILING_HISTORY.json"
FAMILY_DUP_HISTORY_FILE = HISTORY_DIR / "SMARTSCAN_V68_FAMILY_DUP_HISTORY.json"
STATS_FILE = HISTORY_DIR / "SMARTSCAN_V65_STATS.json"
RESTORE_MARKER_FILE = HISTORY_DIR / "SMARTSCAN_V65_LAST_RESTORE.txt"
RAILY_MEMORY_FILE = LEARNING_DIR / "SMARTSCAN_V71_RAILY_MEMORY.json"
UPDATE_DIR = APP_DIR / "Updates"
UPDATE_DIR.mkdir(parents=True, exist_ok=True)

# Current clean-start learning file from v24-v34. v67 keeps it only as legacy
# import material; the working learning database is SMARTSCAN_V40_LEARNING.json.
LEGACY_CLEAN_LEARNING = LEGACY_DATA_DIR / "SMARTSCAN_V24_LEARNING.json"

# Absorb the active state files created by v40-v66. This runs before any state
# is loaded, so the exact same learning continues seamlessly from the new paths.
for _old_name, _new_path in (
    ("SMARTSCAN_V40_CONFIG.json", CONFIG_FILE),
    ("SMARTSCAN_V40_LEARNING.json", LEARNING_FILE),
    ("SMARTSCAN_V40_INDEX.json", INDEX_FILE),
    ("SMARTSCAN_V40.log", LOG_FILE),
    ("SMARTSCAN_V51_DATE_PROFILES.json", DATE_PROFILE_FILE),
    ("SMARTSCAN_V56_DOCUMENT_DATES.json", DOCUMENT_DATE_FILE),
    ("SMARTSCAN_V61_FILING_HISTORY.json", FILING_HISTORY_FILE),
    ("SMARTSCAN_V65_STATS.json", STATS_FILE),
    ("SMARTSCAN_V65_LAST_RESTORE.txt", RESTORE_MARKER_FILE),
    ("SMARTSCAN_V24_LEARNING.json", LEGACY_CLEAN_LEARNING),
):
    _absorb_root_file(_old_name, _new_path)

# Older state that v67 no longer reads is still worth keeping, but it does not
# need to remain loose in the program folder.
for _legacy_name in (
    "SMARTSCAN_V24_CONFIG.json",
    "SMARTSCAN_V24_INDEX.json",
    "CATEGORIES.json",
    "CATEGORIES.backup.json",
    "CATEGORIES.old.json",
    "DOCUMENT_INDEX.json",
    "LEARNED_RULES.json",
    "config.json",
):
    _absorb_root_file(_legacy_name, LEGACY_DATA_DIR / _legacy_name)

_merge_old_backup_folder()

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

DEFAULT_CONFIG = {
    "incoming": str(SCAN_ROOT / "Incoming"),
    "sorted": str(SCAN_ROOT / "Sorted"),
    "review": str(SCAN_ROOT / "Review"),
    "duplicates": str(SCAN_ROOT / "Duplicates" / "Review"),

    "category_threshold": 64,
    "family_threshold": 50,
    "auto_family_threshold": 62,
    "queue_family_threshold": 58,

    "rename_by_type": True,
    "family_subfolders": False,
    "ask_to_teach_unknown": True,
    "printed_structure_learning": True,
    "printed_ocr_min_confidence": 55,

    "detect_duplicates": True,
    "date_aware_duplicates": True,
    "duplicate_text_threshold": 0.91,
    "duplicate_visual_text_threshold": 0.80,
    "duplicate_visual_max_distance": 3,
    "duplicate_same_date_text_threshold": 0.86,
    "duplicate_same_date_token_threshold": 0.82,
    "duplicate_same_date_visual_max_distance": 5,
    "duplicate_same_date_min_size_similarity": 0.72,

    # v68: compare each newly recognized document against the historical
    # documents already learned/filed under the same family. Family alone is
    # never enough to declare a duplicate; date/identifier/content evidence
    # must also agree.
    "family_history_duplicates": True,
    "family_history_max_entries": 5000,
    "family_history_same_date_text_threshold": 0.90,
    "family_history_same_date_token_threshold": 0.84,
    "family_history_identifier_text_threshold": 0.93,
    "family_history_identifier_token_threshold": 0.88,
    "family_history_no_key_text_threshold": 0.985,
    "family_history_no_key_token_threshold": 0.95,

    "add_date_to_filename": True,
    "date_confidence_threshold": 92,
    "minimum_document_year": 2020,
    "maximum_future_years": 1,
    "v41_safe_dates_initialized": False,
    "use_learned_date_region": True,
    "date_region_padding": 0.018,
    "handwritten_date_mode": True,
    "date_subcrop_search": True,
    "fast_post_recognition": True,
    "quick_date_regions": 2,
    "quick_date_subcrops": 3,
    "pause_when_required_date_missing": True,

    "auto_split_combined_scans": True,
    "fast_stack_mode": True,
    "incoming_one_at_a_time": True,
    "split_repeated_forms": True,
    "keep_split_originals": True,
    "split_category_threshold": 78,
    "split_family_threshold": 62,

    "poll_seconds": 2.0,
    "ocr_language": "eng",
    "tesseract_path": "",

    # One-teach-many behavior.
    "auto_apply_new_learning_to_queue": True,
    "auto_process_queue_matches": True,

    "auto_start_sorting_on_launch": False,
    "automatic_state_backups": True,

    # v65 intelligence / management suite.
    "auto_confidence_growth": True,
    "recognition_explanations": True,
    "date_area_recovery": True,
    "document_variant_learning": True,
    "persistent_dashboard_stats": True,
    "minimize_to_tray": False,
    "tray_notifications": False,
    "auto_backup_before_learning_changes": True,
    # v66: user-created categories persist and reappear in Learn menus.
    "custom_categories": [],
    "last_release_backup_version": 0,
    "split_use_page_numbers": True,
    "split_use_document_ids": True,

    # v71 RAILY — railroad-first filing and full-page AI/visual learning.
    "raily_visual_ai_enabled": True,
    "raily_visual_match_threshold": 80,
    "raily_visual_autofile_threshold": 90,
    "raily_visual_memory_examples": 12,
    "raily_route_filing": True,
    "raily_require_railroad_location": True,
    "raily_include_person_in_filename": True,
    "raily_ai_provider": "Local Full-Page",
    "raily_vision_api_url": "",
    "raily_vision_model": "",
    "raily_vision_api_key_env": "RAILY_VISION_API_KEY",
    "raily_update_mode": "Recommended",
    "raily_update_manifest_url": RAILY_UPDATE_MANIFEST_URL,
    "raily_update_channel": "stable",
    "raily_update_only_when_idle": True,
}



NEW_CATEGORY_OPTION = "➕ New Category..."
NEW_FAMILY_OPTION = "➕ New Document Type..."

CATEGORY_RULES = {
    "Expense Reports": [
        "expense report", "reimbursement", "per diem", "travel expense",
        "business expense", "expense reimbursement", "business purpose",
        "transportation", "lodging"
    ],
    "Mileage Logs": [
        "mileage log", "miles driven", "odometer", "mileage rate",
        "business miles", "trip mileage"
    ],
    "Logs": [
        "date worked", "start count", "on duty", "time out",
        "total hours", "work log", "daily log", "crew log",
        "hours of service"
    ],
    "Pay Statements": [
        "pay statement", "pay stub", "earnings statement", "gross pay",
        "net pay", "pay period", "check date"
    ],
    "Railroad": [
        "track warrant", "general order", "timetable", "railroad",
        "federal railroad administration", "fra", "locomotive",
        "conductor", "engineer"
    ],
    "Employment": [
        "employment", "employee", "human resources", "job offer",
        "offer letter", "position", "applicant"
    ],
    "Bills": [
        "amount due", "balance due", "payment due", "due date",
        "billing statement", "invoice", "bill", "account balance"
    ],
    "Banking": [
        "bank statement", "checking account", "savings account",
        "deposit", "withdrawal", "statement period"
    ],
    "Credit Cards": [
        "credit card", "minimum payment", "credit limit",
        "statement balance", "available credit"
    ],
    "Insurance": [
        "insurance", "policy number", "coverage", "premium",
        "deductible", "claim number", "insured"
    ],
    "Legal": [
        "case number", "plaintiff", "defendant", "attorney",
        "court", "legal", "agreement", "contract"
    ],
    "Taxes": [
        "internal revenue service", "irs", "form 1040", "1099",
        "w-2", "wage and tax statement", "tax return", "tax"
    ],
    "Medical": [
        "patient", "provider", "medical", "hospital",
        "diagnosis", "treatment", "explanation of benefits"
    ],
    "Vehicle": [
        "vehicle", "vin", "vehicle identification number",
        "repair order", "registration", "auto", "automobile"
    ],
    "Home": [
        "mortgage", "rent", "lease", "property", "home",
        "homeowner", "landlord"
    ],
    "Receipts": [
        "receipt", "subtotal", "sales tax", "amount paid",
        "cash", "change", "total"
    ],
    "Identification": [
        "driver license", "driver's license", "identification",
        "passport", "date of birth", "license number"
    ],
    "Mail": [
        "usps", "postal", "mail", "sender", "recipient"
    ],
}


STANDARD_FAMILIES = {
    "Expense Reports": [
        ("Expense Report", (
            "expense report", "business purpose", "reimbursement",
            "transportation", "lodging", "per diem"
        )),
    ],
    "Mileage Logs": [
        ("Mileage Log", ("mileage log", "miles driven", "odometer", "mileage rate")),
    ],
    "Logs": [
        ("IRAIL Work Log", (
            "irail services group", "date worked", "start count",
            "on duty", "time out", "total hours"
        )),
        ("Hours of Service Log", ("hours of service", "on duty", "off duty")),
        ("Work Log", ("date worked", "start count", "on duty", "time out")),
    ],
    "Pay Statements": [
        ("Pay Statement", ("pay statement", "pay stub", "gross pay", "net pay", "pay period")),
    ],
    "Bills": [
        ("Personal Loan Statement", ("personal loan", "loan statement", "principal balance")),
        ("Auto Loan Statement", ("auto loan", "vehicle loan", "car loan")),
        ("Utility Bill", ("utility", "electric", "water", "natural gas", "kwh")),
        ("Phone Bill", ("wireless", "mobile", "phone bill", "data plan")),
        ("Internet Bill", ("internet", "broadband", "wifi")),
        ("Hotel Bill", ("hotel", "lodging", "room charge", "guest")),
    ],
    "Receipts": [
        ("Auto Parts Receipt", ("auto parts", "o'reilly", "oreilly", "autozone", "napa", "advance auto")),
        ("Fuel Receipt", ("gallons", "price per gallon", "diesel", "gasoline")),
        ("Hotel Receipt", ("hotel", "lodging", "room", "guest")),
        ("Restaurant Receipt", ("server", "table", "tip", "gratuity")),
    ],
    "Taxes": [
        ("W-2", ("w-2", "wage and tax statement")),
        ("1099", ("1099",)),
        ("Tax Return", ("form 1040", "tax return", "adjusted gross income")),
    ],
    "Banking": [
        ("Bank Statement", ("bank statement", "checking account", "savings account", "statement period")),
    ],
    "Credit Cards": [
        ("Credit Card Statement", ("credit card", "minimum payment", "credit limit", "statement balance")),
    ],
    "Insurance": [
        ("Insurance Policy", ("insurance policy", "policy number", "coverage", "deductible")),
    ],
    "Vehicle": [
        ("Vehicle Registration", ("vehicle registration", "registration", "vin")),
        ("Vehicle Service Record", ("repair order", "vehicle service", "service advisor")),
    ],
    "Employment": [
        ("Offer Letter", ("offer letter", "job offer", "position")),
        ("Employment Document", ("employment", "employee", "human resources")),
    ],
    "Medical": [
        ("Explanation of Benefits", ("explanation of benefits", "eob")),
        ("Medical Document", ("patient", "provider", "medical")),
    ],
    "Legal": [
        ("Legal Document", ("case number", "plaintiff", "defendant", "attorney")),
    ],
    "Railroad": [
        ("Track Warrant", ("track warrant",)),
        ("General Order", ("general order",)),
        ("Timetable", ("timetable",)),
        ("Railroad Document", ("railroad", "fra")),
    ],
}


GENERIC_FAMILY = {
    "Expense Reports": "Expense Report",
    "Mileage Logs": "Mileage Log",
    "Logs": "Log",
    "Pay Statements": "Pay Statement",
    "Railroad": "Railroad Document",
    "Employment": "Employment Document",
    "Bills": "Bill",
    "Banking": "Bank Statement",
    "Credit Cards": "Credit Card Statement",
    "Insurance": "Insurance Document",
    "Legal": "Legal Document",
    "Taxes": "Tax Document",
    "Medical": "Medical Document",
    "Vehicle": "Vehicle Document",
    "Home": "Home Document",
    "Receipts": "Receipt",
    "Identification": "Identification",
    "Mail": "Mail",
}


FILENAME_CATEGORY_HINTS = {
    "Expense Reports": ("expense report", "expense reports", "expense_report", "expenses report"),
    "Mileage Logs": ("mileage log", "mileage logs", "mileage_log"),
    "Pay Statements": ("pay statement", "pay statements", "pay stub", "pay_stub"),
    "Logs": ("work log", "work_log", "crew log", "hours of service"),
    "Taxes": ("tax return", "w-2", "w2", "1099"),
    "Receipts": ("receipt", "receipts"),
    "Banking": ("bank statement",),
    "Credit Cards": ("credit card statement",),
    "Insurance": ("insurance policy",),
}


STRUCTURE_ANCHORS = (
    "irail services group",
    "start date",
    "start count",
    "date worked",
    "on duty",
    "time out",
    "total hours",
    "hours of service",
    "employee name",
    "employee id",
    "pay period",
    "gross pay",
    "net pay",
    "statement date",
    "invoice date",
    "amount due",
    "balance due",
    "policy number",
    "account number",
    "mileage log",
    "odometer",
    "expense report",
    "reimbursement",
    "business purpose",
    "company name",
    "department",
    "approved by",
    "submitted by",
    "transportation",
    "lodging",
    "meals",
    "subtotal",
    "sales tax",
)


# Families that are commonly scanned as one complete form per page.
# We do NOT split merely because the family matches. A strong "document-start"
# signature must appear again on the next page.
REPEATABLE_FORM_START_SIGNALS = {
    ("Expense Reports", "Expense Report"): (
        "expense report",
        "business purpose",
        "company name",
        "reimbursement",
    ),
    ("Mileage Logs", "Mileage Log"): (
        "mileage log",
        "odometer",
        "miles driven",
        "mileage rate",
    ),
    ("Logs", "IRAIL Work Log"): (
        "irail services group",
        "start date",
        "start count",
        "date worked",
        "on duty",
        "time out",
    ),
    ("Logs", "Hours of Service Log"): (
        "hours of service",
        "on duty",
        "off duty",
    ),
    ("Logs", "Work Log"): (
        "start date",
        "date worked",
        "start count",
        "on duty",
        "time out",
    ),
    ("Pay Statements", "Pay Statement"): (
        "pay statement",
        "pay period",
        "gross pay",
        "net pay",
    ),
    ("Railroad", "Track Warrant"): (
        "track warrant",
    ),
    ("Taxes", "W-2"): (
        "wage and tax statement",
        "w-2",
    ),
    ("Taxes", "1099"): (
        "1099",
    ),
}

CONTINUATION_PAGE_SIGNALS = (
    "continued",
    "continued on next page",
    "continued from previous page",
    "page 2 of",
    "page 3 of",
    "page 4 of",
    "page 5 of",
)


STOPWORDS = {
    "the","and","for","with","from","this","that","your","you","are","was","were",
    "has","have","had","not","but","all","any","can","will","our","their","his","her",
    "its","into","onto","page","date","name","number","amount","total","address",
    "phone","email","account","document","company","services","service","group"
}

STRUCTURE_STOPWORDS = STOPWORDS | {
    "one","two","three","four","five","six","seven","eight","nine","zero",
    "am","pm","hr","hrs","hour","hours","day","days","week","weeks"
}


MONTHS = {
    "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,
    "apr":4,"april":4,"may":5,"jun":6,"june":6,"jul":7,"july":7,
    "aug":8,"august":8,"sep":9,"sept":9,"september":9,
    "oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12
}


# v41: document-family-specific date labels.
# Scores represent how trustworthy that label is for naming that family.
DATE_LABELS_BY_FAMILY = {
    ("Logs", "IRAIL Work Log"): [
        ("date worked", 99),
        ("work date", 97),
        ("start date", 93),
    ],
    ("Logs", "Hours of Service Log"): [
        ("date worked", 98),
        ("work date", 96),
        ("start date", 93),
    ],
    ("Logs", "Work Log"): [
        ("date worked", 98),
        ("work date", 96),
        ("start date", 93),
    ],

    ("Expense Reports", "Expense Report"): [
        ("report date", 98),
        ("expense report date", 99),
        ("date submitted", 96),
        ("submitted date", 96),
        ("date worked", 95),
        ("date", 93),
    ],

    ("Mileage Logs", "Mileage Log"): [
        ("trip date", 98),
        ("date worked", 97),
        ("mileage date", 97),
        ("date", 93),
    ],

    ("Pay Statements", "Pay Statement"): [
        ("pay date", 99),
        ("check date", 98),
        ("payment date", 97),
        ("statement date", 94),
    ],

    ("Bills", "Personal Loan Statement"): [
        ("statement date", 98),
        ("billing date", 96),
    ],
    ("Bills", "Auto Loan Statement"): [
        ("statement date", 98),
        ("billing date", 96),
    ],
    ("Bills", "Utility Bill"): [
        ("statement date", 97),
        ("billing date", 97),
        ("bill date", 96),
    ],
    ("Bills", "Phone Bill"): [
        ("statement date", 97),
        ("billing date", 97),
        ("bill date", 96),
    ],
    ("Bills", "Internet Bill"): [
        ("statement date", 97),
        ("billing date", 97),
        ("bill date", 96),
    ],
    ("Bills", "Hotel Bill"): [
        ("statement date", 95),
        ("invoice date", 97),
        ("checkout date", 94),
    ],

    ("Banking", "Bank Statement"): [
        ("statement date", 99),
        ("closing date", 97),
    ],
    ("Credit Cards", "Credit Card Statement"): [
        ("statement date", 99),
        ("closing date", 97),
    ],

    ("Insurance", "Insurance Policy"): [
        ("issue date", 97),
        ("effective date", 96),
        ("policy date", 95),
    ],

    ("Vehicle", "Vehicle Registration"): [
        ("issue date", 96),
        ("registration date", 97),
    ],
    ("Vehicle", "Vehicle Service Record"): [
        ("service date", 98),
        ("repair date", 96),
    ],

    ("Employment", "Offer Letter"): [
        ("offer date", 97),
        ("document date", 95),
        ("date issued", 95),
    ],
    ("Employment", "Employment Document"): [
        ("document date", 95),
        ("effective date", 95),
        ("date issued", 95),
    ],

    ("Medical", "Explanation of Benefits"): [
        ("statement date", 96),
        ("service date", 94),
    ],
    ("Medical", "Medical Document"): [
        ("service date", 94),
        ("statement date", 95),
    ],

    ("Legal", "Legal Document"): [
        ("document date", 96),
        ("effective date", 95),
        ("date filed", 96),
        ("filed date", 96),
    ],

    ("Railroad", "Track Warrant"): [
        ("date issued", 97),
        ("effective date", 96),
    ],
    ("Railroad", "General Order"): [
        ("effective date", 99),
        ("date issued", 97),
    ],
    ("Railroad", "Timetable"): [
        ("effective date", 99),
    ],
    ("Railroad", "Railroad Document"): [
        ("effective date", 96),
        ("date issued", 96),
    ],

    ("Receipts", "Fuel Receipt"): [
        ("transaction date", 96),
        ("purchase date", 96),
    ],
    ("Receipts", "Hotel Receipt"): [
        ("checkout date", 95),
        ("transaction date", 95),
    ],
    ("Receipts", "Restaurant Receipt"): [
        ("transaction date", 95),
    ],
    ("Receipts", "Auto Parts Receipt"): [
        ("transaction date", 95),
        ("purchase date", 95),
    ],
}

# These labels are safe enough to use even when a family does not yet have
# a dedicated rule. Generic "date" is intentionally NOT in this list.
GENERIC_SAFE_DATE_LABELS = [
    ("payment due date", 98),
    ("due date", 98),
    ("period ending", 97),
    ("period end", 96),
    ("pay period ending", 98),
    ("date worked", 97),
    ("statement date", 97),
    ("invoice date", 97),
    ("pay date", 99),
    ("check date", 98),
    ("payment date", 97),
    ("document date", 95),
    ("issue date", 96),
    ("date issued", 96),
    ("effective date", 96),
    ("service date", 94),
    ("transaction date", 95),
    ("purchase date", 95),
    ("report date", 96),
    ("date submitted", 95),
    ("submitted date", 95),
    ("billing date", 96),
    ("closing date", 97),
    ("trip date", 96),
]


# ============================================================================
# JSON / utility helpers
# ============================================================================


def is_split_part_path(path):
    """True for files generated from a combined PDF."""
    try:
        stem = Path(path).stem.casefold()
    except Exception:
        stem = str(path or "").casefold()

    return (
        "__smartscanpart__" in stem
        or "__smartscanmanualpart__" in stem
    )


def is_manual_split_part(path):
    try:
        return "__smartscanmanualpart__" in Path(path).stem.casefold()
    except Exception:
        return "__smartscanmanualpart__" in str(path or "").casefold()


def classification_filename_for(path):
    """Do not let the source PDF filename bias a split document's identity."""
    return (
        ""
        if is_split_part_path(path)
        else Path(path).name
    )


def deep_copy_json(value):
    return json.loads(json.dumps(value))


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return deep_copy_json(default)


CFG = load_json(CONFIG_FILE, DEFAULT_CONFIG)
for _k, _v in DEFAULT_CONFIG.items():
    CFG.setdefault(_k, _v)

LEARNED = load_json(LEARNING_FILE, {"version": 67, "profiles": [], "migration_done": False})
LEARNED.setdefault("version", 62)
LEARNED.setdefault("profiles", [])
LEARNED.setdefault("migration_done", False)

INDEX = load_json(INDEX_FILE, {"version": 62, "files": {}})
INDEX.setdefault("version", 62)
INDEX.setdefault("files", {})

DATE_PROFILES = load_json(
    DATE_PROFILE_FILE,
    {"version": 51, "families": {}}
)
DATE_PROFILES.setdefault("version", 51)
DATE_PROFILES.setdefault("families", {})

DOCUMENT_DATES = load_json(
    DOCUMENT_DATE_FILE,
    {"version": 56, "documents": {}}
)
DOCUMENT_DATES.setdefault("version", 56)
DOCUMENT_DATES.setdefault("documents", {})

FILING_HISTORY = load_json(
    FILING_HISTORY_FILE,
    {"version": 61, "entries": []}
)
FILING_HISTORY.setdefault("version", 61)
FILING_HISTORY.setdefault("entries", [])

FAMILY_DUP_HISTORY = load_json(
    FAMILY_DUP_HISTORY_FILE,
    {"version": 68, "entries": [], "seeded_from_index": False}
)
FAMILY_DUP_HISTORY.setdefault("version", 68)
FAMILY_DUP_HISTORY.setdefault("entries", [])
FAMILY_DUP_HISTORY.setdefault("seeded_from_index", False)

STATS = load_json(
    STATS_FILE,
    {"version": 67, "days": {}, "lifetime": {}, "recognitions": []}
)
STATS.setdefault("version", 66)
STATS.setdefault("days", {})
STATS.setdefault("lifetime", {})
STATS.setdefault("recognitions", [])

RAILY_MEMORY = load_json(
    RAILY_MEMORY_FILE,
    {
        "version": 71,
        "people": {},
        "railroads": {},
        "locations": {},
        "document_overrides": {},
        "updates": [],
    }
)
RAILY_MEMORY.setdefault("version", 71)
RAILY_MEMORY.setdefault("people", {})
RAILY_MEMORY.setdefault("railroads", {})
RAILY_MEMORY.setdefault("locations", {})
RAILY_MEMORY.setdefault("document_overrides", {})
RAILY_MEMORY.setdefault("updates", [])

_AUTO_BACKUP_LAST = {}


def _auto_backup_state_file(path):
    if not CFG.get("automatic_state_backups", True):
        return

    p = Path(path)

    if not p.exists():
        return

    now = time.time()
    last = _AUTO_BACKUP_LAST.get(str(p), 0)

    if now - last < 45:
        return

    _AUTO_BACKUP_LAST[str(p)] = now

    try:
        folder = BACKUP_ROOT / "Auto" / p.stem
        folder.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = folder / f"{p.stem}_{stamp}{p.suffix}"
        shutil.copy2(p, target)

        backups = sorted(
            folder.glob(f"{p.stem}_*{p.suffix}"),
            key=lambda item: item.stat().st_mtime,
            reverse=True
        )

        for old in backups[10:]:
            try:
                old.unlink()
            except Exception:
                pass

    except Exception:
        pass


def save_filing_history():
    FILING_HISTORY_FILE.write_text(
        json.dumps(FILING_HISTORY, indent=2),
        encoding="utf-8"
    )


def save_family_duplicate_history():
    _auto_backup_state_file(FAMILY_DUP_HISTORY_FILE)
    FAMILY_DUP_HISTORY_FILE.write_text(
        json.dumps(FAMILY_DUP_HISTORY, indent=2),
        encoding="utf-8"
    )


def record_filing_history(
    source_path,
    target_path,
    category="",
    family="",
    document_date=None,
    railroad="",
    location="",
    person="",
    recognition_source=""
):
    FILING_HISTORY["entries"].append({
        "source_path": str(source_path),
        "target_path": str(target_path),
        "category": clean_name(category),
        "family": clean_name(family),
        "document_date": _date_string(document_date),
        "railroad": clean_optional_name(railroad),
        "location": clean_optional_name(location),
        "person": clean_optional_name(person),
        "recognition_source": str(recognition_source or ""),
        "filed_at": datetime.now().isoformat(timespec="seconds"),
        "undone": False,
    })

    FILING_HISTORY["entries"] = FILING_HISTORY["entries"][-150:]
    save_filing_history()


def save_config():
    CONFIG_FILE.write_text(json.dumps(CFG, indent=2), encoding="utf-8")


def save_learning():
    _auto_backup_state_file(LEARNING_FILE)
    LEARNING_FILE.write_text(
        json.dumps(LEARNED, indent=2),
        encoding="utf-8"
    )


def save_index():
    INDEX_FILE.write_text(json.dumps(INDEX, indent=2), encoding="utf-8")


def save_date_profiles():
    _auto_backup_state_file(DATE_PROFILE_FILE)
    DATE_PROFILE_FILE.write_text(
        json.dumps(DATE_PROFILES, indent=2),
        encoding="utf-8"
    )


def save_document_dates():
    _auto_backup_state_file(DOCUMENT_DATE_FILE)
    DOCUMENT_DATE_FILE.write_text(
        json.dumps(DOCUMENT_DATES, indent=2),
        encoding="utf-8"
    )


def save_raily_memory():
    _auto_backup_state_file(RAILY_MEMORY_FILE)
    RAILY_MEMORY_FILE.write_text(
        json.dumps(RAILY_MEMORY, indent=2),
        encoding="utf-8"
    )


def remember_raily_entity(kind, value):
    value = clean_name(value) if value else ""
    if not value:
        return ""
    bucket = RAILY_MEMORY.setdefault(kind, {})
    key = value.casefold()
    item = bucket.setdefault(key, {"name": value, "count": 0, "last_seen": ""})
    item["name"] = value
    item["count"] = int(item.get("count", 0)) + 1
    item["last_seen"] = datetime.now().isoformat(timespec="seconds")
    save_raily_memory()
    return value


def known_raily_entities(kind):
    values = []
    for item in RAILY_MEMORY.get(kind, {}).values():
        name = clean_name(item.get("name", ""))
        if name:
            values.append(name)
    return sorted(set(values), key=lambda x: (-len(x), x.casefold()))


def save_stats():
    try:
        STATS_FILE.write_text(json.dumps(STATS, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"Stats save error: {exc}")


def record_stat(event, amount=1, recognition=None):
    """Persist lightweight daily/lifetime counters for the Dashboard."""
    if not CFG.get("persistent_dashboard_stats", True):
        return

    day_key = datetime.now().strftime("%Y-%m-%d")
    day = STATS["days"].setdefault(day_key, {})
    life = STATS["lifetime"]
    day[event] = int(day.get(event, 0)) + int(amount)
    life[event] = int(life.get(event, 0)) + int(amount)

    if recognition:
        item = dict(recognition)
        item["at"] = datetime.now().isoformat(timespec="seconds")
        STATS["recognitions"].append(item)
        STATS["recognitions"] = STATS["recognitions"][-300:]

    save_stats()


def today_stats():
    return STATS.get("days", {}).get(datetime.now().strftime("%Y-%m-%d"), {})


def ensure_release_backup(version):
    """Create one automatic rollback snapshot before a newer release writes state."""
    try:
        version = int(version)
        if int(CFG.get("last_release_backup_version", 0)) >= version:
            return None

        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        archive = BACKUP_ROOT / f"SmartScan_BEFORE_v{version}_{stamp}.zip"
        candidates = [
            CONFIG_FILE, LEARNING_FILE, INDEX_FILE, DATE_PROFILE_FILE,
            DOCUMENT_DATE_FILE, FILING_HISTORY_FILE, FAMILY_DUP_HISTORY_FILE, STATS_FILE, RAILY_MEMORY_FILE,
        ]
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for state_path in candidates:
                if Path(state_path).exists() and Path(state_path).is_file():
                    zf.write(state_path, arcname=Path(state_path).name)

        CFG["last_release_backup_version"] = version
        save_config()
        return archive
    except Exception as exc:
        log(f"Release backup warning: {exc}")
        return None


def _profile_strength(profile):
    corrections = int(profile.get("corrections", 0))
    successes = int(profile.get("auto_successes", 0))
    streak = int(profile.get("success_streak", 0))
    examples = max(
        len(profile.get("fingerprint_examples", []) or []),
        len(profile.get("structure_examples", []) or [])
    )
    score = min(100, corrections * 6 + successes * 2 + streak * 2 + examples * 3)
    if score >= 75:
        return "Strong", score
    if score >= 45:
        return "Growing", score
    return "Learning", score



def initialize_v41_safe_dates():
    """Turn safe date naming on once when upgrading from the working v40 build."""
    if CFG.get("v41_safe_dates_initialized", False):
        return

    CFG["add_date_to_filename"] = True
    CFG["date_confidence_threshold"] = max(
        92,
        int(CFG.get("date_confidence_threshold", 92))
    )
    CFG["v41_safe_dates_initialized"] = True
    save_config()


def migrate_clean_learning_once():
    """Import clean-start v24-v34 family profiles once.

    This preserves the useful family knowledge without reusing old date logic,
    folder logic, or watcher state.
    """
    if LEARNED.get("migration_done"):
        return 0

    imported = 0
    if LEGACY_CLEAN_LEARNING.exists():
        try:
            old = json.loads(LEGACY_CLEAN_LEARNING.read_text(encoding="utf-8"))
            profiles = old.get("profiles", [])
            for profile in profiles:
                category = profile.get("category", "")
                family = profile.get("family", "")
                if not category or not family:
                    continue

                duplicate = any(
                    p.get("category") == category
                    and p.get("family", "").lower() == family.lower()
                    for p in LEARNED["profiles"]
                )
                if duplicate:
                    continue

                clean = {
                    "category": category,
                    "family": family,
                    "fingerprint": profile.get("fingerprint", {}),
                    "printed_structure": profile.get("printed_structure", {}),
                    "corrections": int(profile.get("corrections", 1)),
                    "created_at": profile.get("created_at", datetime.now().isoformat(timespec="seconds")),
                    "migrated_from": LEGACY_CLEAN_LEARNING.name,
                }
                LEARNED["profiles"].append(clean)
                imported += 1
        except Exception:
            pass

    LEARNED["migration_done"] = True
    save_learning()
    return imported


def log(msg):
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def clean_name(value):
    value = re.sub(r'[<>:"/\\|?*]+', " ", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    return value[:120] or "Document"


def clean_optional_name(value):
    """Sanitize an optional label without turning blank into 'Document'."""
    if not str(value or "").strip():
        return ""
    return clean_name(value)


def register_custom_category(name, persist=True):
    """Add a user-created category without requiring a code edit."""
    cleaned = clean_optional_name(name)
    if not cleaned:
        return ""

    for existing in CATEGORY_RULES:
        if existing.casefold() == cleaned.casefold():
            return existing

    CATEGORY_RULES[cleaned] = []
    GENERIC_FAMILY.setdefault(cleaned, cleaned)

    custom = [
        clean_optional_name(item)
        for item in CFG.get("custom_categories", [])
        if clean_optional_name(item)
    ]

    if cleaned.casefold() not in {item.casefold() for item in custom}:
        custom.append(cleaned)

    CFG["custom_categories"] = sorted(
        set(custom),
        key=str.casefold
    )

    if persist:
        save_config()

    return cleaned


def register_saved_custom_categories():
    """Restore user-created categories before classification begins."""
    restored = []
    for name in CFG.get("custom_categories", []) or []:
        cleaned = register_custom_category(name, persist=False)
        if cleaned:
            restored.append(cleaned)
    return restored


# Register saved categories as soon as the helpers are available.
register_saved_custom_categories()


def canonical_numbered_filename(path_or_name):
    """Return the base filename before SmartScan's _2/_3 collision suffix.

    Examples:
      2025-06-01_IRAIL_Work_Log_2.pdf -> 2025-06-01_IRAIL_Work_Log.pdf
      2025-06-01_IRAIL_Work_Log.pdf   -> unchanged
    """
    p = Path(path_or_name)

    stem = re.sub(
        r"_([2-9]|[1-9][0-9]+)$",
        "",
        p.stem
    )

    return f"{stem}{p.suffix.lower()}"


def is_numbered_collision_copy(path_or_name):
    p = Path(path_or_name)

    return bool(
        re.search(
            r"_([2-9]|[1-9][0-9]+)$",
            p.stem
        )
    )


def dated_family_collision_key(path):
    """Group Sorted files that differ only by SmartScan's _2/_3 suffix."""
    p = Path(path)

    return (
        str(p.parent.resolve()).casefold(),
        canonical_numbered_filename(p).casefold(),
    )



def unique_path(path):
    path = Path(path)
    if not path.exists():
        return path

    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def configure_tesseract():
    configured = str(CFG.get("tesseract_path", "")).strip()
    candidates = []

    if configured:
        candidates.append(Path(configured))

    candidates.extend([
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ])

    for candidate in candidates:
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            CFG["tesseract_path"] = str(candidate)
            save_config()
            return str(candidate)

    return ""


def normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def words(text):
    return re.findall(r"[a-z0-9]{3,}", normalize_text(text))



# ============================================================================
# Windows integration / stability helpers
# ============================================================================

def current_application_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def _pythonw_executable():
    exe = Path(sys.executable)

    if os.name == "nt" and exe.name.casefold() == "python.exe":
        candidate = exe.with_name("pythonw.exe")

        if candidate.exists():
            return candidate

    return exe


def _desktop_directory():
    one_drive = os.environ.get("OneDrive")

    if one_drive:
        candidate = Path(one_drive) / "Desktop"

        if candidate.exists():
            return candidate

    candidate = Path.home() / "Desktop"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _startup_directory():
    appdata = os.environ.get("APPDATA", "")

    if appdata:
        folder = (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
    else:
        folder = (
            Path.home()
            / "AppData"
            / "Roaming"
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )

    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _desktop_shortcut_path():
    return _desktop_directory() / "SmartScan Sorter.lnk"


def _startup_shortcut_path():
    return _startup_directory() / "SmartScan Sorter.lnk"


def _ps_quote(value):
    return str(value).replace("'", "''")


def create_windows_shortcut(shortcut_path, minimized=False):
    if os.name != "nt":
        raise RuntimeError("Shortcut creation is available on Windows.")

    shortcut_path = Path(shortcut_path)
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)

    if getattr(sys, "frozen", False):
        target_exe = current_application_path()
        args = "--minimized" if minimized else ""
    else:
        target_exe = _pythonw_executable()
        script_path = Path(__file__).resolve()
        args = f'"{script_path}"' + (" --minimized" if minimized else "")

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_quote(shortcut_path)}'); "
        f"$s.TargetPath = '{_ps_quote(target_exe)}'; "
        f"$s.Arguments = '{_ps_quote(args)}'; "
        f"$s.WorkingDirectory = '{_ps_quote(APP_DIR)}'; "
        "$s.Description = 'RAILY SmartScan Railroad Document Intelligence'; "
        f"$s.WindowStyle = {7 if minimized else 1}; "
        "$s.Save();"
    )

    flags = (
        subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "CREATE_NO_WINDOW")
        else 0
    )

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=flags
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or "Windows could not create the shortcut."
        )

    return shortcut_path


_SINGLE_INSTANCE_MUTEX = None


def acquire_single_instance():
    global _SINGLE_INSTANCE_MUTEX

    if os.name != "nt":
        return True

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(
            None,
            False,
            "SmartScanSorter_v61_SingleInstance"
        )

        if kernel32.GetLastError() == 183:
            return False

        _SINGLE_INSTANCE_MUTEX = handle
        return True

    except Exception:
        return True



# ============================================================================
# OCR
# ============================================================================

def _uncached_load_page_image(path, page_index=0, scale=1.55):
    p = Path(path)

    if p.suffix.lower() == ".pdf":
        doc = fitz.open(p)
        try:
            if len(doc) == 0:
                return None
            page_index = max(0, min(page_index, len(doc) - 1))
            pix = doc[page_index].get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                alpha=False
            )
            return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        finally:
            doc.close()

    try:
        return Image.open(p).convert("RGB")
    except Exception:
        return None


# v72.2: in-memory OCR artifacts only; no learning/configuration schema changes.
_PAGE_RENDER_CACHE = {}
_PAGE_RENDER_LOCK = threading.RLock()
_STRUCTURED_OCR_LOCK = threading.RLock()
STRUCTURED_FIELD_LABELS = {
    "Name": ("employee name", "contractor name", "operator name", "name", "employee"),
    "Start Date": ("start date", "date started"),
    "Location": ("work location", "job location", "location", "terminal", "yard", "site", "station"),
}


class StructuredOCRText(str):
    """Keep the existing text API while carrying confidence and spatial evidence."""
    def __new__(cls, text, report):
        value = super().__new__(cls, text)
        value.structured = report
        return value


def _load_page_image(path, page_index=0, scale=1.55):
    key = (file_cache_key(path), int(page_index))
    with _PAGE_RENDER_LOCK:
        if key not in _PAGE_RENDER_CACHE:
            image = _uncached_load_page_image(path, page_index, scale=2.25)
            if image is None:
                return None
            _PAGE_RENDER_CACHE[key] = image
            while len(_PAGE_RENDER_CACHE) > 6:
                _PAGE_RENDER_CACHE.pop(next(iter(_PAGE_RENDER_CACHE)))
        image = _PAGE_RENDER_CACHE[key]
        if Path(path).suffix.lower() == ".pdf" and scale != 2.25:
            ratio = scale / 2.25
            return image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                                getattr(Image, "Resampling", Image).LANCZOS)
        return image.copy()


def _handwriting_images(crop):
    """Lazy, bounded preparation shared by taught dates and labeled fields."""
    from PIL import ImageFilter
    if crop.width > 1800 or crop.height > 600:
        crop = crop.copy()
        crop.thumbnail((1800, 600), getattr(Image, "Resampling", Image).LANCZOS)
    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    cleaned = _remove_horizontal_lines(gray)
    bounds = ImageOps.invert(cleaned).point(lambda v: 255 if v > 75 else 0).getbbox()
    if bounds:
        x0,y0,x1,y1 = bounds
        bounds = (max(0,x0-6),max(0,y0-5),min(crop.width,x1+6),min(crop.height,y1+5))
        crop, gray, cleaned = crop.crop(bounds), gray.crop(bounds), cleaned.crop(bounds)
    factor = 3.5 if crop.width < 650 and crop.height < 250 else min(2.0, 1800 / max(crop.size))
    factor = max(1.0, factor)
    size = (max(1, round(crop.width * factor)), max(1, round(crop.height * factor)))
    resample = getattr(Image, "Resampling", Image).LANCZOS
    # Remove rules before enlargement so strokes do not grow into the rules.
    yield "line-clean-3.5x", cleaned.resize(size, resample)
    yield "gray-3.5x", gray.resize(size, resample)
    blue = _blue_ink_mask(crop) if CFG.get("handwritten_date_mode", True) else None
    if blue is not None and ImageOps.invert(blue).getbbox():
        yield "blue-ink-3.5x", blue.resize(size, resample)
        bridged = blue.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
        yield "blue-ink-line-repair", bridged.resize(size, resample)
    smooth = cleaned.filter(ImageFilter.MedianFilter(3))
    sharpened = smooth.resize(size, resample).filter(ImageFilter.UnsharpMask(radius=1, percent=110, threshold=3))
    yield "denoise-sharpen", sharpened
    yield "dark-ink-threshold", sharpened.point(lambda v: 255 if v > 165 else 0)


def _ocr_words(image, config="--psm 3", timeout=6.0):
    data = pytesseract.image_to_data(image, lang=CFG.get("ocr_language", "eng"), config=config,
                                   output_type=pytesseract.Output.DICT, timeout=timeout)
    words_out = []
    for i, raw in enumerate(data.get("text", [])):
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            confidence = float(data["conf"][i])
            x, y, w, h = (int(data[k][i]) for k in ("left", "top", "width", "height"))
        except (ValueError, IndexError, KeyError, TypeError):
            continue
        words_out.append({"text": raw, "confidence": confidence, "box": (x, y, x+w, y+h),
                          "block": int(data.get("block_num", [0]*len(data["text"]))[i]),
                          "line": int(data.get("line_num", [0]*len(data["text"]))[i])})
    return words_out


def _ocr_rows(words):
    """Geometric rows, independent of Tesseract's sometimes scrambled blocks."""
    rows = []
    for word in sorted(words, key=lambda w: ((w["box"][1]+w["box"][3])/2, w["box"][0])):
        box = word["box"]
        center = (box[1]+box[3])/2
        row = next((r for r in reversed(rows[-4:]) if abs(r["center"]-center) <= max(5, (box[3]-box[1])*.55)), None)
        if row is None:
            row = {"center": center, "words": []}
            rows.append(row)
        row["words"].append(word)
    for row in rows:
        row["words"].sort(key=lambda w: w["box"][0])
    return sorted(rows, key=lambda r: r["center"])


def _ocr_row_text(row):
    output, previous = [], None
    for word in row["words"]:
        box = word["box"]
        if previous is not None:
            gap = box[0]-previous[2]
            output.append(" | " if gap > max(32, (box[3]-box[1])*2.2) else " ")
        output.append(word["text"])
        previous = box
    return "".join(output)


def _ocr_normalize_page(image):
    """Conservative orientation and modest deskew; coordinates use this image."""
    rotation = 0
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT, timeout=2.0)
        if float(osd.get("orientation_conf", 0)) >= 8:
            rotation = int(osd.get("rotate", 0)) % 360
            if rotation in (90, 180, 270):
                image = image.rotate(-rotation, expand=True, fillcolor="white")
    except Exception:
        pass
    small = ImageOps.autocontrast(ImageOps.grayscale(image))
    small.thumbnail((800, 800))
    def score(angle):
        candidate = small.rotate(angle, fillcolor=255).point(lambda p: 255 if p > 145 else 0)
        counts = [candidate.crop((0, y, candidate.width, y+1)).histogram()[0] for y in range(candidate.height)]
        return sum((b-a)**2 for a, b in zip(counts, counts[1:]))
    baseline = score(0)
    candidates = [(score(angle), angle) for angle in (-3, -1.5, 1.5, 3)]
    best, skew = max(candidates)
    if baseline and best > baseline*1.35:
        image = image.rotate(skew, resample=getattr(Image, "Resampling", Image).BICUBIC, expand=True, fillcolor="white")
    else:
        skew = 0
    return image, {"orientation": rotation, "deskew": skew}


def _ocr_table_box(image):
    gray = ImageOps.grayscale(image)
    gray.thumbnail((900, 1100))
    binary = gray.point(lambda p: 255 if p > 155 else 0)
    rows = []
    for y in range(binary.height):
        if binary.crop((0, y, binary.width, y+1)).histogram()[0] > binary.width*.42:
            if not rows or y > rows[-1]+4:
                rows.append(y)
    if len(rows) < 3:
        return None
    columns = []
    for x in range(binary.width):
        if binary.crop((x,rows[0],x+1,rows[-1]+1)).histogram()[0] > (rows[-1]-rows[0])*.65:
            if not columns or x > columns[-1]+4:
                columns.append(x)
    if len(columns) < 2:
        return None
    # Repeated long rules indicate a table; a lone field underline does not.
    return (0, int(rows[0]*image.height/binary.height), image.width,
            int((rows[-1]+3)*image.height/binary.height))


def _field_valid(label, text):
    text = re.sub(r"\s+", " ", text).strip(" :_|.,")
    if not text or len(text) > 80 or "|" in text:
        return ""
    if any(re.fullmatch(re.escape(alias), text, re.I) or re.search(r"\b"+re.escape(alias)+r"\s*:", text, re.I)
           for aliases in STRUCTURED_FIELD_LABELS.values() for alias in aliases):
        return ""
    if label == "Start Date":
        dates = _date_candidates(text)
        return dates[0][0].strftime("%Y-%m-%d") if len(dates) == 1 else ""
    if len(re.findall(r"[A-Za-z]", text)) < 2 or re.search(r"[^\w\s.'’/&(),-]", text):
        return ""
    if label == "Name" and re.search(r"\d", text):
        return ""
    return text


def _ocr_field_anchors(words):
    anchors = []
    for row in _ocr_rows([w for w in words if w["confidence"] >= 65]):
        items = row["words"]
        occupied = set()
        for label, aliases in STRUCTURED_FIELD_LABELS.items():
            for alias in aliases:
                tokens = alias.split()
                for i in range(len(items)-len(tokens)+1):
                    chunk = items[i:i+len(tokens)]
                    if any(i+j in occupied for j in range(len(tokens))):
                        continue
                    if [re.sub(r"[^a-z]", "", w["text"].lower()) for w in chunk] != tokens:
                        continue
                    if i and not chunk[-1]["text"].endswith(":"):
                        gap = chunk[0]["box"][0]-items[i-1]["box"][2]
                        if gap < (chunk[0]["box"][3]-chunk[0]["box"][1])*3:
                            continue
                    box = (chunk[0]["box"][0], min(w["box"][1] for w in chunk), chunk[-1]["box"][2], max(w["box"][3] for w in chunk))
                    anchors.append({"label": label, "box": box, "confidence": min(w["confidence"] for w in chunk)})
                    occupied.update(range(i, i+len(tokens)))
    return anchors


def _ocr_labeled_fields(image, words, table_box, deadline):
    anchors = _ocr_field_anchors(words)
    fields, calls = {}, 0
    for anchor in anchors:
        label, box = anchor["label"], anchor["box"]
        if table_box and table_box[1] <= box[1] <= table_box[3]:
            continue
        if label in fields and fields[label].get("trusted"):
            continue
        h = max(12, box[3]-box[1])
        neighbors = [a["box"][0] for a in anchors if abs(a["box"][1]-box[1]) < h and a["box"][0] > box[2]]
        right = min(neighbors, default=image.width-8)
        lower = min((a["box"][1] for a in anchors if a["box"][1] > box[3]+h*.3), default=image.height)
        if table_box and table_box[1] > box[3]:
            lower = min(lower, table_box[1])
        regions = [(box[2]+4, max(0, box[1]-int(h*.65)), right-4, min(lower, box[3]+int(h*.65))),
                   (box[0], box[3]+3, right-4, min(lower-3, box[3]+h*3))]
        best = {"value": "", "confidence": 0, "trusted": False, "label_box": box, "method": "unread"}
        votes = {}
        field_calls = 0
        for region in regions:
            x0,y0,x1,y1 = map(int, region)
            if x1-x0 < 15 or y1-y0 < 8:
                continue
            selected = [w for w in words if x0 <= (w["box"][0]+w["box"][2])/2 < x1 and y0 <= (w["box"][1]+w["box"][3])/2 < y1]
            value = _field_valid(label, " ".join(w["text"] for r in _ocr_rows(selected) for w in r["words"]))
            confidence = min((w["confidence"] for w in selected), default=0)
            if value and confidence >= 90:
                best = {"value": value, "confidence": 98 if label == "Start Date" else confidence,
                        "trusted": True, "label_box": box, "value_box": region, "method": "page coordinates"}
                break
            crop = image.crop((x0,y0,x1,y1))
            ink = ImageOps.invert(_remove_horizontal_lines(ImageOps.grayscale(crop))).point(lambda v: 255 if v > 90 else 0)
            if not ink.getbbox():
                continue
            # At most four calls for one field, ten for this page, and a deadline.
            configs = ("--psm 7", "--psm 13")
            for method, prepared in _handwriting_images(crop):
                remaining = deadline-time.monotonic()
                if calls >= 10 or field_calls >= 4 or remaining <= .05:
                    break
                config = configs[field_calls % 2]
                if label == "Start Date":
                    config += " -c tessedit_char_whitelist=0123456789/-."
                calls += 1
                field_calls += 1
                try:
                    candidate_words = _ocr_words(prepared, config, timeout=min(2.5, remaining))
                except Exception:
                    continue
                candidate = _field_valid(label, " ".join(w["text"] for w in candidate_words))
                conf = min((w["confidence"] for w in candidate_words), default=0)
                if not candidate or conf < (0 if label == "Start Date" else 60):
                    continue
                key = candidate.casefold()
                votes[key] = votes.get(key, 0)+1
                trusted = (conf >= 94 and label != "Start Date") or votes[key] >= 2
                if conf > best["confidence"] or trusted:
                    best = {"value": candidate, "confidence": (98 if label == "Start Date" else 92) if trusted else conf,
                            "trusted": trusted, "label_box": box, "value_box": region, "method": method}
                if trusted:
                    break
            if best["trusted"]:
                break
        best["regions"] = [best["value_box"]] if best["trusted"] else regions
        fields[label] = best
    return fields, calls


def _structured_page(image, page_index=0, embedded_words=None):
    started = time.monotonic()
    if embedded_words:
        orientation = {"orientation": 0, "deskew": 0, "source": "PDF text layer"}
        words = embedded_words
    else:
        image, orientation = _ocr_normalize_page(image)
        gray = ImageOps.autocontrast(ImageOps.grayscale(image))
        words = _ocr_words(gray)
    # One sparse printed retry only if automatic page segmentation found little.
    if not embedded_words and len([w for w in words if w["confidence"] >= 65]) < 4:
        retry = _ocr_words(gray, "--psm 11")
        if sum(w["confidence"] >= 65 for w in retry) > sum(w["confidence"] >= 65 for w in words):
            words = retry
    table = _ocr_table_box(image)
    fields, field_calls = _ocr_labeled_fields(image, words, table, time.monotonic()+10)
    reliable, uncertain, table_words = [], [], []
    for word in words:
        text = word["text"]
        cx, cy = (word["box"][0]+word["box"][2])/2, (word["box"][1]+word["box"][3])/2
        field_value = next((f for f in fields.values() if any(r[0] <= cx < r[2] and r[1] <= cy < r[3] for r in f["regions"])), None)
        if field_value:
            if not field_value["trusted"]:
                uncertain.append(word)
        elif word["confidence"] < 65 or not re.search(r"[A-Za-z0-9]", text):
            uncertain.append(word)
        elif table and table[1] <= word["box"][1] <= table[3]:
            table_words.append(word)
        else:
            reliable.append(word)
    printed = [_ocr_row_text(r) for r in _ocr_rows(reliable)]
    anchor_boxes = [f["label_box"] for f in fields.values()]
    safe_words = [w for w in reliable if not any(b[0] <= (w["box"][0]+w["box"][2])/2 <= b[2] and b[1] <= (w["box"][1]+w["box"][3])/2 <= b[3] for b in anchor_boxes)]
    safe_text = [_ocr_row_text(r) for r in _ocr_rows(safe_words)]
    table_text = [_ocr_row_text(r) for r in _ocr_rows(table_words)]
    elapsed = time.monotonic()-started
    log(f"Structured OCR page {page_index+1}: {len(words)} words, {field_calls} field OCR calls, {elapsed:.3f}s")
    header = [_ocr_row_text(row) for row in _ocr_rows(reliable) if row["center"] < image.height*.2]
    return {"page": page_index, "fields": fields, "header": header, "printed": printed, "safe_text": safe_text, "table": table_text,
            "low_confidence": uncertain, "words": words, "orientation": orientation,
            "table_box": table, "field_calls": field_calls, "seconds": elapsed}


def ocr_file(path):
    """Structured recognition of the same first two pages used by prior releases."""
    count = min(2, document_page_count(path))
    pages = []
    for index in range(count):
        image = _load_page_image(path, index, scale=2.25)
        if image is not None:
            embedded = []
            if Path(path).suffix.lower() == ".pdf":
                with fitz.open(path) as document:
                    page = document[index]
                    native = page.get_text("words")
                    if sum(len(w[4]) for w in native) >= 140:
                        for x0,y0,x1,y1,text,block,line,*_ in native:
                            box = fitz.Rect(x0,y0,x1,y1) * page.rotation_matrix
                            embedded.append({"text": text, "confidence": 100, "block": block, "line": line,
                                             "box": tuple(int(v*2.25) for v in box)})
            pages.append(_structured_page(image, index, embedded_words=embedded))
    fields = {}
    for page in pages:
        for label, item in page["fields"].items():
            if label not in fields:
                fields[label] = dict(item)
            elif item["trusted"] and not fields[label]["trusted"] and fields[label].get("method") != "conflicting pages":
                fields[label] = dict(item)
            elif item["trusted"] and fields[label]["trusted"] and item["value"].casefold() != fields[label]["value"].casefold():
                fields[label] = dict(item, trusted=False, method="conflicting pages")
    printed = "\n".join(line for page in pages for line in page["printed"])
    field_lines = "\n".join(f"{label}: {item['value']}" for label,item in fields.items() if item["trusted"])
    table_text = "\n".join(line for page in pages for line in page["table"])
    report = {"pages": pages, "fields": fields, "classification_text": printed+"\n"+field_lines}
    safe_text = "\n".join(line for page in pages for line in page["safe_text"])
    return StructuredOCRText(safe_text+"\n"+field_lines+"\n"+table_text, report)


def structured_ocr_preview(text):
    report = getattr(text, "structured", None)
    if not report:
        return "RAILY STRUCTURED OCR\n\nStructured evidence is not available for this cached text."
    fields = report["fields"]
    output = ["RAILY STRUCTURED OCR", "", "DETECTED FIELDS"]
    for label in STRUCTURED_FIELD_LABELS:
        item = fields.get(label, {})
        output.append(f"{label}: {item['value']} ({item['confidence']:.0f}%)" if item.get("trusted") else f"{label}: Not confidently read — review needed")
    output += ["", "PRINTED PAGE TEXT"]
    for page in report["pages"]:
        output += [f"Page {page['page']+1}"] + page["printed"]
    output += ["", "TABLE / HANDWRITING", "Columns are separated by |; uncertain fields are excluded from filing."]
    for page in report["pages"]:
        output += page["table"]
    output += ["", "LOW CONFIDENCE", "Not used as reliable text:"]
    for page in report["pages"]:
        output += [f"{word['text']} [{word['confidence']:.0f}%]" for word in page["low_confidence"][:80]]
        output += [f"{label}: {item['value']} [unconfirmed]" for label,item in page["fields"].items() if item.get("value") and not item["trusted"]]
    return "\n".join(output)


def _defer_review_ocr(win, path, continuation, selection):
    """Warm OCR on a worker; never act on a selection changed during the read."""
    if isinstance(_OCR_CACHE.get(file_cache_key(path)), StructuredOCRText):
        return False
    if getattr(win, "_ocr_pending", False):
        return True
    win._ocr_pending = True
    def worker():
        try:
            get_cached_ocr(path)
            error = None
        except Exception as exc:
            error = str(exc)
        def ready():
            if not win.winfo_exists():
                return
            win._ocr_pending = False
            if error:
                messagebox.showerror("OCR Error", error, parent=win)
            elif selection() == path:
                continuation()
        try:
            win.after(0, ready)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True, name="RAILYReviewOCR").start()
    return True


# ============================================================================
# Form fingerprints / learning
# ============================================================================

_STRUCTURE_CACHE = {}
_OCR_CACHE = {}


def file_cache_key(path):
    p = Path(path)
    try:
        st = p.stat()
        return (str(p.resolve()).lower(), st.st_size, st.st_mtime_ns)
    except Exception:
        return (str(p).lower(), 0, 0)


def get_cached_ocr(path):
    key = file_cache_key(path)
    if key in _OCR_CACHE and isinstance(_OCR_CACHE[key], StructuredOCRText):
        return _OCR_CACHE[key]

    with _STRUCTURED_OCR_LOCK:
        if isinstance(_OCR_CACHE.get(key), StructuredOCRText):
            return _OCR_CACHE[key]
        text = ocr_file(path)
        _OCR_CACHE[key] = text
        while len(_OCR_CACHE) > 24:
            _OCR_CACHE.pop(next(iter(_OCR_CACHE)))
        return text


def fingerprint(text):
    text = getattr(text, "structured", {}).get("classification_text", text)
    toks = [w for w in words(text) if w not in STOPWORDS]
    token_set = set(toks[:1200])

    lines = [
        re.sub(r"\s+", " ", line.lower()).strip()
        for line in str(text or "").splitlines()
        if line.strip()
    ]

    headers = set()
    for line in lines[:25]:
        headers.update(
            w for w in words(line)
            if w not in STOPWORDS
        )

    phrases = set()
    filtered = [w for w in toks if w not in STOPWORDS][:500]
    for i in range(len(filtered) - 1):
        phrases.add(filtered[i] + " " + filtered[i + 1])

    return {
        "tokens": sorted(token_set)[:250],
        "headers": sorted(headers)[:120],
        "phrases": sorted(phrases)[:180],
    }


def fp_similarity(a, b):
    at, bt = set(a.get("tokens", [])), set(b.get("tokens", []))
    ah, bh = set(a.get("headers", [])), set(b.get("headers", []))
    ap, bp = set(a.get("phrases", [])), set(b.get("phrases", []))

    def jaccard(x, y):
        if not x or not y:
            return 0.0
        return len(x & y) / len(x | y)

    return 100 * (
        0.45 * jaccard(at, bt)
        + 0.35 * jaccard(ah, bh)
        + 0.20 * jaccard(ap, bp)
    )


def printed_structure_fingerprint(path, fallback_text=""):
    if not CFG.get("printed_structure_learning", True):
        return {"tokens": [], "anchors": []}

    key = file_cache_key(path)
    if key in _STRUCTURE_CACHE:
        return _STRUCTURE_CACHE[key]

    evidence = get_cached_ocr(path)
    printed = []
    report = getattr(evidence, "structured", {})
    min_conf = float(CFG.get("printed_ocr_min_confidence", 55))
    for page in report.get("pages", [])[:1]:
        table = page.get("table_box")
        for word in page["words"]:
            if word["confidence"] < max(65, min_conf):
                continue
            if table and table[1] <= word["box"][1] <= table[3]:
                continue
            raw = word["text"]
            if re.search(r"\d", raw):
                continue
            token = re.sub(r"[^A-Za-z]+", "", raw).lower()
            if len(token) >= 3 and token not in STRUCTURE_STOPWORDS:
                printed.append(token)

    printed_text = " ".join(printed)
    anchor_haystack = normalize_text(printed_text + "\n" + str(fallback_text or ""))

    anchors = sorted({
        phrase for phrase in STRUCTURE_ANCHORS
        if phrase in anchor_haystack
    })

    tokens = []
    seen = set()

    for token in printed:
        if token not in seen:
            seen.add(token)
            tokens.append(token)

    result = {
        "tokens": tokens[:180],
        "anchors": anchors[:60],
    }

    _STRUCTURE_CACHE[key] = result
    return result


def structure_similarity(a, b):
    at, bt = set(a.get("tokens", [])), set(b.get("tokens", []))
    aa, ba = set(a.get("anchors", [])), set(b.get("anchors", []))

    def containment(x, y):
        if not x or not y:
            return 0.0
        return len(x & y) / max(1, min(len(x), len(y)))

    token_part = containment(at, bt)
    anchor_part = containment(aa, ba)

    return 100 * (
        0.25 * token_part
        + 0.75 * anchor_part
    )


def structure_summary(fp):
    if fp.get("anchors"):
        return ", ".join(fp["anchors"][:8])

    if fp.get("tokens"):
        return "printed words: " + ", ".join(fp["tokens"][:8])

    return "(no reliable printed structure found)"


def filename_category_boost(category, filename):
    stem = normalize_text(
        Path(filename or "").stem.replace("_", " ").replace("-", " ")
    )

    for hint in FILENAME_CATEGORY_HINTS.get(category, ()):
        if normalize_text(hint) in stem:
            return 60, 3

    return 0, 0


def expense_report_structure_boost(text, filename=""):
    hay = normalize_text(text)
    stem = normalize_text(
        Path(filename or "").stem.replace("_", " ").replace("-", " ")
    )

    signals = (
        "company name",
        "business purpose",
        "purpose",
        "department",
        "dept",
        "transportation",
        "lodging",
        "meals",
        "mileage",
        "receipts",
        "reimbursement",
        "employee",
        "approved by",
        "submitted by",
    )

    hits = sum(1 for signal in signals if signal in hay)

    if "expense report" in stem and hits >= 2:
        return 40, min(5, hits)

    if hits >= 5 and (
        "reimbursement" in hay
        or "business purpose" in hay
    ):
        return 30, min(5, hits)

    return 0, 0


def learned_category_scores(text):
    fp = fingerprint(text)
    scores = {}

    for profile in LEARNED["profiles"]:
        category = profile.get("category", "")
        if not category:
            continue

        score = fp_similarity(
            fp,
            profile.get("fingerprint", {})
        )

        weight = max(
            1,
            int(profile.get("corrections", 1))
        )

        scores[category] = max(
            scores.get(category, 0),
            score + min(20, weight * 4)
        )

    return scores


def classify(text, filename=""):
    text = getattr(text, "structured", {}).get("classification_text", text)
    hay = normalize_text(
        str(text or "") + "\n" + Path(filename).stem
    )

    learned = learned_category_scores(text)
    ranked = []

    for category, keys in CATEGORY_RULES.items():
        raw = 0
        hits = 0

        for key in keys:
            if key in hay:
                hits += 1
                raw += 7 if " " in key else 3

        fboost, fhits = filename_category_boost(category, filename)
        raw += fboost
        hits += fhits

        if category == "Expense Reports":
            eboost, ehits = expense_report_structure_boost(text, filename)
            raw += eboost
            hits += ehits

        raw += learned.get(category, 0)
        ranked.append((category, raw, hits))

    ranked.sort(key=lambda row: row[1], reverse=True)

    category, score, hits = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0

    if score <= 0:
        return "Review", 0, ranked

    margin = score - second
    confidence = int(
        min(
            99,
            48
            + hits * 7
            + min(30, margin * 1.4)
        )
    )

    if learned.get(category, 0) >= 55:
        confidence = max(confidence, 90)

    if category == "Expense Reports":
        fboost, _ = filename_category_boost(category, filename)
        eboost, _ = expense_report_structure_boost(text, filename)

        if fboost and eboost:
            confidence = max(confidence, 94)

    return category, confidence, ranked


def _family_identity_score(profile, text):
    """Score distinctive learned names printed on the page.

    v65 also checks a variant's parent family, so a layout named
    ``LENTEGRITY PayNearMe`` can still get the strong LENTEGRITY identity
    signal while its structure distinguishes the PayNearMe variant.
    """
    hay = normalize_text(text)
    names = [
        clean_name(profile.get("family", "")),
        clean_optional_name(profile.get("parent_family", "")),
    ]
    generic = {
        "receipt", "receipts", "invoice", "statement", "bill", "form",
        "document", "report", "log", "pay statement", "mileage log",
        "expense report", "tax document", "railroad document",
    }
    best = 0
    hay_tokens = set(words(hay))

    for family in names:
        fam = normalize_text(family)
        if not fam or len(fam) < 5 or fam in generic:
            continue
        if fam in hay:
            best = max(best, 96)
            continue
        fam_tokens = [
            token for token in words(fam)
            if len(token) >= 4 and token not in STOPWORDS
        ]
        if not fam_tokens:
            continue
        hits = sum(1 for token in fam_tokens if token in hay_tokens)
        if hits == len(fam_tokens):
            best = max(best, 92)
        elif hits >= 2 and hits / max(1, len(fam_tokens)) >= 0.67:
            best = max(best, 84)

    return best

def _learned_fingerprint_examples(profile):
    """Return current + historical OCR fingerprints for one learned family."""
    examples = []

    current = profile.get("fingerprint", {})
    if current:
        examples.append(current)

    for item in profile.get("fingerprint_examples", []) or []:
        if isinstance(item, dict) and item:
            examples.append(item)

    return examples[:12]


def _learned_structure_examples(profile):
    """Return current + historical printed-structure examples."""
    examples = []

    current = profile.get("printed_structure", {})
    if current:
        examples.append(current)

    for item in profile.get("structure_examples", []) or []:
        if isinstance(item, dict) and item:
            examples.append(item)

    return examples[:12]


def family_match_breakdown(profile, text, path=None):
    """Explain the major signals used for one learned family match."""
    normal_fp = fingerprint(text)
    current_structure = (
        printed_structure_fingerprint(path, text)
        if path
        else {"tokens": [], "anchors": []}
    )

    fp_examples = _learned_fingerprint_examples(profile)
    legacy = max(
        [fp_similarity(normal_fp, example) for example in fp_examples],
        default=0
    )

    structure_examples = _learned_structure_examples(profile)
    structural = 0
    if structure_examples and (
        current_structure.get("tokens") or current_structure.get("anchors")
    ):
        structural = max(
            structure_similarity(current_structure, example)
            for example in structure_examples
        )

    identity = _family_identity_score(profile, text)
    if structural:
        base = 0.82 * structural + 0.18 * legacy
        dominant = "printed structure"
    else:
        base = legacy
        dominant = "OCR fingerprint"

    if identity > base:
        base = identity
        dominant = "printed identity/name"

    corrections = int(profile.get("corrections", 1))
    correction_bonus = min(8.0, corrections * 1.5)

    growth_bonus = 0.0
    if CFG.get("auto_confidence_growth", True) and base >= 70:
        successes = int(profile.get("auto_successes", 0))
        streak = int(profile.get("success_streak", 0))
        growth_bonus = min(5.0, successes * 0.25 + streak * 0.35)

    total = min(99, int(round(base + correction_bonus + growth_bonus)))
    parent = clean_optional_name(profile.get("parent_family", ""))
    variant = clean_optional_name(profile.get("variant_name", ""))

    return {
        "family": parent or clean_name(profile.get("family", "")),
        "learned_profile": clean_name(profile.get("family", "")),
        "parent_family": parent,
        "variant": variant,
        "ocr": round(float(legacy), 1),
        "structure": round(float(structural), 1),
        "identity": round(float(identity), 1),
        "correction_bonus": round(float(correction_bonus), 1),
        "growth_bonus": round(float(growth_bonus), 1),
        "dominant": dominant,
        "total": total,
        "examples": max(len(fp_examples), len(structure_examples)),
        "successes": int(profile.get("auto_successes", 0)),
        "streak": int(profile.get("success_streak", 0)),
        "last_seen": profile.get("last_seen", ""),
    }


def record_recognition_success(category, family, confidence=0, source="", profile_name=""):
    """Strengthen a learned family only after a document was actually filed."""
    if not CFG.get("auto_confidence_growth", True):
        return

    target = clean_name(family).casefold()
    candidates = []
    for profile in LEARNED.get("profiles", []):
        if profile.get("category") != category:
            continue
        profile_family = clean_name(profile.get("family", ""))
        parent = clean_optional_name(profile.get("parent_family", ""))
        if target in {profile_family.casefold(), parent.casefold()}:
            candidates.append(profile)

    if not candidates:
        return

    profile = None
    if profile_name:
        wanted = clean_name(profile_name).casefold()
        profile = next(
            (row for row in candidates if clean_name(row.get("family", "")).casefold() == wanted),
            None
        )

    # If no exact variant was supplied, update the most recently taught
    # matching profile rather than strengthening every sibling variant.
    if profile is None:
        profile = sorted(
            candidates,
            key=lambda row: row.get("updated_at", row.get("created_at", "")),
            reverse=True
        )[0]

    profile["auto_successes"] = int(profile.get("auto_successes", 0)) + 1
    profile["success_streak"] = min(25, int(profile.get("success_streak", 0)) + 1)
    profile["last_seen"] = datetime.now().isoformat(timespec="seconds")
    profile["last_confidence"] = int(confidence or 0)
    profile["last_source"] = str(source or "")
    save_learning()


def match_learned_family(category, text, path=None):
    normal_fp = fingerprint(text)

    # v64: if this is already an individually split PDF, its own first page is
    # safe to inspect.  Older builds disabled structure matching for split files,
    # which caused repeatedly taught document types to fall back to 45%.
    current_structure = (
        printed_structure_fingerprint(path, text)
        if path
        else {"tokens": [], "anchors": []}
    )

    best = None
    best_score = 0
    best_source = "learned OCR"

    for profile in LEARNED["profiles"]:
        if profile.get("category") != category:
            continue

        fp_examples = _learned_fingerprint_examples(profile)
        legacy_scores = [
            fp_similarity(normal_fp, example)
            for example in fp_examples
        ]
        legacy = max(legacy_scores, default=0)

        structural = 0
        structure_examples = _learned_structure_examples(profile)
        if structure_examples and (
            current_structure.get("tokens")
            or current_structure.get("anchors")
        ):
            structural = max(
                structure_similarity(current_structure, example)
                for example in structure_examples
            )

        identity = _family_identity_score(profile, text)

        if structural:
            # Stable printed form layout is dominant, while OCR similarity still
            # helps distinguish related forms that share labels.
            score = 0.82 * structural + 0.18 * legacy
            source = "learned printed structure"
        else:
            score = legacy
            source = "learned OCR"

        # A printed vendor/form identity is stronger than changing account data.
        if identity > score:
            score = identity
            source = "learned document identity"

        corrections = int(profile.get("corrections", 1))
        score += min(8, corrections * 1.5)

        # v65: a history of correctly filed matches can add only a small,
        # capped boost. The underlying content/layout must already be strong.
        if CFG.get("auto_confidence_growth", True) and score >= 70:
            successes = int(profile.get("auto_successes", 0))
            streak = int(profile.get("success_streak", 0))
            score += min(5, successes * 0.25 + streak * 0.35)

        if score > best_score:
            best_score = score
            best = profile
            best_source = source

    if best:
        resolved_family = clean_optional_name(best.get("parent_family", "")) or best.get("family", "")
        variant = clean_optional_name(best.get("variant_name", ""))
        if variant:
            best_source = f"{best_source}; variant {variant}"
        return (
            resolved_family,
            min(99, int(round(best_score))),
            best_source,
            best,
        )

    return "", 0, "none", None


def match_standard_family(category, text):
    hay = normalize_text(text)

    best_family = ""
    best_score = 0

    for family, terms in STANDARD_FAMILIES.get(category, []):
        hits = sum(
            1 for term in terms
            if normalize_text(term) in hay
        )

        if hits == 0:
            continue

        required = 1 if len(terms) <= 2 else 2
        if hits < required:
            continue

        score = min(94, 55 + hits * 10)

        if score > best_score:
            best_family = family
            best_score = score

    if best_family:
        return best_family, best_score, "standard"

    return "", 0, "none"


def resolve_family(category, text, path=None):
    learned_family, learned_score, learned_source, profile = match_learned_family(
        category,
        text,
        path
    )

    if (
        learned_family
        and learned_score >= int(CFG.get("family_threshold", 50))
    ):
        return learned_family, learned_score, learned_source

    standard_family, standard_score, standard_source = match_standard_family(
        category,
        text
    )

    if standard_family:
        return standard_family, standard_score, standard_source

    fallback = GENERIC_FAMILY.get(category, category or "Document")

    one_to_one = {
        "Expense Reports": 88,
        "Mileage Logs": 85,
        "Pay Statements": 85,
    }

    fallback_conf = one_to_one.get(category, 45)
    fallback_source = (
        "category-defined family"
        if category in one_to_one
        else "category fallback"
    )

    return fallback, fallback_conf, fallback_source


def teach_family(path, category, family, railroad="", location="", person=""):
    """Teach one family using OCR plus RAILY's whole-page visual memory."""
    text = get_cached_ocr(path)
    regular_fp = fingerprint(text)
    struct_fp = printed_structure_fingerprint(path, text)
    visual_sig = raily_full_page_signature(path)
    family = clean_name(family)

    inferred = raily_extract_metadata(text)
    railroad = _raily_clean_entity(railroad or inferred.get("railroad", ""), "railroad")
    location = _raily_clean_entity(location or inferred.get("location", ""), "location")
    person = _raily_clean_entity(person or inferred.get("person", ""), "person")

    if railroad:
        remember_raily_entity("railroads", railroad)
    if location:
        remember_raily_entity("locations", location)
    if person:
        remember_raily_entity("people", person)

    existing = None

    for profile in LEARNED["profiles"]:
        if profile.get("category") != category:
            continue
        direct = clean_name(profile.get("family", "")).lower()
        parent = clean_optional_name(profile.get("parent_family", "")).lower()
        if family.lower() in {direct, parent}:
            existing = profile
            break

    if existing:
        # v64: repeated teaching now ADDS examples instead of replacing the one
        # previous example.  This is the important fix for forms whose amounts,
        # dates, account numbers, or OCR quality change from scan to scan.
        fp_examples = list(existing.get("fingerprint_examples", []) or [])
        previous_fp = existing.get("fingerprint", {})
        if previous_fp:
            fp_examples.append(previous_fp)
        fp_examples.append(regular_fp)

        # Keep a small rolling training set and de-duplicate exact JSON shapes.
        unique_fp = []
        seen_fp = set()
        for example in reversed(fp_examples):
            try:
                marker = json.dumps(example, sort_keys=True)
            except Exception:
                continue
            if marker in seen_fp:
                continue
            seen_fp.add(marker)
            unique_fp.append(example)
            if len(unique_fp) >= 10:
                break
        unique_fp.reverse()

        structure_examples = list(existing.get("structure_examples", []) or [])
        previous_struct = existing.get("printed_structure", {})
        if previous_struct:
            structure_examples.append(previous_struct)
        if struct_fp:
            structure_examples.append(struct_fp)

        unique_struct = []
        seen_struct = set()
        for example in reversed(structure_examples):
            try:
                marker = json.dumps(example, sort_keys=True)
            except Exception:
                continue
            if marker in seen_struct:
                continue
            seen_struct.add(marker)
            unique_struct.append(example)
            if len(unique_struct) >= 10:
                break
        unique_struct.reverse()

        visual_examples = list(existing.get("visual_examples", []) or [])
        previous_visual = existing.get("visual_signature", {})
        if previous_visual:
            visual_examples.append(previous_visual)
        if visual_sig:
            visual_examples.append(visual_sig)

        unique_visual = []
        seen_visual = set()
        limit_visual = max(3, int(CFG.get("raily_visual_memory_examples", 12)))
        for example in reversed(visual_examples):
            try:
                marker = json.dumps(example, sort_keys=True)
            except Exception:
                continue
            if marker in seen_visual:
                continue
            seen_visual.add(marker)
            unique_visual.append(example)
            if len(unique_visual) >= limit_visual:
                break
        unique_visual.reverse()

        # Merge anchors so one imperfect scan does not erase a good printed label.
        old_struct = existing.get("printed_structure", {})
        old_anchors = set(old_struct.get("anchors", []))
        new_anchors = set(struct_fp.get("anchors", []))

        existing["fingerprint"] = regular_fp
        existing["fingerprint_examples"] = unique_fp
        existing["printed_structure"] = {
            "tokens": struct_fp.get("tokens", [])[:180],
            "anchors": sorted(old_anchors | new_anchors)[:60],
        }
        existing["structure_examples"] = unique_struct
        if visual_sig:
            existing["visual_signature"] = visual_sig
            existing["visual_examples"] = unique_visual
        if railroad:
            existing["railroad"] = railroad
        if location:
            existing["location"] = location
        existing["corrections"] = min(
            50,
            int(existing.get("corrections", 1)) + 1
        )
        existing["success_streak"] = 0
        existing.setdefault("auto_successes", 0)
        existing["updated_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        LEARNED["profiles"].append({
            "category": category,
            "family": family,
            "parent_family": "",
            "variant_name": "",
            "auto_successes": 0,
            "success_streak": 0,
            "last_seen": "",
            "last_confidence": 0,
            "fingerprint": regular_fp,
            "fingerprint_examples": [regular_fp],
            "printed_structure": struct_fp,
            "structure_examples": [struct_fp] if struct_fp else [],
            "visual_signature": visual_sig,
            "visual_examples": [visual_sig] if visual_sig else [],
            "railroad": railroad,
            "location": location,
            "corrections": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        record_stat("new_families", 1)

    save_learning()

    # v41: once the document family is known, remember the reliable printed
    # date label for that exact family when one is present.
    learn_family_date_label(category, family, text)

    return text


# ============================================================================
# Optional date metadata — never used to decide destination folders
# ============================================================================

def safe_date(year, month, day):
    try:
        year = int(year)
        month = int(month)
        day = int(day)

        if year < 100:
            year += 2000 if year < 70 else 1900

        min_year = int(CFG.get("minimum_document_year", 2020))
        max_year = datetime.now().year + int(CFG.get("maximum_future_years", 1))

        if not (min_year <= year <= max_year):
            return None

        return datetime(year, month, day)
    except Exception:
        return None


def _repair_ocr_date_text(raw):
    """Repair common OCR mistakes only inside date-looking numeric tokens.

    Examples recovered safely inside date fields:
      O2/O8/2O26 -> 02/08/2026
      02-0B-2026 -> 02-08-2026
      02/I8/2026 -> 02/18/2026

    The repair is deliberately limited to tokens made mostly of digits,
    separators, and common digit-lookalike characters so normal words are
    left untouched.
    """
    text = str(raw or "")

    # Normalize common Unicode punctuation produced by PDFs/OCR.
    text = (
        text.replace("\\", "/")
        .replace("／", "/")
        .replace("⁄", "/")
        .replace("∕", "/")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .replace("‐", "-")
        .replace("‑", "-")
    )

    confusion_map = str.maketrans({
        "O": "0", "o": "0", "Q": "0", "q": "0",
        "I": "1", "i": "1", "l": "1", "|": "1", "!": "1",
        "S": "5", "s": "5",
        "B": "8", "b": "8",
        "Z": "2", "z": "2",
    })

    # Date-like tokens containing visible separators.
    token_re = re.compile(
        r"(?<![A-Za-z0-9])"
        r"[0-9OoQqIiLl|!SsBbZz]{1,4}"
        r"(?:\s*[/\.\-]\s*[0-9OoQqIiLl|!SsBbZz]{1,4}){2}"
        r"(?![A-Za-z0-9])"
    )

    text = token_re.sub(
        lambda m: m.group(0).translate(confusion_map),
        text
    )

    return text


def parse_date(raw):
    """Parse the date styles commonly found on statements, forms and logs."""
    text = _repair_ocr_date_text(raw).strip()

    if not text:
        return None

    # Trim harmless punctuation OCR may leave around a captured value.
    text = text.strip(" \t\r\n:;,[](){}")
    text = re.sub(r"\s*([/.\-])\s*", r"\1", text)
    text = re.sub(r"\s+", " ", text)

    # YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD / YYYY MM DD
    match = re.fullmatch(
        r"(20\d{2})(?:[/\.\-]|\s+)(\d{1,2})(?:[/\.\-]|\s+)(\d{1,2})",
        text
    )
    if match:
        return safe_date(
            match.group(1),
            match.group(2),
            match.group(3)
        )

    # MM/DD/YYYY, MM-DD-YY, MM.DD.YYYY, MM DD YYYY.
    # If the first number is > 12 and the second is a valid month, treat it
    # as day/month/year. Otherwise SmartScan follows the U.S. month/day order.
    match = re.fullmatch(
        r"(\d{1,2})(?:[/\.\-]|\s+)(\d{1,2})(?:[/\.\-]|\s+)(\d{2,4})",
        text
    )
    if match:
        a, b, year = match.groups()

        if int(a) > 12 and int(b) <= 12:
            return safe_date(year, b, a)

        return safe_date(year, a, b)

    month_words = "|".join(
        sorted(MONTHS, key=len, reverse=True)
    )

    # February 8, 2026 / Feb-8-2026 / Feb. 8 2026
    match = re.fullmatch(
        rf"({month_words})\.?[\s/\.-]+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?[\s/\.-]+)(\d{{2,4}})",
        text,
        re.I
    )
    if match:
        return safe_date(
            match.group(3),
            MONTHS[match.group(1).lower()],
            match.group(2)
        )

    # 8 February 2026 / 08-Feb-26
    match = re.fullmatch(
        rf"(\d{{1,2}})(?:st|nd|rd|th)?[\s/\.-]+({month_words})\.?(?:,?[\s/\.-]+)(\d{{2,4}})",
        text,
        re.I
    )
    if match:
        return safe_date(
            match.group(3),
            MONTHS[match.group(2).lower()],
            match.group(1)
        )

    # 2026 February 8 / 2026-Feb-08
    match = re.fullmatch(
        rf"(20\d{{2}})[\s/\.-]+({month_words})\.?[\s/\.-]+(\d{{1,2}})(?:st|nd|rd|th)?",
        text,
        re.I
    )
    if match:
        return safe_date(
            match.group(1),
            MONTHS[match.group(2).lower()],
            match.group(3)
        )

    # Compact values are accepted when the whole supplied value is clearly a
    # date. This is also useful for manual confirmation fields.
    compact = re.sub(r"[^0-9]", "", text)

    if len(compact) == 8:
        # YYYYMMDD
        if compact.startswith("20"):
            dt = safe_date(
                compact[0:4],
                compact[4:6],
                compact[6:8]
            )
            if dt:
                return dt

        # MMDDYYYY
        dt = safe_date(
            compact[4:8],
            compact[0:2],
            compact[2:4]
        )
        if dt:
            return dt

    if len(compact) == 6:
        # MMDDYY
        dt = safe_date(
            compact[4:6],
            compact[0:2],
            compact[2:4]
        )
        if dt:
            return dt

    return None

def _family_profile(category, family):
    family_low = clean_name(family).lower()

    for profile in LEARNED.get("profiles", []):
        if profile.get("category") != category:
            continue

        direct = clean_name(profile.get("family", "")).lower()
        parent = clean_optional_name(profile.get("parent_family", "")).lower()
        if family_low in {direct, parent}:
            return profile

    return None




def _date_profile_key(category, family):
    return clean_name(category).casefold() + "||" + clean_name(family).casefold()


def _date_profile(category, family, create=False):
    category = clean_name(category)
    family = clean_name(family)

    if not category or not family:
        return None

    key = _date_profile_key(category, family)
    profile = DATE_PROFILES["families"].get(key)

    if profile is None and create:
        profile = {
            "category": category,
            "family": family,
            "date_regions": [],
            "date_required": False,
            "preferred_date_label": "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        DATE_PROFILES["families"][key] = profile
        save_date_profiles()

    return profile


def _date_profile_regions(category, family):
    dp = _date_profile(category, family, create=False)
    if not dp:
        return []

    regions = []
    for index, raw in enumerate(dp.get("date_regions", []), 1):
        cleaned = _clean_date_region(
            raw,
            default_name=f"Date Area {index}",
            default_priority=index
        )
        if cleaned:
            regions.append(cleaned)

    regions.sort(
        key=lambda region: (
            int(region.get("priority", 999)),
            int(region.get("page", 0)),
            region.get("name", "")
        )
    )
    return regions


def _clean_date_region(region, default_name="", default_priority=1):
    if not region:
        return None

    cleaned = {
        "page": max(0, int(region.get("page", 0))),
        "x0": max(0.0, min(1.0, float(region.get("x0", 0.0)))),
        "y0": max(0.0, min(1.0, float(region.get("y0", 0.0)))),
        "x1": max(0.0, min(1.0, float(region.get("x1", 1.0)))),
        "y1": max(0.0, min(1.0, float(region.get("y1", 1.0)))),
        "name": clean_name(region.get("name", default_name) or default_name or "Date Area"),
        "priority": max(1, int(region.get("priority", default_priority))),
    }

    if (
        cleaned["x1"] <= cleaned["x0"]
        or cleaned["y1"] <= cleaned["y0"]
    ):
        return None

    if region.get("sample"):
        cleaned["sample"] = str(region.get("sample"))

    if region.get("created_at"):
        cleaned["created_at"] = str(region.get("created_at"))

    if region.get("updated_at"):
        cleaned["updated_at"] = str(region.get("updated_at"))

    return cleaned


def _region_iou(a, b):
    """Intersection-over-union for two normalized rectangles on the same page."""
    if int(a.get("page", 0)) != int(b.get("page", 0)):
        return 0.0

    left = max(float(a["x0"]), float(b["x0"]))
    top = max(float(a["y0"]), float(b["y0"]))
    right = min(float(a["x1"]), float(b["x1"]))
    bottom = min(float(a["y1"]), float(b["y1"]))

    if right <= left or bottom <= top:
        return 0.0

    intersection = (right - left) * (bottom - top)

    area_a = (
        (float(a["x1"]) - float(a["x0"]))
        * (float(a["y1"]) - float(a["y0"]))
    )
    area_b = (
        (float(b["x1"]) - float(b["x0"]))
        * (float(b["y1"]) - float(b["y0"]))
    )

    union = area_a + area_b - intersection

    if union <= 0:
        return 0.0

    return intersection / union


def get_family_date_regions(profile):
    """Return only v51 isolated date regions for this exact learned family."""
    if not profile:
        return []

    return _date_profile_regions(
        profile.get("category", ""),
        clean_optional_name(profile.get("parent_family", "")) or profile.get("family", "")
    )


def save_family_date_region(category, family, region, sample_date=None):
    """Save a date area only under the exact Category → Document Type key."""
    dp = _date_profile(category, family, create=True)
    if not dp:
        return False

    existing = _date_profile_regions(category, family)
    default_priority = max(
        [int(r.get("priority", 0)) for r in existing],
        default=0
    ) + 1

    cleaned = _clean_date_region(
        region,
        default_name=f"Date Area {len(existing) + 1}",
        default_priority=default_priority
    )
    if not cleaned:
        return False

    now = datetime.now().isoformat(timespec="seconds")
    cleaned["category"] = clean_name(category)
    cleaned["family"] = clean_name(family)
    cleaned["updated_at"] = now
    cleaned.setdefault("created_at", now)

    if sample_date:
        cleaned["sample"] = sample_date.strftime("%Y-%m-%d")

    replaced = False
    for index, current in enumerate(existing):
        if _region_iou(cleaned, current) >= 0.72:
            cleaned["created_at"] = current.get("created_at", cleaned["created_at"])
            existing[index] = cleaned
            replaced = True
            break

    if not replaced:
        existing.append(cleaned)

    existing.sort(
        key=lambda region: (
            int(region.get("priority", 999)),
            int(region.get("page", 0)),
            region.get("name", "")
        )
    )

    dp["date_regions"] = existing
    dp["date_required"] = True
    dp["updated_at"] = now
    save_date_profiles()
    return True


def clear_family_date_region(category, family):
    """Clear only this exact family's isolated date areas."""
    dp = _date_profile(category, family, create=False)
    if not dp:
        return False

    dp["date_regions"] = []
    dp["date_required"] = False
    dp["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_date_profiles()
    return True


def delete_family_date_region(category, family, target_region):
    dp = _date_profile(category, family, create=False)
    if not dp or not target_region:
        return False

    regions = _date_profile_regions(category, family)
    best_index = None
    best_score = 0.0

    for index, region in enumerate(regions):
        if int(region.get("page", 0)) != int(target_region.get("page", 0)):
            continue

        score = _region_iou(region, target_region)

        if region.get("name") == target_region.get("name"):
            score += 0.08

        if int(region.get("priority", 999)) == int(target_region.get("priority", 999)):
            score += 0.04

        if score > best_score:
            best_score = score
            best_index = index

    if best_index is None or best_score < 0.55:
        return False

    removed = regions.pop(best_index)
    dp["date_regions"] = regions
    dp["date_required"] = bool(regions)
    dp["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_date_profiles()
    return removed


def family_requires_date(category, family):
    dp = _date_profile(category, family, create=False)
    if not dp:
        return False
    return bool(
        dp.get("date_required")
        and _date_profile_regions(category, family)
    )


def _blue_ink_mask(rgb):
    """Fast isolation of likely blue/cyan handwriting from black form lines."""
    try:
        rgb = rgb.convert("RGB")
        red, green, blue = rgb.split()
        blue_minus_red = ImageChops.subtract(blue, red)
        return blue_minus_red.point(lambda value: 0 if value >= 10 else 255)
    except Exception:
        return None


def _remove_horizontal_lines(gray):
    """Suppress long horizontal form lines while retaining most handwriting."""
    try:
        img = gray.copy()
        px = img.load()
        width, height = img.size
        if width < 40 or height < 10:
            return img

        min_run = max(30, int(width * 0.22))
        for y in range(height):
            start = None
            for x in range(width + 1):
                dark = (x < width and px[x, y] < 90)
                if dark and start is None:
                    start = x
                elif not dark and start is not None:
                    if x - start >= min_run:
                        for xx in range(start, x):
                            px[xx, y] = 255
                    start = None
        return img
    except Exception:
        return gray


def _compact_region_date_candidates(fragment):
    """Extra date parsing allowed ONLY inside a user-taught date box.

    Because the user explicitly identified this crop as a date field, we can
    safely recover separators that OCR sometimes drops.
    """
    raw = str(fragment or "")
    compact = re.sub(r"[^0-9]", "", raw)

    output = []

    def add_candidate(month, day, year):
        dt = safe_date(year, month, day)
        if dt and dt not in output:
            output.append(dt)

    # MMDDYYYY
    if len(compact) == 8:
        add_candidate(
            compact[0:2],
            compact[2:4],
            compact[4:8]
        )

    # MMDDYY
    if len(compact) == 6:
        add_candidate(
            compact[0:2],
            compact[2:4],
            compact[4:6]
        )

    # MDDYYYY, e.g. 6172026 -> 6/17/2026
    if len(compact) == 7:
        add_candidate(
            compact[0:1],
            compact[1:3],
            compact[3:7]
        )

        # MMDYYYY, e.g. 0612026 -> 06/1/2026
        add_candidate(
            compact[0:2],
            compact[2:3],
            compact[3:7]
        )

    return output



def _candidate_date_subcrops(crop_rgb, blue_mask=None):
    """Create safe tighter crops inside a user-taught date search zone.

    The user's box is treated as a search zone rather than an exact OCR crop.
    Candidates stay entirely inside that zone so SmartScan cannot wander into
    unrelated areas of the page.
    """
    width, height = crop_rgb.size

    if width < 40 or height < 20:
        return [("full-zone", crop_rgb)]

    candidates = []
    seen_boxes = set()

    def add(name, x0, y0, x1, y1):
        x0 = max(0, min(width - 1, int(x0)))
        y0 = max(0, min(height - 1, int(y0)))
        x1 = max(x0 + 1, min(width, int(x1)))
        y1 = max(y0 + 1, min(height, int(y1)))

        if x1 - x0 < 18 or y1 - y0 < 10:
            return

        key = (x0, y0, x1, y1)

        if key in seen_boxes:
            return

        seen_boxes.add(key)
        candidates.append(
            (
                name,
                crop_rgb.crop(
                    (x0, y0, x1, y1)
                )
            )
        )

    # 1) Smartest option first: find likely blue handwriting and tighten
    # around its actual bounds with a small margin.
    if CFG.get("handwritten_date_mode", True):
        try:
            if blue_mask is None:
                blue_mask = _blue_ink_mask(crop_rgb)

            if blue_mask is not None:
                # _blue_ink_mask returns dark ink on white. Invert so PIL bbox
                # finds the non-white/ink extent.
                inverted = ImageOps.invert(
                    blue_mask.convert("L")
                )

                bbox = inverted.getbbox()

                if bbox:
                    bx0, by0, bx1, by1 = bbox

                    # Ignore a bbox that is effectively the entire zone; that
                    # usually means the mask was not selective enough.
                    bbox_area = max(
                        1,
                        (bx1 - bx0) * (by1 - by0)
                    )
                    zone_area = max(
                        1,
                        width * height
                    )

                    if bbox_area / zone_area < 0.88:
                        margin_x = max(
                            8,
                            int((bx1 - bx0) * 0.18)
                        )
                        margin_y = max(
                            5,
                            int((by1 - by0) * 0.30)
                        )

                        add(
                            "auto-ink-tight",
                            bx0 - margin_x,
                            by0 - margin_y,
                            bx1 + margin_x,
                            by1 + margin_y
                        )

        except Exception:
            pass

    # 2) User's full zone.
    add(
        "full-zone",
        0,
        0,
        width,
        height
    )

    if not CFG.get("date_subcrop_search", True):
        return candidates

    # 3) Common forgiving trims. These help when the box contains a printed
    # label, border, underline, or a piece of the next field.
    add(
        "trim-left-12",
        width * 0.12,
        0,
        width,
        height
    )
    add(
        "trim-right-12",
        0,
        0,
        width * 0.88,
        height
    )
    add(
        "trim-top-12",
        0,
        height * 0.12,
        width,
        height
    )
    add(
        "trim-bottom-12",
        0,
        0,
        width,
        height * 0.88
    )

    # 4) Horizontal windows are especially useful when "Start Date" is on the
    # left and the handwritten value is on the right.
    add(
        "right-82",
        width * 0.18,
        0,
        width,
        height
    )
    add(
        "left-82",
        0,
        0,
        width * 0.82,
        height
    )
    add(
        "center-84",
        width * 0.08,
        height * 0.08,
        width * 0.92,
        height * 0.92
    )

    return candidates


def _prepare_date_crop_variants(crop_rgb):
    """Prepare a small set of handwriting-friendly OCR images."""
    gray = ImageOps.autocontrast(
        ImageOps.grayscale(crop_rgb)
    )

    variants = []

    if CFG.get("handwritten_date_mode", True):
        blue = _blue_ink_mask(crop_rgb)

        if blue is not None:
            variants.append(
                ("blue-ink", ImageOps.autocontrast(blue))
            )

    variants.append(
        (
            "line-clean",
            _remove_horizontal_lines(gray)
        )
    )
    variants.append(
        ("gray", gray)
    )

    try:
        variants.append(
            (
                "threshold",
                gray.point(
                    lambda value: 255 if value > 170 else 0
                )
            )
        )
    except Exception:
        pass

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    prepared = []

    for name, variant in variants:
        # Tighter automatic crops need enough pixel width for handwriting OCR.
        if variant.width < 950:
            scale = min(
                3.4,
                max(
                    1.45,
                    950 / max(1, variant.width)
                )
            )

            variant = variant.resize(
                (
                    max(1, int(variant.width * scale)),
                    max(1, int(variant.height * scale)),
                ),
                resample
            )

        prepared.append(
            (name, variant)
        )

    return prepared


def _ocr_one_date_candidate(
    candidate_name,
    candidate_rgb,
    page_index,
    progress=None,
    progress_prefix=""
):
    """OCR one internal crop and return its best date evidence.

    Returns a dict or None. A candidate can succeed strongly by producing
    two agreeing OCR passes. A single valid hit is returned as weak evidence
    so another sub-crop can confirm it.
    """
    prepared = _prepare_date_crop_variants(
        candidate_rgb
    )

    votes = {}
    evidence = []
    fragments = []

    def record(fragment, angle, variant_name, config_name):
        fragment = str(fragment or "").strip()

        if fragment:
            fragments.append(
                f"[{candidate_name} | {variant_name} {angle}° {config_name}] {fragment}"
            )

        dates = [
            dt
            for dt, raw in _date_candidates(fragment)
        ]

        dates.extend(
            _compact_region_date_candidates(
                fragment
            )
        )

        # v63: if OCR happened to preserve a field label, give that date extra
        # weight. This helps boxes containing labels and neighboring values.
        labeled_hit = _date_near_zone_label(fragment)
        labeled_key = None

        if labeled_hit:
            labeled_dt = labeled_hit[0]
            labeled_key = labeled_dt.strftime("%Y-%m-%d")
            dates.insert(0, labeled_dt)

        seen = set()

        for dt in dates:
            key = dt.strftime(
                "%Y-%m-%d"
            )

            if key in seen:
                continue

            seen.add(key)
            votes[key] = votes.get(key, 0) + (2 if key == labeled_key else 1)

            evidence.append(
                (
                    key,
                    angle,
                    variant_name,
                    config_name
                )
            )

    def current_winner():
        if not votes:
            return None

        ranked = sorted(
            votes.items(),
            key=lambda item: item[1],
            reverse=True
        )

        best_key, best_votes = ranked[0]
        second_votes = (
            ranked[1][1]
            if len(ranked) > 1
            else 0
        )

        if best_votes >= 2 and best_votes > second_votes:
            return best_key, best_votes

        return None

    rotations = (
        0,
        90,
        270,
        180,
    )

    # Digits-first is the fastest/cleanest path for a known date field.
    fast_configs = [
        (
            "digits",
            "--psm 7 -c tessedit_char_whitelist=0123456789/-."
        ),
        (
            "line",
            "--psm 7"
        ),
    ]

    pass_no = 0
    winner = None

    # Use handwriting-oriented variants first. Most good boxes will finish
    # before the full fallback set is needed.
    for variant_name, base in prepared:
        for angle in rotations:
            rotated = (
                base.rotate(
                    angle,
                    expand=True
                )
                if angle
                else base
            )

            for config_name, config in fast_configs:
                pass_no += 1

                if progress:
                    progress(
                        f"{progress_prefix} • {candidate_name} • pass {pass_no}"
                    )

                try:
                    fragment = pytesseract.image_to_string(
                        rotated,
                        lang=CFG.get(
                            "ocr_language",
                            "eng"
                        ),
                        config=config
                    )

                    record(
                        fragment,
                        angle,
                        variant_name,
                        config_name
                    )

                except Exception:
                    pass

                winner = current_winner()

                if winner:
                    break

            if winner:
                break

        if winner:
            break

    if not votes:
        return None

    ranked = sorted(
        votes.items(),
        key=lambda item: item[1],
        reverse=True
    )

    best_key, best_votes = ranked[0]
    second_votes = (
        ranked[1][1]
        if len(ranked) > 1
        else 0
    )

    if (
        len(ranked) > 1
        and best_votes <= second_votes
    ):
        return None

    matching = [
        item
        for item in evidence
        if item[0] == best_key
    ]

    return {
        "date_key": best_key,
        "votes": best_votes,
        "strong": bool(
            best_votes >= 2
            and best_votes > second_votes
        ),
        "angles": sorted({
            item[1]
            for item in matching
        }),
        "modes": sorted({
            item[2]
            for item in matching
        }),
        "fragments": fragments[:8],
        "candidate": candidate_name,
        "page": page_index,
    }


def _date_region_recovery_variants(region):
    """Create conservative nearby searches when a learned date box drifts."""
    if not region:
        return []

    base = _clean_date_region(region)
    if not base:
        return []

    x0, y0, x1, y1 = [float(base[k]) for k in ("x0", "y0", "x1", "y1")]
    width = max(0.01, x1 - x0)
    height = max(0.01, y1 - y0)
    variants = []

    def add(name, dx=0.0, dy=0.0, expand=0.0):
        candidate = dict(base)
        candidate["name"] = name
        candidate["x0"] = max(0.0, x0 + dx - expand)
        candidate["y0"] = max(0.0, y0 + dy - expand)
        candidate["x1"] = min(1.0, x1 + dx + expand)
        candidate["y1"] = min(1.0, y1 + dy + expand)
        cleaned = _clean_date_region(candidate)
        if cleaned:
            variants.append(cleaned)

    # Scanner alignment shifts are usually small. Try expansion first, then
    # a few directional shifts; all attempts remain close to the taught zone.
    add("Recovered expanded date area", expand=max(0.012, min(0.035, max(width, height) * 0.18)))
    xshift = max(0.010, min(0.025, width * 0.18))
    yshift = max(0.010, min(0.025, height * 0.18))
    add("Recovered date area left", dx=-xshift, expand=0.008)
    add("Recovered date area right", dx=xshift, expand=0.008)
    add("Recovered date area up", dy=-yshift, expand=0.008)
    add("Recovered date area down", dy=yshift, expand=0.008)
    return variants


def _recover_date_region(path, region, progress=None):
    if not CFG.get("date_area_recovery", True):
        return None

    variants = _date_region_recovery_variants(region)
    for index, candidate in enumerate(variants, 1):
        if progress:
            progress(f"Date area drift recovery {index}/{len(variants)}...")
        result = _ocr_date_region(
            path,
            candidate,
            progress=progress,
            _allow_recovery=False
        )
        if result:
            dt, conf, text, details = result
            return dt, min(99, max(conf, 95)), text, f"auto-recovered shifted area; {details}"
    return None


DATE_FAST_OCR_LIMIT = 3
DATE_FULL_OCR_LIMIT = 24


def _read_taught_date(path, region, progress=None, fast=False,
                     page_image=None, page_cache=None, allow_recovery=True):
    """Bounded, lazy date OCR; retain date parsing, year limits and consensus.

    Each read owns its images/evidence, so simultaneous workers cannot mix dates.
    A normal exact crop needs two agreeing calls (or one labeled date). No
    rotation, blue-ink extraction or drift search runs before upright OCR fails.
    """
    started = time.perf_counter()
    calls = 0
    deadline = time.monotonic() + 20.0
    success_method = "unread"
    limit = DATE_FAST_OCR_LIMIT if fast else DATE_FULL_OCR_LIMIT
    votes, fragments, prepared = {}, [], {}
    threshold = int(CFG.get("date_confidence_threshold", 92))
    page_index = max(0, int((region or {}).get("page", 0)))

    def report(message):
        if progress:
            progress(message)

    try:
        report("Fast date check — Trying taught date area")
        if not path or not region or not Path(path).exists():
            return None
        if page_image is None:
            cache = page_cache if page_cache is not None else {}
            key = (str(Path(path).resolve()), page_index)
            if key not in cache:
                cache[key] = _load_page_image(path, page_index, scale=2.25)
            page_image = cache[key]
        if page_image is None:
            return None

        width, height = page_image.size
        crops = {}

        def crop_for(area, padding=0.0):
            box = (
                max(0, min(width, int((float(area.get("x0", 0)) - padding) * width))),
                max(0, min(height, int((float(area.get("y0", 0)) - padding) * height))),
                max(0, min(width, int((float(area.get("x1", 1)) + padding) * width))),
                max(0, min(height, int((float(area.get("y1", 1)) + padding) * height))),
            )
            if box[2] - box[0] < 18 or box[3] - box[1] < 10:
                return None
            if box not in crops:
                crops[box] = page_image.crop(box).convert("RGB")
            return crops[box]

        exact = crop_for(region)
        if exact is None:
            return None

        def image_variant(crop, mode, angle=0):
            key = (id(crop), mode, angle)
            if key not in prepared:
                if angle:
                    image = image_variant(crop, mode).rotate(angle, expand=True)
                elif mode == "raw":
                    image = crop
                elif mode == "gray":
                    image = ImageOps.grayscale(crop)
                elif mode == "contrast":
                    image = ImageOps.autocontrast(image_variant(crop, "gray"))
                elif mode == "threshold":
                    image = image_variant(crop, "contrast").point(lambda v: 255 if v > 170 else 0)
                elif mode == "line-clean":
                    image = _remove_horizontal_lines(image_variant(crop, "contrast"))
                elif mode == "blue-ink":
                    image = image_variant(crop, "blue-mask")
                    if image is not None:
                        image = ImageOps.autocontrast(image)
                elif mode == "blue-mask":
                    image = _blue_ink_mask(crop)
                else:
                    raise ValueError(mode)
                # Enlargement is reserved for the handwriting/cleanup stage.
                if image is not None and not angle and mode in {"blue-ink", "line-clean", "threshold"} and image.width < 950:
                    scale = min(3.4, 950 / max(1, image.width))
                    resample = getattr(Image, "Resampling", Image).LANCZOS
                    image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), resample)
                prepared[key] = image
            return prepared[key]

        digits = "--psm 7 -c tessedit_char_whitelist=0123456789/-."

        def attempts():
            yield "exact", exact, "raw", 0, digits, "Fast date check"
            yield "exact", exact, "contrast", 0, digits, "Trying taught date area"
            yield "exact", exact, "contrast", 0, "--psm 7", "Trying taught date area"
            if fast:
                return
            # All upright crop/block attempts precede any rotated attempt.
            padding = max(0.0, float(CFG.get("date_region_padding", 0.018)))
            padded = crop_for(region, padding)
            if padded is not None and padded is not exact:
                yield "padded", padded, "contrast", 0, digits, "Trying taught date area"
            if CFG.get("date_subcrop_search", True):
                w, h = exact.size
                for name, box in (("trim-left", (int(w * .12), 0, w, h)),
                                  ("trim-top", (0, int(h * .12), w, h)),
                                  ("trim-right", (0, 0, int(w * .88), h)),
                                  ("trim-bottom", (0, 0, w, int(h * .88)))):
                    trimmed = exact.crop(box)
                    # Keep references alive for the per-read identity cache.
                    crops[name] = trimmed
                    yield name, trimmed, "contrast", 0, digits, "Trying taught date area"
            yield "exact", exact, "contrast", 0, "--psm 6", "Trying taught date area — multi-line"
            yield "exact", exact, "contrast", 0, "--psm 11", "Trying taught date area — multi-line"
            for angle in (90, 270, 180):
                yield "exact", exact, "contrast", angle, digits, "Rotation fallback"
            # Handwriting preparation never runs on the three-call fast path.
            handwriting_method = None
            for index, (method, image) in enumerate(_handwriting_images(exact)):
                if calls >= limit or time.monotonic() >= deadline:
                    return
                prepared[(id(exact), method, 0)] = image
                if handwriting_method is None or method == "blue-ink-line-repair":
                    handwriting_method = method
                config = ("--psm 7" if index % 2 == 0 else "--psm 13")
                config += " -c tessedit_char_whitelist=0123456789/-."
                yield "exact", exact, method, 0, config, "Handwriting fallback"
            if handwriting_method:
                for angle in (90, 270, 180):
                    yield "exact", exact, handwriting_method, angle, digits, "Handwriting fallback"
            if allow_recovery and CFG.get("date_area_recovery", True):
                for index, area in enumerate(_date_region_recovery_variants(region), 1):
                    shifted = crop_for(area)
                    if shifted is not None:
                        yield f"drift-{index}", shifted, "contrast", 0, digits, "Handwriting fallback — date area drift recovery"

        def read(crop, mode, angle, config, stage, name):
            nonlocal calls
            if calls >= limit or (not fast and time.monotonic() >= deadline):
                return None, None
            image = image_variant(crop, mode, angle)
            if image is None:
                return None, None
            calls += 1
            report(f"{stage} • {name} • {angle}° • OCR {calls}/{limit}")
            try:
                fragment = pytesseract.image_to_string(
                    image, lang=CFG.get("ocr_language", "eng"), config=config, timeout=5.0 if fast else max(.05, min(5.0, deadline-time.monotonic()))
                ).strip()
            except Exception:
                return None, None
            if fragment:
                fragments.append(f"[{name} {mode} {angle}°] {fragment}")
            labeled = _date_near_zone_label(fragment)
            dates = {dt.strftime("%Y-%m-%d"): dt for dt, _ in _date_candidates(fragment)}
            if not dates:
                dates = {dt.strftime("%Y-%m-%d"): dt for dt in _compact_region_date_candidates(fragment)}
            if labeled:
                dt = labeled[0]
                if 99 >= threshold:
                    return (dt, 99), None
            # Do not vote for ambiguous fragments or manufacture missing dates.
            if len(dates) != 1:
                return None, None
            key, dt = next(iter(dates.items()))
            votes[key] = votes.get(key, 0) + 1
            others = max((count for other, count in votes.items() if other != key), default=0)
            if votes[key] >= 2 and votes[key] > others and 98 >= threshold:
                return (dt, 98), key
            return None, key

        for name, crop, mode, angle, config, stage in attempts():
            if calls >= limit or (not fast and time.monotonic() >= deadline):
                break
            accepted, key = read(crop, mode, angle, config, stage, name)
            # Confirmation only on evidence; never run all modes on every image.
            if not accepted and key and calls >= DATE_FAST_OCR_LIMIT and calls < limit:
                confirm_config = "--psm 13 -c tessedit_char_whitelist=0123456789/-." if stage == "Handwriting fallback" else ("--psm 7" if config == digits else digits)
                accepted, _ = read(crop, mode, angle, confirm_config, stage, name)
            if accepted:
                dt, confidence = accepted
                success_method = mode
                details = f"page {page_index + 1}, {name}, {mode}, {angle}°, {calls} OCR calls"
                report(f"Date found • {calls} OCR calls")
                return dt, confidence, "\n".join(fragments), details
        return None
    finally:
        log(f"Date reader {'fast' if fast else 'full'}: page {page_index + 1}, "
            f"{calls} OCR calls, {time.perf_counter() - started:.3f}s, limit {limit}, method {success_method}")


def _ocr_date_region(path, region, progress=None, _allow_recovery=True,
                     page_image=None, page_cache=None):
    return _read_taught_date(path, region, progress=progress,
                            page_image=page_image, page_cache=page_cache,
                            allow_recovery=_allow_recovery)


def _quick_ocr_date_region(path, region, page_cache=None):
    result = _read_taught_date(path, region, fast=True, page_cache=page_cache)
    if result:
        dt, confidence, text, details = result
        return dt, confidence, "quick " + details
    return None


def _date_candidates(fragment):
    """Extract supported date strings from a short OCR fragment.

    v63 supports numeric, ISO, month-name and space-separated dates, and
    repairs common OCR digit/lookalike errors inside date-looking tokens.
    """
    fragment = _repair_ocr_date_text(fragment)

    # Repair spaces OCR sometimes inserts around visible separators.
    fragment = re.sub(
        r"(?<=\d)\s*([/.\-])\s*(?=\d)",
        r"\1",
        fragment
    )

    numeric = re.findall(
        r"(?<!\d)(?:"
        r"20\d{2}(?:[/\.\-]|\s+)\d{1,2}(?:[/\.\-]|\s+)\d{1,2}"
        r"|"
        r"\d{1,2}(?:[/\.\-]|\s+)\d{1,2}(?:[/\.\-]|\s+)\d{2,4}"
        r")(?!\d)",
        fragment
    )

    month_words = "|".join(
        sorted(MONTHS, key=len, reverse=True)
    )

    month_first = re.findall(
        rf"\b(?:{month_words})\.?[\s/\.-]+\d{{1,2}}(?:st|nd|rd|th)?(?:,?[\s/\.-]+)\d{{2,4}}\b",
        fragment,
        flags=re.I
    )

    day_first = re.findall(
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?[\s/\.-]+(?:{month_words})\.?(?:,?[\s/\.-]+)\d{{2,4}}\b",
        fragment,
        flags=re.I
    )

    year_first_word = re.findall(
        rf"\b20\d{{2}}[\s/\.-]+(?:{month_words})\.?[\s/\.-]+\d{{1,2}}(?:st|nd|rd|th)?\b",
        fragment,
        flags=re.I
    )

    output = []
    seen = set()

    for raw in numeric + month_first + day_first + year_first_word:
        dt = parse_date(raw)

        if not dt:
            continue

        key = dt.strftime("%Y-%m-%d")

        if key in seen:
            continue

        seen.add(key)
        output.append((dt, raw))

    return output


# Labels commonly found beside a date on bills, statements, payroll forms,
# logs, expense reports and general business documents. More specific labels
# are checked before the generic word "date".
DATE_ZONE_LABELS = (
    "payment due date",
    "payment due",
    "due date",
    "period ending",
    "period end",
    "pay period ending",
    "statement date",
    "invoice date",
    "billing date",
    "bill date",
    "document date",
    "report date",
    "expense date",
    "service date",
    "transaction date",
    "posting date",
    "posted date",
    "check date",
    "pay date",
    "issue date",
    "issued date",
    "effective date",
    "start date",
    "end date",
    "date worked",
    "work date",
    "date",
)


def _date_near_zone_label(fragment):
    """Return a labeled date hit from a taught date zone, if one is clear."""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(fragment or "").splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    normalized_lines = [
        normalize_text(line)
        for line in lines
    ]

    for label in DATE_ZONE_LABELS:
        label_norm = normalize_text(label)

        for index, line_norm in enumerate(normalized_lines):
            if label_norm not in line_norm:
                continue

            same_line = _date_candidates(lines[index])

            if len(same_line) == 1:
                dt, raw = same_line[0]
                return dt, raw, label, "same line"

            # Some OCR engines split a two-column form into a label line then
            # place the value on the following line. Check the next two lines.
            for offset in (1, 2):
                next_index = index + offset

                if next_index >= len(lines):
                    break

                nearby = _date_candidates(lines[next_index])

                if len(nearby) == 1:
                    dt, raw = nearby[0]
                    return dt, raw, label, f"+{offset} line"

    return None


def _date_zone_text_prepass(crop_rgb, page_index, progress=None, fast=False):
    """Fallback OCR for a taught zone that contains labels + other fields.

    The old reader was optimized for a tight, one-line date crop. v63 adds a
    multi-line fallback using PSM 6/11 so a box such as:

        Due Date:     02/08/2026
        Account #:    123456789
        Amount Due:   $749.41

    can still produce the intended date. A labeled date wins immediately;
    otherwise two agreeing OCR passes are required when possible.
    """
    if crop_rgb is None:
        return None

    gray = ImageOps.autocontrast(
        ImageOps.grayscale(crop_rgb)
    )

    variants = [("block-gray", gray)]

    if not fast:
        try:
            variants.append(
                (
                    "block-threshold",
                    gray.point(
                        lambda value: 255 if value > 178 else 0
                    )
                )
            )
        except Exception:
            pass

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    prepared = []

    for name, variant in variants:
        # Multi-field statement boxes benefit from a little enlargement but
        # keep this bounded so the fallback remains quick.
        if variant.width < 1050:
            scale = min(
                2.4,
                max(
                    1.25,
                    1050 / max(1, variant.width)
                )
            )

            variant = variant.resize(
                (
                    max(1, int(variant.width * scale)),
                    max(1, int(variant.height * scale)),
                ),
                resample
            )

        prepared.append((name, variant))

    configs = [
        ("block", "--psm 6"),
        ("sparse", "--psm 11"),
    ]

    if fast:
        # In normal sorting this is only a fallback after the fast one-line
        # reader failed, so keep it to a maximum of two OCR calls.
        prepared = prepared[:1]

    votes = {}
    evidence = {}
    fragments = []

    for variant_name, variant in prepared:
        for config_name, config in configs:
            if progress:
                progress(
                    f"Trying multi-line date reader: {variant_name}/{config_name}"
                )

            try:
                fragment = pytesseract.image_to_string(
                    variant,
                    lang=CFG.get("ocr_language", "eng"),
                    config=config
                )
            except Exception:
                continue

            fragment = str(fragment or "").strip()

            if fragment:
                fragments.append(
                    f"[{variant_name} {config_name}] {fragment}"
                )

            labeled = _date_near_zone_label(fragment)

            if labeled:
                dt, raw, label, relation = labeled
                details = (
                    f"page {page_index + 1}, multi-line label '{label}' "
                    f"({relation}), {variant_name}/{config_name}"
                )

                return (
                    dt,
                    99,
                    "\n".join(fragments[:8]),
                    details
                )

            dates = _date_candidates(fragment)

            # A taught date zone can safely use a single unique date from a
            # multi-line pass. Require agreement for the strongest confidence.
            if len(dates) == 1:
                dt, raw = dates[0]
                key = dt.strftime("%Y-%m-%d")
                votes[key] = votes.get(key, 0) + 1
                evidence[key] = (
                    variant_name,
                    config_name,
                    raw,
                )

                if votes[key] >= 2:
                    details = (
                        f"page {page_index + 1}, multi-line agreement, "
                        f"{variant_name}/{config_name}"
                    )

                    return (
                        dt,
                        98,
                        "\n".join(fragments[:8]),
                        details
                    )

    if not votes:
        return None

    ranked = sorted(
        votes.items(),
        key=lambda item: item[1],
        reverse=True
    )

    best_key, best_votes = ranked[0]
    second_votes = ranked[1][1] if len(ranked) > 1 else 0

    if len(ranked) > 1 and best_votes <= second_votes:
        return None

    dt = datetime.strptime(best_key, "%Y-%m-%d")
    variant_name, config_name, raw = evidence[best_key]

    return (
        dt,
        95 if best_votes == 1 else 97,
        "\n".join(fragments[:8]),
        (
            f"page {page_index + 1}, unique multi-line date, "
            f"{variant_name}/{config_name}"
        )
    )

def _find_date_near_label(text, label, base_confidence):
    """Find a date on the label's line or immediately following it."""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").splitlines()
        if line.strip()
    ]

    label_low = normalize_text(label)

    for index, line in enumerate(lines):
        if label_low not in normalize_text(line):
            continue

        # Same-line date is strongest.
        same_line = _date_candidates(line)

        if len(same_line) == 1:
            dt, raw = same_line[0]
            return dt, int(base_confidence), raw, "same line"

        # Multiple dates on one labeled line are ambiguous; do not guess.
        if len(same_line) > 1:
            continue

        # OCR often places the value directly below the printed label.
        if index + 1 < len(lines):
            next_line = _date_candidates(lines[index + 1])

            if len(next_line) == 1:
                dt, raw = next_line[0]
                return dt, max(0, int(base_confidence) - 4), raw, "next line"

    return None


def _quick_date_from_fragment(fragment):
    dates = [
        dt
        for dt, raw in _date_candidates(
            fragment
        )
    ]

    dates.extend(
        _compact_region_date_candidates(
            fragment
        )
    )

    unique = []

    for dt in dates:
        if dt not in unique:
            unique.append(dt)

    return unique





def detect_family_date_fast(category, family, text, path=None):
    """Fast automatic document-date path used after recognition.

    v69 priority intentionally mirrors what the user sees on the form:
      1. Learned spatial date area for this exact Category -> Document Type.
      2. Learned/preferred printed date label in already-available OCR text.
      3. Safe family/generic printed date labels.
      4. Stop. Full handwriting OCR remains reserved for Teach/Review.

    The important v69 change is that a taught date box outranks another date
    elsewhere in the OCR text (for example a print date, pay-period date, or
    revision date).
    """
    if (
        path
        and CFG.get("use_learned_date_region", True)
    ):
        profile = _family_profile(category, family)
        if profile:
            regions = get_family_date_regions(profile)[:max(
                1,
                int(CFG.get("quick_date_regions", 2))
            )]
            page_cache = {}
            for region in regions:
                result = _quick_ocr_date_region(path, region, page_cache=page_cache)
                if result:
                    date_value, date_conf, details = result
                    return (
                        date_value,
                        date_conf,
                        region.get("name", "Date Area"),
                        (
                            f"quick learned date area "
                            f"Priority {region.get('priority', '?')} "
                            f"({details})"
                        )
                    )

    # Cheap text-only pass after the taught boxes.  path=None prevents the
    # slower region renderer from running a second time.
    dt, confidence, label, source = detect_family_date(
        category,
        family,
        text,
        path=None,
        include_learned=True
    )
    if dt:
        return dt, confidence, label, source

    return None, 0, "none", "quick date zones/labels exhausted"

def detect_reliable_filename_date(path):
    """Return a date only when the incoming filename itself looks intentional.

    Generic scanner/page names are deliberately rejected because their date is
    usually the scan date, not the date printed on the document.
    """
    if not path:
        return None

    stem = Path(path).stem
    low = normalize_text(stem).replace("_", " ").replace("-", " ")
    generic = (
        "scan", "scanner", "img", "image", "document", "page", "hp scan",
        "adobe scan", "camscanner", "copy"
    )
    if any(low == token or low.startswith(token + " ") for token in generic):
        return None
    if "__split" in stem.casefold() or re.search(r"(?:^|[_ -])page[_ -]?\d+", stem, re.I):
        return None

    candidates = []
    candidates.extend(re.findall(r"(?<!\d)(20\d{2}[-_. ]\d{1,2}[-_. ]\d{1,2})(?!\d)", stem))
    candidates.extend(re.findall(r"(?<!\d)(\d{1,2}[-_. ]\d{1,2}[-_. ](?:20)?\d{2})(?!\d)", stem))
    for raw in candidates:
        dt = parse_date(raw.replace("_", "-"))
        if dt:
            return dt
    return None


def scan_date_fallback(path):
    """Last-resort filesystem date for documents whose family does not require
    a printed date. This never satisfies a date-required learned form.
    """
    if not path:
        return None
    try:
        st = Path(path).stat()
        # On Windows st_ctime is the file creation time. On other systems use
        # mtime because ctime is metadata-change time.
        ts = st.st_ctime if os.name == "nt" else st.st_mtime
        dt = datetime.fromtimestamp(ts)
        return safe_date(dt.year, dt.month, dt.day)
    except Exception:
        return None


def detect_family_date(category, family, text, path=None, include_learned=True, progress=None):
    """Return (datetime, confidence, label, source).

    Priority:
      1. Every learned spatial date area, in user-defined priority order.
      2. Learned printed date label.
      3. Safe family-specific label.
      4. Generic high-confidence label.

    If Priority 1 fails on a particular document, Priority 2 is tried, and so
    on. No unlabeled whole-page date guessing is allowed.
    """
    checked = set()
    profile = _family_profile(category, family)

    if (
        include_learned
        and path
        and CFG.get("use_learned_date_region", True)
        and profile
    ):
        regions = get_family_date_regions(profile)

        page_cache = {}
        for region in regions:
            result = _ocr_date_region(
                path, region, progress=progress, page_cache=page_cache
            )

            if result:
                dt, confidence, region_text, details = result

                area_name = region.get(
                    "name",
                    "Date Area"
                )

                priority = int(
                    region.get(
                        "priority",
                        999
                    )
                )

                return (
                    dt,
                    confidence,
                    area_name,
                    (
                        f"learned date area Priority {priority} "
                        f"({details})"
                    )
                )

    # v65: an explicit filename-date preference outranks inferred labels.
    date_profile = _date_profile(category, family, create=False)
    preferred_label = normalize_text(
        (date_profile or {}).get("preferred_date_label", "")
    )
    if preferred_label:
        checked.add(preferred_label)
        result = _find_date_near_label(text, preferred_label, 99)
        if result:
            dt, conf, raw, proximity = result
            return dt, conf, preferred_label, f"preferred date label ({proximity})"

    if include_learned and profile:
        for label in profile.get("date_labels", []):
            label = normalize_text(label)

            if not label or label in checked:
                continue

            checked.add(label)

            result = _find_date_near_label(
                text,
                label,
                99
            )

            if result:
                dt, conf, raw, proximity = result

                return (
                    dt,
                    conf,
                    label,
                    f"learned label ({proximity})"
                )

    rules = DATE_LABELS_BY_FAMILY.get(
        (category, clean_name(family)),
        []
    )

    for label, confidence in rules:
        label = normalize_text(label)

        if label in checked:
            continue

        checked.add(label)

        result = _find_date_near_label(
            text,
            label,
            confidence
        )

        if result:
            dt, conf, raw, proximity = result

            return (
                dt,
                conf,
                label,
                f"family rule ({proximity})"
            )

    for label, confidence in GENERIC_SAFE_DATE_LABELS:
        label = normalize_text(label)

        if label in checked:
            continue

        checked.add(label)

        result = _find_date_near_label(
            text,
            label,
            confidence
        )

        if result:
            dt, conf, raw, proximity = result

            return (
                dt,
                conf,
                label,
                f"safe label ({proximity})"
            )

    # A validated spatial field is a final fallback; existing date priorities win.
    field = getattr(text, "structured", {}).get("fields", {}).get("Start Date", {})
    if (not preferred_label or preferred_label in STRUCTURED_FIELD_LABELS["Start Date"]) and field.get("trusted"):
        confidence = field.get("confidence", 0)
        if confidence >= float(CFG.get("date_confidence_threshold", 92)):
            dt = parse_date(field.get("value", ""))
            if dt:
                return dt, confidence, "start date", "structured labeled field"
    return None, 0, "none", "none"


def learn_family_date_label(category, family, text):
    """Teach the family which printed date label has proven reliable."""
    profile = _family_profile(category, family)

    if not profile:
        return None

    dt, confidence, label, source = detect_family_date(
        category,
        family,
        text,
        path=None,
        include_learned=False
    )

    # Only store a label that is already safe enough to name a file.
    if (
        not dt
        or confidence < int(CFG.get("date_confidence_threshold", 92))
        or label == "none"
    ):
        return None

    labels = [
        normalize_text(value)
        for value in profile.get("date_labels", [])
        if normalize_text(value)
    ]

    if label not in labels:
        labels.insert(0, label)

    profile["date_labels"] = labels[:5]
    profile["date_label_updated_at"] = datetime.now().isoformat(
        timespec="seconds"
    )
    save_learning()

    return {
        "date": dt,
        "confidence": confidence,
        "label": label,
        "source": source,
    }


def detect_optional_date(text, category="", family="", path=None):
    """Compatibility wrapper for older calls."""
    dt, confidence, label, source = detect_family_date(
        category,
        family,
        text,
        path=path
    )

    return dt, confidence, label


# ============================================================================
# Duplicate guard
# ============================================================================

_FILE_SHA_CACHE = {}


def sha256_file(path):
    """SHA-256 with a small metadata-keyed cache.

    The same Incoming file is consulted by exact-duplicate checks, persistent
    manual-date lookup, and duplicate indexing. Avoid hashing it repeatedly.
    """
    p = Path(path)

    try:
        st = p.stat()
        cache_key = (
            str(p.resolve()).lower(),
            int(st.st_size),
            int(st.st_mtime_ns),
        )
    except Exception:
        cache_key = None

    if cache_key is not None:
        cached = _FILE_SHA_CACHE.get(
            cache_key
        )

        if cached:
            return cached

    digest = hashlib.sha256()

    with open(p, "rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b""
        ):
            digest.update(chunk)

    value = digest.hexdigest()

    if cache_key is not None:
        _FILE_SHA_CACHE[cache_key] = value

        # Prevent an unbounded long-running cache.
        if len(_FILE_SHA_CACHE) > 512:
            for old_key in list(
                _FILE_SHA_CACHE.keys()
            )[:128]:
                _FILE_SHA_CACHE.pop(
                    old_key,
                    None
                )

    return value



def document_date_key(path):
    """Exact content identity for a document.

    SHA-256 survives renames and moves. Manual dates therefore stay attached to
    the exact document without leaking to another copy of the same form.
    """
    p = Path(path)

    if not p.exists() or not p.is_file():
        return ""

    try:
        return "sha256:" + sha256_file(p)
    except Exception:
        return ""


def get_saved_manual_date(path):
    key = document_date_key(path)

    if not key:
        return None, None

    entry = DOCUMENT_DATES.get("documents", {}).get(key)

    if not entry:
        return None, None

    raw = entry.get("date", "")
    dt = parse_date(raw)

    if not dt:
        return None, entry

    return dt, entry


def save_manual_document_date(path, dt, category="", family=""):
    if not dt:
        return False

    key = document_date_key(path)

    if not key:
        return False

    p = Path(path)
    now = datetime.now().isoformat(timespec="seconds")
    existing = DOCUMENT_DATES["documents"].get(key, {})

    DOCUMENT_DATES["documents"][key] = {
        "date": dt.strftime("%Y-%m-%d"),
        "category": clean_name(category) if category else existing.get("category", ""),
        "family": clean_name(family) if family else existing.get("family", ""),
        "last_filename": p.name,
        "last_path": str(p),
        "source": "manual",
        "created_at": existing.get("created_at", now),
        "updated_at": now,
    }

    save_document_dates()
    return True


def update_saved_manual_date_path(path):
    """Refresh informational path metadata after SmartScan moves/renames a file."""
    key = document_date_key(path)

    if not key:
        return False

    entry = DOCUMENT_DATES.get("documents", {}).get(key)

    if not entry:
        return False

    p = Path(path)
    entry["last_filename"] = p.name
    entry["last_path"] = str(p)
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_document_dates()
    return True


def strict_duplicate_signature(text):
    text = str(text or "").lower()
    text = text.replace("\\", "/")
    text = re.sub(r"[–—−_]", "-", text)
    text = re.sub(r"[\u2018\u2019`]", "'", text)
    text = re.sub(r"[\u201c\u201d]", '"', text)
    text = re.sub(r"[^a-z0-9./:$%#@+\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(text.split()[:3500])


def duplicate_token_set(signature):
    return set(
        re.findall(r"[a-z0-9]{2,}", signature or "")
    )


def token_similarity(a, b):
    aa = duplicate_token_set(a)
    bb = duplicate_token_set(b)

    if not aa or not bb:
        return 0.0

    return len(aa & bb) / len(aa | bb)


def visual_document_hash(path, hash_size=16):
    img = _load_page_image(path, 0, scale=1.0)
    if img is None:
        return ""

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    img = ImageOps.autocontrast(
        ImageOps.grayscale(img)
    )
    img = img.resize((hash_size + 1, hash_size), resample)

    pixels = list(img.getdata())
    bits = []
    width = hash_size + 1

    for y in range(hash_size):
        row = y * width
        for x in range(hash_size):
            bits.append(
                1 if pixels[row + x] > pixels[row + x + 1]
                else 0
            )

    value = 0
    for bit in bits:
        value = (value << 1) | bit

    return f"{value:0{(len(bits) + 3) // 4}x}"


def visual_hash_distance(a, b):
    if not a or not b or len(a) != len(b):
        return 999

    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except Exception:
        return 999



def _raily_page_image(path, max_side=1100):
    """Load the whole first page for RAILY visual analysis without OCR."""
    img = _load_page_image(path, 0, scale=1.15)
    if img is None:
        return None
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), resample)
    return img


def raily_full_page_signature(path):
    """Compact whole-page visual signature used before OCR classification.

    The signature intentionally looks at the entire page: overall layout,
    whitespace, printed blocks and line density. It lets a taught form be
    recognized even when OCR text is imperfect or spaced badly.
    """
    img = _raily_page_image(path, max_side=900)
    if img is None:
        return {}

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    gray = ImageOps.autocontrast(ImageOps.grayscale(img))
    aspect = round(gray.width / max(1, gray.height), 4)

    # Normalize the whole page to a fixed canvas, preserving layout.
    canvas = Image.new("L", (72, 96), 255)
    fitted = gray.copy()
    fitted.thumbnail((72, 96), resample)
    ox = (72 - fitted.width) // 2
    oy = (96 - fitted.height) // 2
    canvas.paste(fitted, (ox, oy))

    # 9 x 12 darkness grid.
    grid = []
    px = canvas.load()
    for gy in range(12):
        for gx in range(9):
            total = 0
            count = 0
            x0, x1 = gx * 8, (gx + 1) * 8
            y0, y1 = gy * 8, (gy + 1) * 8
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    total += 255 - px[xx, yy]
                    count += 1
            grid.append(int(round(100 * total / max(1, count * 255))))

    # Coarse projections capture tables/headers/signature blocks.
    hproj = []
    for gy in range(16):
        y0, y1 = gy * 6, min(96, (gy + 1) * 6)
        total = 0
        count = 0
        for yy in range(y0, y1):
            for xx in range(72):
                total += 255 - px[xx, yy]
                count += 1
        hproj.append(int(round(100 * total / max(1, count * 255))))

    vproj = []
    for gx in range(12):
        x0, x1 = gx * 6, min(72, (gx + 1) * 6)
        total = 0
        count = 0
        for yy in range(96):
            for xx in range(x0, x1):
                total += 255 - px[xx, yy]
                count += 1
        vproj.append(int(round(100 * total / max(1, count * 255))))

    return {
        "visual_hash": visual_document_hash(path, hash_size=16),
        "aspect": aspect,
        "grid": grid,
        "hproj": hproj,
        "vproj": vproj,
    }


def raily_visual_similarity(a, b):
    if not a or not b:
        return 0.0

    def list_score(x, y):
        if not x or not y or len(x) != len(y):
            return 0.0
        mad = sum(abs(int(i) - int(j)) for i, j in zip(x, y)) / len(x)
        return max(0.0, 100.0 - mad * 2.25)

    grid_score = list_score(a.get("grid"), b.get("grid"))
    h_score = list_score(a.get("hproj"), b.get("hproj"))
    v_score = list_score(a.get("vproj"), b.get("vproj"))

    hash_score = 0.0
    ah, bh = a.get("visual_hash", ""), b.get("visual_hash", "")
    if ah and bh and len(ah) == len(bh):
        bits = len(ah) * 4
        dist = visual_hash_distance(ah, bh)
        if dist < 999:
            hash_score = max(0.0, 100.0 * (1.0 - dist / max(1, bits)))

    aspect_a = float(a.get("aspect", 0) or 0)
    aspect_b = float(b.get("aspect", 0) or 0)
    if aspect_a and aspect_b:
        ratio = min(aspect_a, aspect_b) / max(aspect_a, aspect_b)
        aspect_score = 100.0 * ratio
    else:
        aspect_score = 0.0

    return (
        0.45 * grid_score
        + 0.20 * h_score
        + 0.15 * v_score
        + 0.15 * hash_score
        + 0.05 * aspect_score
    )


def raily_match_visual_document(path):
    """Return the best learned full-page visual family, independent of OCR."""
    if not CFG.get("raily_visual_ai_enabled", True):
        return None

    incoming = raily_full_page_signature(path)
    if not incoming:
        return None

    best = None
    for profile in LEARNED.get("profiles", []):
        examples = list(profile.get("visual_examples", []) or [])
        latest = profile.get("visual_signature") or {}
        if latest:
            examples.append(latest)
        if not examples:
            continue

        score = max(raily_visual_similarity(incoming, ex) for ex in examples if ex)
        if best is None or score > best["score"]:
            best = {
                "category": clean_optional_name(profile.get("category", "")),
                "family": clean_optional_name(profile.get("family", "")),
                "railroad": clean_optional_name(profile.get("railroad", "")),
                "location": clean_optional_name(profile.get("location", "")),
                "score": float(score),
                "profile": profile,
                "signature": incoming,
            }

    if not best:
        return None
    if best["score"] < float(CFG.get("raily_visual_match_threshold", 80)):
        return None
    return best


def _raily_extract_labeled_value(lines, labels, max_len=80):
    label_alt = "|".join(re.escape(label) for label in labels)
    for i, raw in enumerate(lines):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        m = re.search(
            rf"(?:^|\b)(?:{label_alt})\s*(?:name)?\s*[:\-–—]?\s*(.+)$",
            line,
            flags=re.I,
        )
        if m:
            value = m.group(1).strip(" :-–—|\t")
            if value and len(value) <= max_len and not re.fullmatch(label_alt, value, flags=re.I):
                return value
        normalized = line.casefold().strip(" :")
        if any(normalized == label.casefold() for label in labels) and i + 1 < len(lines):
            value = re.sub(r"\s+", " ", lines[i + 1]).strip(" :-–—|\t")
            if value and len(value) <= max_len:
                return value
    return ""


def _raily_clean_entity(value, kind=""):
    value = re.sub(r"\s+", " ", str(value or "")).strip(" ,;:|\t\r\n-")
    value = re.sub(r"\s{2,}", " ", value)
    if not value:
        return ""
    # Cut obvious neighboring field labels accidentally captured by OCR.
    value = re.split(
        r"\b(?:date|employee|location|railroad|supervisor|signature|time|hours|phone|email)\s*[:#]?",
        value,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,;:-")
    if len(value) < 2 or len(value) > 80:
        return ""
    if kind == "person":
        # Avoid accepting a field heading as a person's name.
        bad = {"employee", "employee name", "name", "supervisor", "signature", "engineer", "conductor"}
        if value.casefold() in bad:
            return ""
    return value


def raily_extract_metadata(text, visual_match=None, ai_result=None):
    """Extract route/person metadata; learned values outrank fragile OCR guesses."""
    ai_result = ai_result or {}
    visual_match = visual_match or {}
    structured = getattr(text, "structured", None)
    text = structured["classification_text"] if structured else str(text or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    haystack = normalize_text(text)

    result = {
        "railroad": _raily_clean_entity(ai_result.get("railroad", ""), "railroad"),
        "location": _raily_clean_entity(ai_result.get("location", ""), "location"),
        "person": _raily_clean_entity(ai_result.get("person", ""), "person"),
        "person_role": _raily_clean_entity(ai_result.get("person_role", "")),
        "source": "vision AI" if ai_result else "",
    }

    if structured:
        result["field_evidence"] = structured["fields"]
        for label, key in (("Name", "person"), ("Location", "location"), ("Start Date", "start_date")):
            field = structured["fields"].get(label, {})
            if field.get("trusted") and not result.get(key):
                result[key] = field["value"] if key == "start_date" else _raily_clean_entity(field["value"], key)
                result["source"] = result["source"] or "label-aware OCR"
                if key == "person":
                    result["person_role"] = result.get("person_role") or "Employee"

    # Exact learned entities found in the page are stronger than free-form OCR parsing.
    if not result["railroad"]:
        for name in known_raily_entities("railroads"):
            if normalize_text(name) in haystack:
                result["railroad"] = name
                result["source"] = result["source"] or "known railroad"
                break
    if not result["location"]:
        for name in known_raily_entities("locations"):
            if normalize_text(name) in haystack:
                result["location"] = name
                result["source"] = result["source"] or "known location"
                break
    if not result["person"]:
        for name in known_raily_entities("people"):
            if normalize_text(name) in haystack:
                result["person"] = name
                result["source"] = result["source"] or "known person"
                break

    if not result["railroad"]:
        result["railroad"] = _raily_clean_entity(_raily_extract_labeled_value(
            lines,
            ["railroad name", "railroad", "rr name", "company", "employer"],
        ), "railroad")
    if not result["location"] and not structured:
        result["location"] = _raily_clean_entity(_raily_extract_labeled_value(
            lines,
            ["work location", "job location", "location", "terminal", "yard", "station", "site"],
        ), "location")
    if not result["person"] and not structured:
        result["person"] = _raily_clean_entity(_raily_extract_labeled_value(
            lines,
            ["employee name", "employee", "contractor name", "operator name", "name"],
        ), "person")
        if result["person"]:
            result["person_role"] = result["person_role"] or "Employee"

    # A visually learned form can safely supply stable route defaults when OCR
    # cannot read them. Person is never blindly inherited because it can vary.
    if not result["railroad"]:
        result["railroad"] = _raily_clean_entity(visual_match.get("railroad", ""), "railroad")
        if result["railroad"]:
            result["source"] = result["source"] or "visual family default"
    if not result["location"]:
        result["location"] = _raily_clean_entity(visual_match.get("location", ""), "location")
        if result["location"]:
            result["source"] = result["source"] or "visual family default"

    return result


def _raily_image_data_url(path):
    img = _raily_page_image(path, max_side=1400)
    if img is None:
        return ""
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def raily_external_vision_analysis(path):
    """Optional OpenAI-compatible full-page vision endpoint.

    No network AI is required for SmartScan to work. If a URL/model/key are
    configured, RAILY sends one whole-page image and expects JSON metadata.
    """
    provider = str(CFG.get("raily_ai_provider", "Local Full-Page"))
    url = str(CFG.get("raily_vision_api_url", "") or "").strip()
    model = str(CFG.get("raily_vision_model", "") or "").strip()
    env_name = str(CFG.get("raily_vision_api_key_env", "RAILY_VISION_API_KEY") or "").strip()
    api_key = os.environ.get(env_name, "") if env_name else ""
    if provider.casefold() == "local full-page".casefold() or not url or not model:
        return None
    if not api_key:
        return None

    image_url = _raily_image_data_url(path)
    if not image_url:
        return None

    prompt = (
        "Analyze this entire scanned document page. Return JSON only with keys: "
        "document_type, category, railroad, location, person, person_role, date, confidence. "
        "Choose the primary person named by the document, not a supervisor/signature unless that "
        "is clearly the subject. Do not invent missing values; use empty strings. Date should be "
        "YYYY-MM-DD when confidently visible."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are RAILY, a precise railroad document classifier."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        "temperature": 0,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"RAILY-SmartScan/{APP_VERSION}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8", errors="replace"))
        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
        content = str(content or "").strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S).strip()
        result = json.loads(content)
        if not isinstance(result, dict):
            return None
        result["confidence"] = int(float(result.get("confidence", 0) or 0))
        return result
    except Exception:
        return None


def _date_string(value):
    """Normalize a datetime/date-like value to YYYY-MM-DD or empty string."""
    if not value:
        return ""

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    dt = parse_date(str(value))
    return dt.strftime("%Y-%m-%d") if dt else ""


def _filename_document_date(path):
    """Read SmartScan's YYYY-MM-DD filename prefix when present."""
    name = Path(path).stem

    match = re.match(
        r"^((?:19|20)\d{2}-\d{2}-\d{2})(?:_|$)",
        name
    )

    if not match:
        return ""

    dt = parse_date(match.group(1))
    return dt.strftime("%Y-%m-%d") if dt else ""


def _manual_date_from_sha(sha_value):
    if not sha_value:
        return ""

    entry = DOCUMENT_DATES.get(
        "documents",
        {}
    ).get(
        "sha256:" + str(sha_value)
    )

    if not entry:
        return ""

    return _date_string(
        entry.get("date")
    )


def duplicate_entry_date(entry):
    """Get the best known date for an indexed document without rerunning OCR."""
    if not entry:
        return ""

    direct = _date_string(
        entry.get("document_date")
    )

    if direct:
        return direct

    manual = _manual_date_from_sha(
        entry.get("sha256")
    )

    if manual:
        return manual

    path = entry.get("path", "")

    if path:
        return _filename_document_date(
            path
        )

    return ""


def duplicate_size_similarity(a, b):
    try:
        aa = max(1, int(a or 0))
        bb = max(1, int(b or 0))
    except Exception:
        return 0.0

    return min(aa, bb) / max(aa, bb)


def find_exact_duplicate(path):
    """Exact content hash check; classification/date is irrelevant."""
    p = Path(path)

    if not p.exists():
        return None

    try:
        incoming_size = p.stat().st_size
        incoming_sha = sha256_file(p)
    except Exception:
        return None

    for entry in list(
        INDEX.get("files", {}).values()
    ):
        existing_path = Path(
            entry.get("path", "")
        )

        if not existing_path.exists():
            continue

        try:
            if existing_path.resolve() == p.resolve():
                continue
        except Exception:
            pass

        if (
            incoming_sha
            and incoming_sha == entry.get("sha256")
            and int(incoming_size)
            == int(entry.get("size", -1))
        ):
            return (
                "exact",
                existing_path,
                1.0,
                "identical SHA-256 file hash"
            )

    return None


def index_document(
    path,
    text,
    category="",
    family="",
    document_date=None,
    date_source="",
    railroad="",
    location="",
    person=""
):
    p = Path(path)

    if not p.exists():
        return

    try:
        st = p.stat()
        sha_value = sha256_file(p)

        date_value = _date_string(
            document_date
        )

        if not date_value:
            date_value = _manual_date_from_sha(
                sha_value
            )

        if not date_value:
            date_value = _filename_document_date(
                p
            )

        INDEX["files"][
            str(p.resolve()).lower()
        ] = {
            "path": str(p),
            "size": st.st_size,
            "sha256": sha_value,
            "strict_text_sig": strict_duplicate_signature(text),
            "visual_hash": visual_document_hash(p),
            "document_identifier": _extract_document_identifier(text),
            "page_count": document_page_count(p),
            "category": category,
            "family": family,
            "railroad": clean_optional_name(railroad),
            "location": clean_optional_name(location),
            "person": clean_optional_name(person),
            "document_date": date_value,
            "date_source": (
                date_source
                if date_value
                else ""
            ),
            "indexed_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

        save_index()

    except Exception as exc:
        log(
            f"Index error {p}: {exc}"
        )


def cleanup_index():
    dead = []

    for key, entry in INDEX["files"].items():
        if not Path(entry.get("path", "")).exists():
            dead.append(key)

    for key in dead:
        INDEX["files"].pop(key, None)

    if dead:
        save_index()


def duplicate_score(incoming, existing):
    """Compare two indexed document records.

    Date is a strengthening signal, never the only signal.
    """
    if (
        incoming.get("sha256")
        and incoming.get("sha256")
        == existing.get("sha256")
        and int(incoming.get("size", 0))
        == int(existing.get("size", -1))
    ):
        return (
            "exact",
            1.0,
            "identical file hash"
        )

    a = incoming.get(
        "strict_text_sig",
        ""
    )
    b = existing.get(
        "strict_text_sig",
        ""
    )

    if len(a) < 80 or len(b) < 80:
        return None

    seq = SequenceMatcher(
        None,
        a[:16000],
        b[:16000]
    ).ratio()

    tok = token_similarity(
        a,
        b
    )

    vdist = visual_hash_distance(
        incoming.get(
            "visual_hash",
            ""
        ),
        existing.get(
            "visual_hash",
            ""
        )
    )

    size_sim = duplicate_size_similarity(
        incoming.get("size"),
        existing.get("size")
    )

    incoming_date = duplicate_entry_date(
        incoming
    )
    existing_date = duplicate_entry_date(
        existing
    )

    same_date = bool(
        incoming_date
        and existing_date
        and incoming_date == existing_date
    )

    # If both documents have trustworthy dates and those dates differ, they
    # are not duplicate copies of the same dated document. Exact hash was
    # already handled above.
    if (
        CFG.get("date_aware_duplicates", True)
        and incoming_date
        and existing_date
        and incoming_date != existing_date
    ):
        return None

    same_category = (
        not incoming.get("category")
        or not existing.get("category")
        or incoming.get("category")
        == existing.get("category")
    )

    same_family = (
        not incoming.get("family")
        or not existing.get("family")
        or clean_name(
            incoming.get("family", "")
        ).casefold()
        == clean_name(
            existing.get("family", "")
        ).casefold()
    )

    # v57: same exact family + same date permits a somewhat more tolerant
    # content comparison, which catches rescans/compression differences while
    # still requiring the documents themselves to be strongly alike.
    if (
        CFG.get(
            "date_aware_duplicates",
            True
        )
        and same_date
        and same_category
        and same_family
        and size_sim
        >= float(
            CFG.get(
                "duplicate_same_date_min_size_similarity",
                0.72
            )
        )
    ):
        date_text_threshold = float(
            CFG.get(
                "duplicate_same_date_text_threshold",
                0.86
            )
        )
        date_token_threshold = float(
            CFG.get(
                "duplicate_same_date_token_threshold",
                0.82
            )
        )
        date_visual_limit = int(
            CFG.get(
                "duplicate_same_date_visual_max_distance",
                5
            )
        )

        if (
            seq >= date_text_threshold
            and tok >= date_token_threshold
        ):
            score = (
                seq * 0.55
                + tok * 0.25
                + size_sim * 0.20
            )

            return (
                "same-date",
                score,
                (
                    f"same document date {incoming_date}, "
                    f"text {seq:.0%}, tokens {tok:.0%}, "
                    f"size {size_sim:.0%}"
                )
            )

        if (
            vdist <= date_visual_limit
            and seq >= max(
                0.80,
                date_text_threshold - 0.04
            )
            and tok >= max(
                0.76,
                date_token_threshold - 0.04
            )
        ):
            visual_score = max(
                0.0,
                1.0 - vdist / 256.0
            )

            score = (
                seq * 0.45
                + tok * 0.20
                + visual_score * 0.20
                + size_sim * 0.15
            )

            return (
                "same-date",
                score,
                (
                    f"same document date {incoming_date}, "
                    f"text {seq:.0%}, visual distance {vdist}, "
                    f"size {size_sim:.0%}"
                )
            )

    # Existing non-date duplicate rules remain as a fallback.
    if seq >= 0.97:
        return (
            "possible",
            seq,
            f"text {seq:.0%}"
        )

    if (
        seq
        >= float(
            CFG.get(
                "duplicate_text_threshold",
                0.91
            )
        )
        and tok >= 0.88
    ):
        score = (
            seq * 0.75
            + tok * 0.25
        )

        return (
            "possible",
            score,
            f"text {seq:.0%}, tokens {tok:.0%}"
        )

    if (
        vdist
        <= int(
            CFG.get(
                "duplicate_visual_max_distance",
                3
            )
        )
        and seq
        >= float(
            CFG.get(
                "duplicate_visual_text_threshold",
                0.80
            )
        )
        and tok >= 0.72
    ):
        visual_score = max(
            0.0,
            1.0 - vdist / 256.0
        )

        score = (
            seq * 0.60
            + tok * 0.25
            + visual_score * 0.15
        )

        return (
            "possible",
            score,
            (
                f"text {seq:.0%}, "
                f"visual distance {vdist}"
            )
        )

    return None



def _history_entry_key(entry):
    sha_value = str(entry.get("sha256", "") or "").strip().lower()
    if sha_value:
        return "sha:" + sha_value

    return "fallback:" + "||".join([
        clean_name(entry.get("category", "")).casefold(),
        clean_name(entry.get("family", "")).casefold(),
        _date_string(entry.get("document_date")),
        str(entry.get("document_identifier", "") or "").upper(),
        str(entry.get("size", "") or ""),
        str(entry.get("strict_text_sig", "") or "")[:800],
    ])


def record_family_duplicate_history(
    path,
    text,
    category="",
    family="",
    document_date=None,
):
    """Remember one successfully filed original for future family-aware checks.

    These records remain even if the user later reorganizes or moves the original
    PDF, which lets SmartScan catch a rescan months later.
    """
    family = clean_name(family)
    category = clean_name(category)

    if not family:
        return

    p = Path(path)
    if not p.exists():
        return

    try:
        st = p.stat()
        entry = {
            "path": str(p),
            "size": int(st.st_size),
            "sha256": sha256_file(p),
            "strict_text_sig": strict_duplicate_signature(text),
            "visual_hash": visual_document_hash(p),
            "document_identifier": _extract_document_identifier(text),
            "page_count": document_page_count(p),
            "category": category,
            "family": family,
            "document_date": _date_string(document_date),
            "filed_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as exc:
        log(f"Family duplicate history record error {p}: {exc}")
        return

    key = _history_entry_key(entry)
    entries = list(FAMILY_DUP_HISTORY.get("entries", []) or [])

    # Update an existing original rather than creating another history row.
    updated = False
    for idx, existing in enumerate(entries):
        if _history_entry_key(existing) == key:
            merged = dict(existing)
            merged.update(entry)
            entries[idx] = merged
            updated = True
            break

    if not updated:
        entries.append(entry)

    max_entries = max(
        250,
        int(CFG.get("family_history_max_entries", 5000))
    )
    FAMILY_DUP_HISTORY["entries"] = entries[-max_entries:]
    FAMILY_DUP_HISTORY["version"] = 68
    save_family_duplicate_history()


def seed_family_duplicate_history_from_index():
    """Backfill v68 history from the user's existing SmartScan index once.

    This is what makes the new feature useful immediately with documents filed
    before v68 instead of only learning history from this release forward.
    """
    if FAMILY_DUP_HISTORY.get("seeded_from_index"):
        return

    entries = list(FAMILY_DUP_HISTORY.get("entries", []) or [])
    seen = {_history_entry_key(item) for item in entries}

    added = 0

    for indexed in list(INDEX.get("files", {}).values()):
        family = clean_name(indexed.get("family", ""))
        if not family:
            continue

        history_entry = {
            "path": str(indexed.get("path", "") or ""),
            "size": int(indexed.get("size", 0) or 0),
            "sha256": str(indexed.get("sha256", "") or ""),
            "strict_text_sig": str(indexed.get("strict_text_sig", "") or ""),
            "visual_hash": str(indexed.get("visual_hash", "") or ""),
            "document_identifier": str(
                indexed.get("document_identifier", "")
                or _extract_document_identifier(
                    indexed.get("strict_text_sig", "")
                )
                or ""
            ),
            "page_count": int(indexed.get("page_count", 0) or 0),
            "category": clean_name(indexed.get("category", "")),
            "family": family,
            "document_date": duplicate_entry_date(indexed),
            "filed_at": str(indexed.get("indexed_at", "") or ""),
        }

        if history_entry["page_count"] <= 0:
            p = Path(history_entry["path"])
            if p.exists():
                history_entry["page_count"] = document_page_count(p)

        key = _history_entry_key(history_entry)
        if key in seen:
            continue

        entries.append(history_entry)
        seen.add(key)
        added += 1

    max_entries = max(
        250,
        int(CFG.get("family_history_max_entries", 5000))
    )
    FAMILY_DUP_HISTORY["entries"] = entries[-max_entries:]
    FAMILY_DUP_HISTORY["seeded_from_index"] = True
    FAMILY_DUP_HISTORY["version"] = 68
    save_family_duplicate_history()

    if added:
        log(
            f"v68 family duplicate history seeded with {added} "
            "previously indexed document(s)."
        )


def _family_history_display_path(entry):
    raw = str(entry.get("path", "") or "").strip()
    if raw:
        return Path(raw)

    family = clean_name(entry.get("family", "")) or "Historical"
    date_value = _date_string(entry.get("document_date")) or "unknown-date"
    return Path(f"{family}_{date_value}.pdf")


def family_historical_duplicate(
    path,
    text,
    category="",
    family="",
    document_date=None,
):
    """Compare a new scan to prior originals in the same learned family.

    The family narrows the search; it is NOT itself duplicate evidence.
    SmartScan still requires matching dates/identifiers and strong OCR/content
    similarity before moving a scan to Duplicates Review.
    """
    if not CFG.get("family_history_duplicates", True):
        return None

    family_key = clean_name(family).casefold()
    category_key = clean_name(category).casefold()

    if not family_key:
        return None

    p = Path(path)
    if not p.exists():
        return None

    try:
        incoming_size = int(p.stat().st_size)
    except Exception:
        incoming_size = 0

    incoming_sha = sha256_file(p)
    incoming_sig = strict_duplicate_signature(text)
    incoming_date = _date_string(document_date)
    incoming_identifier = _extract_document_identifier(text)
    incoming_pages = document_page_count(p)

    candidates = []

    for entry in reversed(list(FAMILY_DUP_HISTORY.get("entries", []) or [])):
        if clean_name(entry.get("family", "")).casefold() != family_key:
            continue

        entry_category = clean_name(entry.get("category", "")).casefold()
        if category_key and entry_category and category_key != entry_category:
            continue

        raw_path = str(entry.get("path", "") or "")
        if raw_path:
            try:
                if Path(raw_path).exists() and Path(raw_path).resolve() == p.resolve():
                    continue
            except Exception:
                pass

        candidates.append(entry)

    if not candidates:
        return None

    best = None

    for entry in candidates:
        existing_sha = str(entry.get("sha256", "") or "")

        # Historical exact hash remains useful even if the original file was
        # later moved outside the SmartScan Sorted tree.
        if incoming_sha and existing_sha and incoming_sha == existing_sha:
            return (
                "family-history-exact",
                _family_history_display_path(entry),
                1.0,
                "same learned family and identical historical SHA-256 file hash"
            )

        existing_sig = str(entry.get("strict_text_sig", "") or "")
        if len(incoming_sig) < 80 or len(existing_sig) < 80:
            continue

        existing_date = _date_string(entry.get("document_date"))
        existing_identifier = str(
            entry.get("document_identifier", "")
            or _extract_document_identifier(existing_sig)
            or ""
        ).upper()

        # Different known dates or different known account/reference numbers
        # are strong evidence that these are separate family members.
        if incoming_date and existing_date and incoming_date != existing_date:
            continue

        if (
            incoming_identifier
            and existing_identifier
            and incoming_identifier != existing_identifier
        ):
            continue

        seq = SequenceMatcher(
            None,
            incoming_sig[:16000],
            existing_sig[:16000]
        ).ratio()
        tok = token_similarity(incoming_sig, existing_sig)
        size_sim = duplicate_size_similarity(
            incoming_size,
            entry.get("size", 0)
        )

        existing_pages = int(entry.get("page_count", 0) or 0)
        same_pages = (
            not incoming_pages
            or not existing_pages
            or incoming_pages == existing_pages
        )

        same_date = bool(
            incoming_date
            and existing_date
            and incoming_date == existing_date
        )
        same_identifier = bool(
            incoming_identifier
            and existing_identifier
            and incoming_identifier == existing_identifier
        )

        kind = None
        score = 0.0
        details = ""

        # Strongest non-hash case: same family + same date + same explicit
        # account/invoice/reference identifier.
        if (
            same_date
            and same_identifier
            and seq >= 0.82
            and tok >= 0.78
            and size_sim >= 0.60
        ):
            score = (
                seq * 0.45
                + tok * 0.20
                + size_sim * 0.15
                + 0.20
            )
            kind = "family-history-confirmed"
            details = (
                f"same family, date {incoming_date}, identifier "
                f"{incoming_identifier}, text {seq:.0%}, tokens {tok:.0%}"
            )

        # Same family + same date with very strong content similarity.
        elif (
            same_date
            and seq >= float(
                CFG.get("family_history_same_date_text_threshold", 0.90)
            )
            and tok >= float(
                CFG.get("family_history_same_date_token_threshold", 0.84)
            )
            and size_sim >= 0.68
            and same_pages
        ):
            score = (
                seq * 0.55
                + tok * 0.25
                + size_sim * 0.15
                + 0.05
            )
            kind = "family-history"
            details = (
                f"same family and date {incoming_date}; "
                f"text {seq:.0%}, tokens {tok:.0%}, size {size_sim:.0%}"
            )

        # Same explicit account/invoice/reference can substitute for a missing
        # date, but content must be even stronger.
        elif (
            same_identifier
            and seq >= float(
                CFG.get("family_history_identifier_text_threshold", 0.93)
            )
            and tok >= float(
                CFG.get("family_history_identifier_token_threshold", 0.88)
            )
            and size_sim >= 0.72
            and same_pages
        ):
            score = (
                seq * 0.55
                + tok * 0.25
                + size_sim * 0.15
                + 0.05
            )
            kind = "family-history"
            details = (
                f"same family and identifier {incoming_identifier}; "
                f"text {seq:.0%}, tokens {tok:.0%}, size {size_sim:.0%}"
            )

        # With neither date nor identifier, require an almost identical OCR
        # signature so ordinary recurring statements are not mistaken as copies.
        elif (
            not incoming_date
            and not existing_date
            and not incoming_identifier
            and not existing_identifier
            and seq >= float(
                CFG.get("family_history_no_key_text_threshold", 0.985)
            )
            and tok >= float(
                CFG.get("family_history_no_key_token_threshold", 0.95)
            )
            and size_sim >= 0.82
            and same_pages
        ):
            score = (
                seq * 0.65
                + tok * 0.25
                + size_sim * 0.10
            )
            kind = "family-history-high-similarity"
            details = (
                f"same family with near-identical historical content; "
                f"text {seq:.1%}, tokens {tok:.1%}, size {size_sim:.0%}"
            )

        if kind and (
            best is None
            or score > best[2]
        ):
            best = (
                kind,
                _family_history_display_path(entry),
                min(1.0, score),
                details,
            )

    return best

def find_duplicate(
    path,
    text,
    category="",
    family="",
    document_date=None,
    skip_exact=False
):
    p = Path(path)

    if not p.exists():
        return None

    try:
        incoming_size = p.stat().st_size
    except Exception:
        return None

    incoming_date = _date_string(
        document_date
    )

    incoming_sig = strict_duplicate_signature(
        text
    )

    # Build a short candidate list first. This prevents visual rendering and
    # SequenceMatcher work against unrelated document families/dates.
    candidates = []

    for entry in list(
        INDEX.get(
            "files",
            {}
        ).values()
    ):
        existing_path = Path(
            entry.get(
                "path",
                ""
            )
        )

        if not existing_path.exists():
            continue

        try:
            if existing_path.resolve() == p.resolve():
                continue
        except Exception:
            pass

        if (
            category
            and entry.get("category")
            and category != entry.get("category")
        ):
            continue

        if (
            family
            and entry.get("family")
            and clean_name(family).casefold()
            != clean_name(
                str(
                    entry.get(
                        "family",
                        ""
                    )
                )
            ).casefold()
        ):
            continue

        existing_date = duplicate_entry_date(
            entry
        )

        # If both trusted dates are known and differ, this cannot be the
        # same dated document (exact hash already handled earlier).
        if (
            CFG.get(
                "date_aware_duplicates",
                True
            )
            and incoming_date
            and existing_date
            and incoming_date != existing_date
        ):
            continue

        candidates.append(
            entry
        )

    if not candidates:
        return family_historical_duplicate(
            p,
            text,
            category,
            family,
            document_date=document_date
        )

    incoming = {
        "path": str(p),
        "size": incoming_size,
        # Exact duplicate was already checked by find_exact_duplicate.
        "sha256": (
            ""
            if skip_exact
            else sha256_file(p)
        ),
        "strict_text_sig": incoming_sig,
        "visual_hash": "",
        "category": category,
        "family": family,
        "document_date": incoming_date,
    }

    best = None
    need_visual = False

    # Text/date pass first — no page rendering.
    for entry in candidates:
        result = duplicate_score(
            incoming,
            entry
        )

        if result:
            kind, score, details = result

            if (
                best is None
                or score > best[2]
            ):
                best = (
                    kind,
                    Path(
                        entry.get(
                            "path",
                            ""
                        )
                    ),
                    score,
                    details,
                )

        # Only consider expensive visual comparison when the text is already
        # in the neighborhood of a possible duplicate.
        a = incoming_sig
        b = entry.get(
            "strict_text_sig",
            ""
        )

        if (
            len(a) >= 80
            and len(b) >= 80
        ):
            rough_tokens = token_similarity(
                a,
                b
            )

            if rough_tokens >= 0.68:
                need_visual = True

    if best:
        return best

    if not need_visual:
        return None

    # Visual hash is expensive because it renders page 1. Do it ONCE, only
    # after the cheap candidate pass says it may matter.
    incoming["visual_hash"] = visual_document_hash(
        p
    )

    if not incoming["visual_hash"]:
        return None

    for entry in candidates:
        result = duplicate_score(
            incoming,
            entry
        )

        if not result:
            continue

        kind, score, details = result

        if (
            best is None
            or score > best[2]
        ):
            best = (
                kind,
                Path(
                    entry.get(
                        "path",
                        ""
                    )
                ),
                score,
                details,
            )

    if best:
        return best

    return family_historical_duplicate(
        p,
        text,
        category,
        family,
        document_date=document_date
    )


def build_destination(category, family, railroad="", location=""):
    """Build the filing route. v71 defaults to Railroad → Location → documents."""
    railroad = _raily_clean_entity(railroad, "railroad")
    location = _raily_clean_entity(location, "location")

    if CFG.get("raily_route_filing", True) and railroad and location:
        return (
            Path(CFG["sorted"])
            / clean_name(railroad)
            / clean_name(location)
        )

    # Compatibility fallback for maintenance/legacy documents. Main RAILY
    # processing can require Railroad + Location before it reaches this branch.
    destination = Path(CFG["sorted"]) / clean_name(category)
    if CFG.get("family_subfolders", True):
        destination = destination / clean_name(family)
    return destination



def build_filename(
    original,
    category,
    family,
    text,
    path=None,
    manual_date=None,
    detected_date=None,
    person=""
):
    p = Path(original)

    if not CFG.get("rename_by_type", True):
        return p.name

    family_part = clean_name(family) or "Document"
    person_part = _raily_clean_entity(person, "person") if CFG.get("raily_include_person_in_filename", True) else ""
    base = f"{person_part} - {family_part}" if person_part else family_part

    if manual_date:
        return f"{manual_date:%Y-%m-%d} - {base}{p.suffix.lower()}"

    if detected_date:
        return f"{detected_date:%Y-%m-%d} - {base}{p.suffix.lower()}"

    if path:
        remembered_date, _ = get_saved_manual_date(path)
        if remembered_date:
            return f"{remembered_date:%Y-%m-%d} - {base}{p.suffix.lower()}"

    if CFG.get("add_date_to_filename", True):
        dt, confidence, label, source = detect_family_date(
            category, family, text, path=path
        )
        if dt and confidence >= int(CFG.get("date_confidence_threshold", 92)):
            return f"{dt:%Y-%m-%d} - {base}{p.suffix.lower()}"

    return f"{base}{p.suffix.lower()}"


# ============================================================================
# Existing file helpers
# ============================================================================

def category_from_sorted_path(path):
    p = Path(path)
    root = Path(CFG["sorted"])

    try:
        entry = INDEX.get("files", {}).get(str(p.resolve()).lower(), {})
        indexed = clean_optional_name(entry.get("category", ""))
        if indexed in CATEGORY_RULES:
            return indexed
    except Exception:
        pass

    try:
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] in CATEGORY_RULES:
            return rel.parts[0]
    except Exception:
        pass

    return ""


def family_from_sorted_path(path):
    p = Path(path)
    root = Path(CFG["sorted"])

    try:
        entry = INDEX.get("files", {}).get(str(p.resolve()).lower(), {})
        indexed = clean_optional_name(entry.get("family", ""))
        if indexed:
            return indexed
    except Exception:
        pass

    try:
        rel = p.relative_to(root)

        # v71 Railroad → Location folders do not encode Document Type.
        if CFG.get("raily_route_filing", True) and rel.parts and rel.parts[0] not in CATEGORY_RULES:
            return ""

        if len(rel.parts) >= 3:
            candidate = rel.parts[1]

            if not re.fullmatch(r"(?:19|20)\d{2}", candidate):
                if candidate.lower() not in {
                    "needs date",
                    "review",
                    "repair_logs",
                    "repair_existing"
                }:
                    return clean_name(candidate)
    except Exception:
        pass

    return ""



# ============================================================================
# Automatic multi-document stack separation
# ============================================================================

def _ocr_pdf_page_object(page):
    """Read one PDF page without letting other pages influence classification."""
    embedded = page.get_text("text") or ""
    text = embedded

    # Scanned/image PDFs usually have little or no embedded text.
    if len(re.findall(r"[A-Za-z0-9]", embedded)) < 120:
        pix = page.get_pixmap(
            matrix=fitz.Matrix(1.65, 1.65),
            alpha=False
        )

        img = Image.frombytes(
            "RGB",
            [pix.width, pix.height],
            pix.samples
        )

        img = ImageOps.autocontrast(
            ImageOps.grayscale(img)
        )

        ocr = pytesseract.image_to_string(
            img,
            lang=CFG.get("ocr_language", "eng"),
            config="--psm 6"
        )

        text = "\n".join(
            value
            for value in (embedded, ocr)
            if value and value.strip()
        )

    return text


def _page_start_strength(category, family, text):
    terms = REPEATABLE_FORM_START_SIGNALS.get(
        (category, family),
        ()
    )

    if not terms:
        return 0, 999

    hay = normalize_text(text)

    hits = sum(
        1
        for term in terms
        if normalize_text(term) in hay
    )

    if len(terms) <= 2:
        required = 1
    elif len(terms) <= 4:
        required = 2
    else:
        required = 3

    return hits, required


def _looks_like_continuation_page(text):
    hay = normalize_text(text)

    return any(
        signal in hay
        for signal in CONTINUATION_PAGE_SIGNALS
    )


def _extract_page_sequence(text):
    """Return (page_number, page_total) from common 'Page X of Y' styles."""
    raw = str(text or "")
    patterns = (
        r"\bpage\s*(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s+of\s+(\d{1,3})\s+pages?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.I)
        if match:
            try:
                return int(match.group(1)), int(match.group(2))
            except Exception:
                pass
    return 0, 0


def _extract_document_identifier(text):
    """Extract a conservative account/invoice/reference identifier for splitting."""
    raw = str(text or "")
    patterns = (
        r"\baccount\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{5,24})\b",
        r"\binvoice\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{4,24})\b",
        r"\breference\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{5,24})\b",
        r"\bclaim\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{5,24})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.I)
        if match:
            value = re.sub(r"[^A-Z0-9]", "", match.group(1).upper())
            if len(value) >= 5:
                return value
    return ""


def analyze_combined_pdf_pages(path, notify=None):
    """Classify each page independently.

    Filename hints are intentionally disabled here; otherwise a combined file
    named "Expense_Report.pdf" could incorrectly bias every page as an expense
    report.
    """
    p = Path(path)

    if p.suffix.lower() != ".pdf":
        return []

    doc = fitz.open(p)
    pages = []

    try:
        total = len(doc)

        for page_index, page in enumerate(doc):
            if notify:
                notify(
                    f"Analyzing combined scan: page {page_index + 1} of {total}..."
                )

            text = _ocr_pdf_page_object(page)

            category, category_conf, _ = classify(
                text,
                ""
            )

            # Do not pass the full combined PDF here; printed-structure matching
            # against path would always inspect page 1 and contaminate later pages.
            family, family_conf, family_source = resolve_family(
                category,
                text,
                path=None
            )

            start_hits, start_required = _page_start_strength(
                category,
                family,
                text
            )

            page_number, page_total = _extract_page_sequence(text)
            document_id = _extract_document_identifier(text)

            pages.append({
                "page": page_index,
                "text": text,
                "page_number": page_number,
                "page_total": page_total,
                "document_id": document_id,
                "category": category,
                "category_conf": int(category_conf),
                "family": family,
                "family_conf": int(family_conf),
                "family_source": family_source,
                "start_hits": int(start_hits),
                "start_required": int(start_required),
                "continuation": _looks_like_continuation_page(text),
            })

    finally:
        doc.close()

    return pages


def _page_identity_is_strong(info):
    return (
        info.get("category") not in {"", "Review"}
        and int(info.get("category_conf", 0))
        >= int(CFG.get("split_category_threshold", 78))
        and int(info.get("family_conf", 0))
        >= int(CFG.get("split_family_threshold", 62))
    )


def _same_page_identity(a, b):
    return (
        clean_name(a.get("category", "")).casefold()
        == clean_name(b.get("category", "")).casefold()
        and clean_name(a.get("family", "")).casefold()
        == clean_name(b.get("family", "")).casefold()
    )


def _page_is_repeatable_new_document(info):
    if not CFG.get("split_repeated_forms", True):
        return False

    if info.get("continuation"):
        return False

    required = int(
        info.get("start_required", 999)
    )

    hits = int(
        info.get("start_hits", 0)
    )

    return (
        required < 999
        and hits >= required
    )


def segment_combined_pdf_pages(page_infos):
    """Group pages into likely documents.

    Conservative rules:
      • Strong change of Category/Family => new document.
      • Same-family pages stay together unless BOTH pages look like fresh
        starts of a known repeatable form.
      • Low-confidence/continuation pages attach to the current document.
    """
    if not page_infos:
        return []

    segments = [{
        "pages": [page_infos[0]],
        "identity": page_infos[0],
    }]

    for info in page_infos[1:]:
        current = segments[-1]
        anchor = current["identity"]
        previous = current["pages"][-1]

        new_segment = False

        if (
            _page_identity_is_strong(anchor)
            and _page_identity_is_strong(info)
        ):
            if not _same_page_identity(anchor, info):
                new_segment = True

            elif (
                CFG.get("split_use_page_numbers", True)
                and int(info.get("page_number", 0)) == 1
                and len(current.get("pages", [])) >= 1
                and int(previous.get("page_number", 0)) != 0
            ):
                # A fresh Page 1 after an already-numbered page is a very
                # strong boundary even when the family is identical.
                new_segment = True

            elif (
                CFG.get("split_use_document_ids", True)
                and info.get("document_id")
                and previous.get("document_id")
                and info.get("document_id") != previous.get("document_id")
                and (
                    int(info.get("page_number", 0)) in {0, 1}
                    or _page_is_repeatable_new_document(info)
                )
            ):
                # Same vendor/form but a new account/invoice/reference starts.
                new_segment = True

            elif (
                _page_is_repeatable_new_document(info)
                and _page_is_repeatable_new_document(previous)
            ):
                # Two consecutive pages each look like the top/start of the
                # same one-page form, e.g. two IRAIL Work Logs scanned together.
                new_segment = True

        if new_segment:
            segments.append({
                "pages": [info],
                "identity": info,
            })
            continue

        current["pages"].append(info)

        # If the segment began weakly but we later encounter a strong page,
        # use that identity for future boundary comparisons without discarding
        # the earlier page (it may be a cover/continuation page).
        if (
            not _page_identity_is_strong(current["identity"])
            and _page_identity_is_strong(info)
        ):
            current["identity"] = info

    return segments


def _split_original_backup_path(path):
    backup_dir = (
        Path(CFG["review"])
        / "Split Originals"
    )
    backup_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return unique_path(
        backup_dir
        / Path(path).name
    )



def parse_manual_page_groups(spec, total_pages):
    """Parse `1, 2-3, 4` as separate document groups.

    Every source page must appear exactly once. This avoids silent page loss.
    """
    text = str(spec or "").strip()

    if not text:
        raise ValueError("Enter page groups such as 1, 2, 3-4, 5.")

    groups = []
    used = []

    for raw in [part.strip() for part in text.split(",") if part.strip()]:
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", raw)

        if match:
            start = int(match.group(1))
            end = int(match.group(2))

            if end < start:
                raise ValueError(f"Page range {raw} is backwards.")

            pages = list(range(start, end + 1))
        elif re.fullmatch(r"\d+", raw):
            pages = [int(raw)]
        else:
            raise ValueError(
                f"Could not read page group '{raw}'. Use 1, 2-3, 4, 5-6."
            )

        for page in pages:
            if page < 1 or page > total_pages:
                raise ValueError(
                    f"Page {page} is outside this PDF (1-{total_pages})."
                )

        groups.append(pages)
        used.extend(pages)

    duplicates = sorted({page for page in used if used.count(page) > 1})

    if duplicates:
        raise ValueError(
            "Pages cannot appear in more than one document group: "
            + ", ".join(map(str, duplicates))
        )

    missing = [page for page in range(1, total_pages + 1) if page not in used]

    if missing:
        raise ValueError(
            "Every page must be assigned. Missing: "
            + ", ".join(map(str, missing))
        )

    # Page groups must be contiguous because each resulting document is a
    # contiguous PDF range. This keeps order predictable and safe.
    for pages in groups:
        if pages != list(range(min(pages), max(pages) + 1)):
            raise ValueError("Each document group must use consecutive pages.")

    return groups


def manual_split_pdf_groups(path, groups, notify=None):
    """Split one PDF into user-defined document groups.

    The original is moved to Review/Split Originals when it came from Incoming.
    Generated parts are intentionally marked ManualPart so automatic separation
    will not split them again before the user teaches them.
    """
    p = Path(path)

    if not p.exists() or p.suffix.lower() != ".pdf":
        raise ValueError("Manual page splitting is available for PDF files only.")

    source = fitz.open(p)

    try:
        total_pages = len(source)

        for pages in groups:
            for page in pages:
                if page < 1 or page > total_pages:
                    raise ValueError(f"Page {page} is outside this PDF.")

        incoming = Path(CFG["incoming"])
        incoming.mkdir(parents=True, exist_ok=True)

        staging = Path(CFG["review"]) / ".SmartScan Manual Split Staging"
        staging.mkdir(parents=True, exist_ok=True)

        staged = []
        final_parts = []

        for part_number, pages in enumerate(groups, 1):
            first = min(pages) - 1
            last = max(pages) - 1

            part_doc = fitz.open()

            try:
                part_doc.insert_pdf(
                    source,
                    from_page=first,
                    to_page=last
                )

                staged_path = unique_path(
                    staging
                    / f"{p.stem}__SmartScanManualPart__{part_number:02d}.pdf"
                )

                part_doc.save(
                    staged_path,
                    garbage=4,
                    deflate=True
                )
            finally:
                part_doc.close()

            staged.append(staged_path)

    finally:
        source.close()

    try:
        # Put all parts into Incoming only after every staged part exists.
        for staged_path in staged:
            final = unique_path(
                Path(CFG["incoming"]) / staged_path.name
            )
            shutil.move(str(staged_path), str(final))
            final_parts.append(final)

        # If the original is in Incoming, move it out so the watcher cannot
        # process the combined file again. Otherwise preserve it in place and
        # make a safety copy in Split Originals.
        backup_dir = Path(CFG["review"]) / "Split Originals"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = unique_path(backup_dir / p.name)

        try:
            in_incoming = Path(CFG["incoming"]).resolve() in p.resolve().parents
        except Exception:
            in_incoming = False

        if in_incoming:
            shutil.move(str(p), str(backup))
        else:
            shutil.copy2(str(p), str(backup))

        if notify:
            notify(
                f"Manual split created {len(final_parts)} document(s); "
                f"original saved as {backup.name}."
            )

        return final_parts, backup

    except Exception:
        # Do not leave partially-created parts behind if finalization fails.
        for part in final_parts:
            try:
                if part.exists():
                    part.unlink()
            except Exception:
                pass

        for staged_path in staged:
            try:
                if staged_path.exists():
                    staged_path.unlink()
            except Exception:
                pass

        raise



def fast_split_pdf_pages(path, notify=None):
    """Guaranteed fast stack split: one PDF page becomes one child PDF.

    No OCR or classification happens here. This is intentionally fast and is
    designed for scanner workflows where a stack of individual paper documents
    is saved as one multi-page PDF.

    The source PDF is preserved under Review/Split Originals.
    """
    p = Path(path)

    if (
        not p.exists()
        or p.suffix.lower() != ".pdf"
        or is_split_part_path(p)
    ):
        return None

    try:
        source = fitz.open(p)
    except Exception:
        return None

    try:
        page_count = len(source)

        if page_count <= 1:
            return None

        if notify:
            notify(
                f"FAST STACK: {p.name} contains {page_count} pages — "
                "splitting immediately before OCR."
            )

        incoming = Path(CFG["incoming"])
        incoming.mkdir(
            parents=True,
            exist_ok=True
        )

        staging = (
            Path(CFG["review"])
            / ".SmartScan Fast Stack Staging"
        )
        staging.mkdir(
            parents=True,
            exist_ok=True
        )

        staged = []

        # Create every page safely in staging first.
        for page_index in range(page_count):
            part = fitz.open()

            try:
                part.insert_pdf(
                    source,
                    from_page=page_index,
                    to_page=page_index
                )

                staged_path = unique_path(
                    staging
                    / (
                        f"SmartScanStack__SmartScanPart__"
                        f"{page_index + 1:03d}.pdf"
                    )
                )

                part.save(
                    staged_path,
                    garbage=3,
                    deflate=True
                )

            finally:
                part.close()

            staged.append(
                staged_path
            )

    finally:
        source.close()

    final_parts = []

    try:
        # Move original out of Incoming BEFORE children enter Incoming, so the
        # watcher can never rediscover and resplit the source.
        backup = _split_original_backup_path(
            p
        )

        shutil.move(
            str(p),
            str(backup)
        )

        # Give child pages deterministic order. Their filenames already sort
        # 001, 002, 003..., and timestamps are staggered by milliseconds.
        now = time.time()

        for index, staged_path in enumerate(
            staged
        ):
            final = unique_path(
                Path(CFG["incoming"])
                / staged_path.name
            )

            shutil.move(
                str(staged_path),
                str(final)
            )

            try:
                stamp = now + (index * 0.002)
                os.utime(
                    final,
                    (
                        stamp,
                        stamp
                    )
                )
            except Exception:
                pass

            final_parts.append(
                final
            )

        if notify:
            notify(
                f"FAST STACK COMPLETE: created {len(final_parts)} individual PDF(s)."
            )
            notify(
                f"Original combined scan backed up: {backup.name}"
            )

        return final_parts

    except Exception as exc:
        # Best-effort cleanup. If original was already moved, keep the backup.
        for staged_path in staged:
            try:
                if staged_path.exists():
                    staged_path.unlink()
            except Exception:
                pass

        for part_path in final_parts:
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass

        if notify:
            notify(
                f"Fast stack split failed safely: {exc}"
            )

        return []



def split_combined_pdf(path, notify=None):
    """Split a mixed multi-page PDF into document-sized PDFs.

    Returns:
      None  -> no split was needed
      []    -> split was attempted but failed safely
      [..]  -> new Incoming part paths

    The original is moved to Review/Split Originals after all parts are safely
    created. Generated part files contain __SmartScanPart__ in their names and
    are never recursively split.
    """
    p = Path(path)

    if (
        not CFG.get("auto_split_combined_scans", True)
        or p.suffix.lower() != ".pdf"
        or "__smartscanpart__" in p.stem.casefold()
        or "__smartscanmanualpart__" in p.stem.casefold()
        or not p.exists()
    ):
        return None

    try:
        doc = fitz.open(p)

        try:
            page_count = len(doc)
        finally:
            doc.close()

    except Exception:
        return None

    if page_count <= 1:
        return None

    if notify:
        notify(
            f"Multi-page scan detected: {p.name} has {page_count} pages. "
            "Checking for separate documents..."
        )

    try:
        page_infos = analyze_combined_pdf_pages(
            p,
            notify=notify
        )
        segments = segment_combined_pdf_pages(
            page_infos
        )
    except Exception as exc:
        if notify:
            notify(
                f"Combined-scan analysis skipped: {exc}"
            )
        return None

    if len(segments) <= 1:
        if notify:
            notify(
                "Multi-page scan appears to be one document; keeping all pages together."
            )
        return None

    incoming = Path(
        CFG["incoming"]
    )
    incoming.mkdir(
        parents=True,
        exist_ok=True
    )

    staging = (
        Path(CFG["review"])
        / ".SmartScan Split Staging"
    )
    staging.mkdir(
        parents=True,
        exist_ok=True
    )

    created_staging = []
    final_parts = []

    try:
        source_doc = fitz.open(p)

        try:
            for part_number, segment in enumerate(
                segments,
                1
            ):
                page_numbers = [
                    int(info["page"])
                    for info in segment["pages"]
                ]

                first_page = min(page_numbers)
                last_page = max(page_numbers)

                part_doc = fitz.open()

                try:
                    part_doc.insert_pdf(
                        source_doc,
                        from_page=first_page,
                        to_page=last_page
                    )

                    staged = unique_path(
                        staging
                        / (
                            f"{p.stem}__SmartScanPart__"
                            f"{part_number:02d}.pdf"
                        )
                    )

                    part_doc.save(
                        staged,
                        garbage=4,
                        deflate=True
                    )

                finally:
                    part_doc.close()

                created_staging.append(
                    staged
                )

        finally:
            source_doc.close()

        # All parts exist before touching the original.
        for staged in created_staging:
            final_path = unique_path(
                incoming
                / staged.name
            )

            shutil.move(
                str(staged),
                str(final_path)
            )

            final_parts.append(
                final_path
            )

        # Warm OCR cache with the per-page text already read above.
        for part_path, segment in zip(
            final_parts,
            segments
        ):
            combined_text = "\n".join(
                info.get("text", "")
                for info in segment["pages"]
            )

            _OCR_CACHE[
                file_cache_key(part_path)
            ] = combined_text

        if CFG.get(
            "keep_split_originals",
            True
        ):
            backup = _split_original_backup_path(
                p
            )

            shutil.move(
                str(p),
                str(backup)
            )

            if notify:
                notify(
                    f"Original combined scan backed up: {backup.name}"
                )
        else:
            p.unlink()

        if notify:
            summary = []

            for number, segment in enumerate(
                segments,
                1
            ):
                identity = segment["identity"]
                pages = [
                    info["page"] + 1
                    for info in segment["pages"]
                ]

                if len(pages) == 1:
                    page_text = f"page {pages[0]}"
                else:
                    page_text = (
                        f"pages {pages[0]}-{pages[-1]}"
                    )

                summary.append(
                    f"Part {number}: {identity.get('category')} → "
                    f"{identity.get('family')} ({page_text})"
                )

            notify(
                f"Separated combined scan into {len(final_parts)} documents."
            )

            for item in summary:
                notify(item)

        return final_parts

    except Exception as exc:
        # Clean up partial products. Leave original untouched whenever possible.
        for staged in created_staging:
            try:
                if staged.exists():
                    staged.unlink()
            except Exception:
                pass

        for part in final_parts:
            try:
                if part.exists():
                    part.unlink()
            except Exception:
                pass

        if notify:
            notify(
                f"Could not safely separate combined scan; original kept intact: {exc}"
            )

        return []



# ============================================================================
# Preview
# ============================================================================

def document_page_count(path):
    p = Path(path)

    try:
        if p.suffix.lower() == ".pdf":
            doc = fitz.open(p)
            try:
                return max(1, len(doc))
            finally:
                doc.close()

        return 1
    except Exception:
        return 1


def render_document_preview(path, page_index=0, max_width=760, max_height=390):
    img = _load_page_image(
        path,
        page_index,
        scale=1.35
    )

    if img is None:
        return None

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    img.thumbnail(
        (max_width, max_height),
        resample
    )

    return img


# ============================================================================
# Engine
# ============================================================================

class SmartScanEngine:


    def __init__(self, notify, teach_callback, progress_callback=None):
        self.notify = notify
        self.teach_callback = teach_callback
        self.progress_callback = progress_callback

        self.running = False
        self.paused = set()
        self.processing = set()
        self.seen = {}

        self._watcher_thread = None
        self._watch_generation = 0
        self._watcher_heartbeat = 0.0
        self._scan_lock = threading.Lock()

        # v52: one processing worker, one document at a time.
        self._process_queue = []
        self._process_queue_keys = set()
        self._process_queue_lock = threading.Lock()
        self._process_worker_thread = None
        self._process_worker_stop = False
        self._teach_blocked = False
        self._active_processing_path = None
        self.last_recognition = {}
        self.last_metadata_by_path = {}

    def progress(self, stage, path=None, **data):
        callback = self.progress_callback
        if callback is None:
            return
        try:
            callback(stage, str(path) if path else "", data)
        except Exception:
            pass

    def key(self, path):
        try:
            return str(Path(path).resolve()).lower()
        except Exception:
            return str(Path(path)).lower()


    def _watcher_is_alive(self):
        return bool(
            self._watcher_thread
            and self._watcher_thread.is_alive()
        )

    def _ensure_watcher(self):
        """Start or recover the live watcher if it is not actually alive."""
        if self._watcher_is_alive():
            return False

        self._watch_generation += 1
        generation = self._watch_generation

        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            args=(generation,),
            daemon=True,
            name=f"SmartScanWatcher-{generation}"
        )
        self._watcher_thread.start()
        return True


    def _queue_status(self):
        with self._process_queue_lock:
            waiting = len(self._process_queue)

        if self._active_processing_path:
            return (
                f"processing {Path(self._active_processing_path).name} "
                f"• {waiting} waiting"
            )

        if self._teach_blocked:
            return f"paused for Teach/Review • {waiting} waiting"

        return f"{waiting} waiting"

    def _processing_is_idle(self):
        with self._process_queue_lock:
            waiting = len(
                self._process_queue
            )

        return (
            not self._teach_blocked
            and self._active_processing_path is None
            and not self.processing
            and waiting == 0
        )

    def _next_incoming_file(self):
        """Return one oldest eligible Incoming file without reading its contents."""
        incoming = Path(
            CFG["incoming"]
        )
        incoming.mkdir(
            parents=True,
            exist_ok=True
        )

        candidates = []

        for path in incoming.iterdir():
            if (
                not path.is_file()
                or path.suffix.lower() not in SUPPORTED
            ):
                continue

            key = self.key(path)

            if key in self.paused:
                continue

            if key in self.processing:
                continue

            if key in self._process_queue_keys:
                continue

            candidates.append(
                path
            )

        if not candidates:
            return None

        try:
            candidates.sort(
                key=lambda p: (
                    p.stat().st_mtime_ns,
                    p.name.lower()
                )
            )
        except Exception:
            candidates.sort(
                key=lambda p: p.name.lower()
            )

        return candidates[0]


    def _process_worker_is_alive(self):
        return bool(
            self._process_worker_thread
            and self._process_worker_thread.is_alive()
        )

    def _ensure_process_worker(self):
        if self._process_worker_is_alive():
            return False

        self._process_worker_stop = False
        self._process_worker_thread = threading.Thread(
            target=self._process_worker_loop,
            daemon=True,
            name="SmartScanSequentialProcessor"
        )
        self._process_worker_thread.start()
        return True

    def _enqueue_processing(
        self,
        path,
        force=False,
        learned_hint=None,
        front=False
    ):
        path = Path(path)

        if not path.exists():
            return False

        key = self.key(path)

        if key in self.paused and not force:
            return False

        with self._process_queue_lock:
            if key in self._process_queue_keys:
                return False

            if key in self.processing:
                return False

            if (
                self._active_processing_path
                and self.key(self._active_processing_path) == key
            ):
                return False

            item = {
                "path": str(path),
                "force": bool(force),
                "learned_hint": learned_hint,
            }

            if front:
                self._process_queue.insert(
                    0,
                    item
                )
            else:
                self._process_queue.append(
                    item
                )

            self._process_queue_keys.add(
                key
            )

        self.notify(
            f"Queued: {path.name} • {self._queue_status()}"
        )

        self._ensure_process_worker()
        return True

    def _enqueue_split_parts_next(self, paths):
        """v59: queue only ONE split part; leave the rest in Incoming."""
        paths = [
            Path(path)
            for path in paths
            if Path(path).exists()
        ]

        if not paths:
            return

        try:
            paths.sort(
                key=lambda p: p.name.lower()
            )
        except Exception:
            pass

        self._enqueue_processing(
            paths[0],
            front=True
        )

    def _process_worker_loop(self):
        self.notify(
            "Sequential processor online — SmartScan will handle one document at a time."
        )

        while not self._process_worker_stop:
            if self._teach_blocked:
                time.sleep(0.20)
                continue

            item = None

            with self._process_queue_lock:
                if self._process_queue:
                    item = self._process_queue.pop(0)
                    self._process_queue_keys.discard(
                        self.key(item["path"])
                    )

            if item is None:
                time.sleep(0.20)
                continue

            path = Path(item["path"])

            if not path.exists():
                continue

            key = self.key(path)

            if key in self.paused and not item.get("force"):
                continue

            self.processing.add(key)
            self._active_processing_path = str(path)

            self.notify(
                f"PROCESSING ONE: {path.name}"
            )

            try:
                if not self._wait_until_stable(path):
                    if path.exists():
                        self.notify(
                            f"Still being written; watcher will retry later: {path.name}"
                        )
                    continue

                if not path.exists():
                    continue

                self.process(
                    path,
                    force=item.get("force", False),
                    learned_hint=item.get("learned_hint")
                )

            except Exception as exc:
                self.notify(
                    f"Processing error: {path.name}: {exc}"
                )

            finally:
                self.processing.discard(key)
                self._active_processing_path = None

            self.notify(
                f"Document finished • {self._queue_status()}"
            )

            # Do not preload the whole Incoming directory. Ask for only one
            # additional file after the current document is completely done.
            if (
                self.running
                and not self._teach_blocked
            ):
                self.scan_incoming()

        self.notify("Sequential processor stopped.")

    def set_teach_blocked(self, blocked):
        was_blocked = self._teach_blocked
        self._teach_blocked = bool(blocked)

        if self._teach_blocked and not was_blocked:
            self.notify(
                "Processing queue paused for Teach/Review."
            )
        elif not self._teach_blocked and was_blocked:
            self.notify(
                "Teach/Review finished — processing next queued document."
            )

        self._ensure_process_worker()


    def start(self):
        already_running = self.running
        watcher_alive = self._watcher_is_alive()

        self.running = True
        self._process_worker_stop = False
        self.seen.clear()

        watcher_recovered = self._ensure_watcher()
        processor_started = self._ensure_process_worker()

        if not already_running:
            self.notify("Automatic sorting started.")
        elif watcher_recovered or not watcher_alive:
            self.notify("Automatic sorting watcher recovered.")
        else:
            self.notify("Automatic sorting is already running.")

        if processor_started:
            self.notify(
                "One-document-at-a-time processor started."
            )

        self.notify(
            "Live watcher online in TRUE single-file mode: one Incoming file is selected only after the previous file finishes."
        )

        threading.Thread(
            target=self.scan_incoming,
            daemon=True,
            name="SmartScanInitialScan"
        ).start()



    def stop(self):
        self.running = False
        self.seen.clear()
        self._watch_generation += 1
        self._process_worker_stop = True

        with self._process_queue_lock:
            self._process_queue.clear()
            self._process_queue_keys.clear()

        self.notify(
            "Automatic sorting stopped. Waiting processing queue cleared."
        )




    def _watch_loop(self, generation):
        self.notify(
            "Watcher thread started in TRUE single-file mode."
        )

        while (
            self.running
            and generation == self._watch_generation
        ):
            self._watcher_heartbeat = time.time()

            try:
                # Only look for another file when the processor is genuinely
                # idle. Nothing else is added to the queue ahead of time.
                if self._processing_is_idle():
                    self.scan_incoming()

            except Exception as exc:
                self.notify(
                    f"Watcher warning: {exc}"
                )

            time.sleep(
                max(
                    0.60,
                    float(
                        CFG.get(
                            "poll_seconds",
                            2.0
                        )
                    )
                )
            )

        if generation == self._watch_generation:
            self.notify(
                "Watcher thread stopped."
            )




    def scan_incoming(self):
        """Queue only the next eligible file.

        This method never OCRs or preloads all Incoming files. It inspects only
        directory metadata, chooses one candidate, and returns.
        """
        if not self._scan_lock.acquire(
            blocking=False
        ):
            return

        try:
            if (
                CFG.get(
                    "incoming_one_at_a_time",
                    True
                )
                and not self._processing_is_idle()
            ):
                return

            next_path = self._next_incoming_file()

            if next_path is None:
                return

            self.notify(
                f"NEXT INCOMING FILE: {next_path.name}"
            )

            self._enqueue_processing(
                next_path
            )

        finally:
            self._scan_lock.release()


    def _wait_until_stable(self, path, timeout=35.0, interval=0.55, stable_checks=3):
        """Wait until a scanner/copier has finished writing the file."""
        path = Path(path)
        deadline = time.time() + timeout
        previous = None
        stable = 0

        while time.time() < deadline:
            if not path.exists():
                return False

            try:
                stat = path.stat()
                current = (
                    stat.st_size,
                    stat.st_mtime_ns
                )
            except Exception:
                time.sleep(interval)
                continue

            # Empty/zero-byte scan is not ready.
            if current[0] <= 0:
                stable = 0
                previous = current
                time.sleep(interval)
                continue

            if current == previous:
                stable += 1
            else:
                stable = 0
                previous = current

            if stable >= stable_checks:
                return True

            time.sleep(interval)

        return False


    def process_async(self, path, force=False, learned_hint=None):
        self._enqueue_processing(
            path,
            force=force,
            learned_hint=learned_hint
        )


    def _pause_for_teaching(self, path, reason, category="", family=""):
        self.progress(
            "review",
            path,
            category=category,
            family=family,
            reason=reason,
            signal="yellow"
        )
        key = self.key(path)
        self.paused.add(key)
        self._teach_blocked = True

        self.notify(
            f"Paused for teaching: {path.name}"
        )
        record_stat("needs_review", 1)

        self.teach_callback(
            str(path),
            reason,
            category,
            family
        )

    def _move_duplicate(
        self,
        path,
        duplicate,
        review_name=None
    ):
        kind, existing, score, details = duplicate

        destination = Path(CFG["duplicates"])
        destination.mkdir(
            parents=True,
            exist_ok=True
        )

        target_name = (
            review_name
            or path.name
        )

        target = unique_path(
            destination
            / target_name
        )

        shutil.move(
            str(path),
            str(target)
        )

        self.progress(
            "duplicate_found",
            target,
            duplicate_of=str(existing),
            score=float(score),
            details=details,
            signal="yellow"
        )
        self.notify(
            f"Duplicate moved to review: {target.name}"
        )
        self.notify(
            f"Matches {existing.name} • {kind} {score:.0%} • {details}"
        )
        record_stat("duplicates", 1)



    def _file_known_document(
        self,
        path,
        text,
        category,
        family,
        manual_date=None,
        recognition_confidence=0,
        recognition_source="",
        recognition_profile_name="",
        metadata=None,
        ai_date=None
    ):
        metadata = dict(metadata or {})
        railroad = _raily_clean_entity(metadata.get("railroad", ""), "railroad")
        location = _raily_clean_entity(metadata.get("location", ""), "location")
        person = _raily_clean_entity(metadata.get("person", ""), "person")

        # Exact byte-for-byte duplicates are safe to remove from the normal
        # flow immediately, even before date OCR/manual review.
        if CFG.get("detect_duplicates", True):
            exact_duplicate = find_exact_duplicate(
                path
            )

            if exact_duplicate:
                self._move_duplicate(
                    path,
                    exact_duplicate
                )
                return "duplicate", None

        learn_family_date_label(
            category,
            family,
            text
        )

        detected_date = None
        date_source = ""

        if manual_date:
            detected_date = manual_date
            date_source = "manual"

            save_manual_document_date(
                path,
                manual_date,
                category,
                family
            )

            self.notify(
                f"Manual date saved for this exact document: "
                f"{manual_date:%Y-%m-%d}."
            )

        else:
            remembered_date, remembered_entry = get_saved_manual_date(
                path
            )

            if remembered_date:
                detected_date = remembered_date
                date_source = "remembered-manual"

                self.notify(
                    f"Remembered manual date: "
                    f"{remembered_date:%Y-%m-%d} "
                    "(exact document fingerprint)."
                )

        if detected_date is None and ai_date:
            candidate_ai_date = ai_date if isinstance(ai_date, datetime) else parse_date(str(ai_date))
            if candidate_ai_date:
                detected_date = candidate_ai_date
                date_source = "RAILY full-page vision AI"
                self.notify(
                    f"RAILY document date: {candidate_ai_date:%Y-%m-%d} from full-page vision AI."
                )

        if (
            detected_date is None
            and CFG.get(
                "add_date_to_filename",
                True
            )
        ):
            if CFG.get(
                "fast_post_recognition",
                True
            ):
                self.notify(
                    "Fast date check..."
                )

                (
                    dt,
                    date_conf,
                    date_label,
                    detected_source
                ) = detect_family_date_fast(
                    category,
                    family,
                    text,
                    path=path
                )
            else:
                (
                    dt,
                    date_conf,
                    date_label,
                    detected_source
                ) = detect_family_date(
                    category,
                    family,
                    text,
                    path=path
                )

            required_conf = int(
                CFG.get(
                    "date_confidence_threshold",
                    92
                )
            )

            if (
                dt
                and date_conf >= required_conf
            ):
                detected_date = dt
                date_source = detected_source or date_label or "detected"

                self.notify(
                    f"Safe date: {dt:%Y-%m-%d} "
                    f"from '{date_label}' "
                    f"({date_conf}%, {detected_source})"
                )

            else:
                # v69 fallback #1: use a date in an intentional filename only.
                # Generic scanner names are rejected by detect_reliable_filename_date.
                filename_date = detect_reliable_filename_date(path)
                if filename_date:
                    detected_date = filename_date
                    date_source = "reliable filename"
                    self.notify(
                        f"Document date fallback: {filename_date:%Y-%m-%d} "
                        "from a reliable filename."
                    )

                # A learned date-required family must never silently substitute
                # the scan/Windows date for an unreadable printed document date.
                elif (
                    CFG.get("pause_when_required_date_missing", True)
                    and family_requires_date(category, family)
                ):
                    self.notify(
                        "Quick date check could not confirm the required printed "
                        "document date — sending to Teach/Review. The scan date "
                        "will NOT be substituted for this learned form."
                    )
                    return ("needs_date", None)

                # v69 fallback #2: only for families that do not require a
                # printed date, use file creation/scan date as the final fallback.
                else:
                    fallback_date = scan_date_fallback(path)
                    if fallback_date:
                        detected_date = fallback_date
                        date_source = "scan/file date fallback"
                        self.notify(
                            f"No printed document date was confirmed; using "
                            f"{fallback_date:%Y-%m-%d} as the final scan/file-date fallback."
                        )
                    else:
                        self.notify(
                            "No printed document date or safe fallback date was found; "
                            "filing without a date."
                        )

        self.progress(
            "date",
            path,
            date=_date_string(detected_date),
            date_source=date_source,
            railroad=railroad,
            location=location,
            person=person
        )

        # Fast date-aware duplicate pass. Text/date filtering happens first;
        # expensive visual hashing is lazy and only runs for close candidates.
        if CFG.get("detect_duplicates", True):
            self.progress("duplicate", path, signal="yellow")
            self.notify(
                "Fast duplicate check..."
            )

            duplicate = find_duplicate(
                path,
                text,
                category,
                family,
                document_date=detected_date,
                skip_exact=True
            )

            if duplicate:
                self._move_duplicate(
                    path,
                    duplicate
                )
                return (
                    "duplicate",
                    None
                )

        if CFG.get("raily_route_filing", True) and CFG.get("raily_require_railroad_location", True):
            missing = []
            if not railroad:
                missing.append("Railroad")
            if not location:
                missing.append("Location")
            if missing:
                self.notify(
                    "RAILY route review required — missing " + " and ".join(missing) + "."
                )
                self.progress(
                    "review",
                    path,
                    reason="Missing " + " and ".join(missing),
                    railroad=railroad,
                    location=location,
                    person=person,
                    signal="yellow"
                )
                return ("needs_route", None)

        if railroad:
            remember_raily_entity("railroads", railroad)
        if location:
            remember_raily_entity("locations", location)
        if person:
            remember_raily_entity("people", person)

        destination = build_destination(
            category,
            family,
            railroad=railroad,
            location=location
        )

        self.progress(
            "filing",
            path,
            railroad=railroad,
            location=location,
            person=person,
            category=category,
            family=family,
            destination=str(destination),
            signal="yellow"
        )

        destination.mkdir(
            parents=True,
            exist_ok=True
        )

        intended_name = build_filename(
            path.name,
            category,
            family,
            text,
            path=path,
            manual_date=manual_date,
            detected_date=detected_date,
            person=person
        )

        intended_target = (
            destination
            / intended_name
        )

        # v62: A same family/date filename collision is itself a manual-review
        # duplicate condition. Do NOT silently create _2, _3, etc. in Sorted.
        if (
            CFG.get(
                "detect_duplicates",
                True
            )
            and intended_target.exists()
        ):
            self.notify(
                "Dated filename collision detected — sending the new copy "
                "to Duplicates Review instead of creating a numbered file."
            )

            collision_duplicate = (
                "same-name/date",
                intended_target,
                1.0,
                (
                    f"same destination filename {intended_name}; "
                    f"same category/document type/date"
                )
            )

            self._move_duplicate(
                path,
                collision_duplicate,
                review_name=intended_name
            )

            return (
                "duplicate",
                None
            )

        # Non-dated or otherwise distinct filename collisions still retain the
        # old safety behavior so SmartScan never overwrites a real document.
        target = unique_path(
            intended_target
        )

        original_source_path = str(path)

        shutil.move(
            str(path),
            str(target)
        )

        record_filing_history(
            original_source_path,
            target,
            category,
            family,
            detected_date,
            railroad=railroad,
            location=location,
            person=person,
            recognition_source=recognition_source
        )

        update_saved_manual_date_path(
            target
        )

        index_document(
            target,
            text,
            category,
            family,
            document_date=detected_date,
            date_source=date_source,
            railroad=railroad,
            location=location,
            person=person
        )

        record_family_duplicate_history(
            target,
            text,
            category,
            family,
            document_date=detected_date
        )

        record_recognition_success(
            category,
            family,
            confidence=recognition_confidence,
            source=recognition_source,
            profile_name=recognition_profile_name
        )
        record_stat(
            "filed",
            1,
            recognition={
                "category": category,
                "family": family,
                "confidence": int(recognition_confidence or 0),
                "source": str(recognition_source or ""),
                "filename": target.name,
            }
        )

        route_text = (
            f"{railroad} › {location}"
            if railroad and location
            else f"{category} › {family}"
        )
        self.notify(
            f"Filed successfully: {route_text} › {target.name}"
        )
        self.progress(
            "complete",
            target,
            railroad=railroad,
            location=location,
            person=person,
            category=category,
            family=family,
            date=_date_string(detected_date),
            destination=str(target),
            signal="green"
        )

        return (
            "filed",
            target
        )


    def process(self, path, force=False, learned_hint=None):
        path = Path(path)
        if not path.exists():
            return

        key = self.key(path)
        self.progress("received", path, filename=path.name, signal="yellow")

        # FAST STACK: scanner bundles are separated before RAILY analyzes pages.
        if (
            not force
            and CFG.get("auto_split_combined_scans", True)
            and path.suffix.lower() == ".pdf"
            and not is_split_part_path(path)
        ):
            self.progress("splitting", path, signal="yellow")
            if CFG.get("fast_stack_mode", True):
                split_parts = fast_split_pdf_pages(path, notify=self.notify)
            else:
                split_parts = split_combined_pdf(path, notify=self.notify)

            if split_parts:
                self._enqueue_processing(split_parts[0], front=True)
                self.notify(
                    f"Split complete. Processing only {split_parts[0].name} next; "
                    f"{max(0, len(split_parts) - 1)} split page(s) remain in Incoming."
                )
                return

        # ------------------------------------------------------------------
        # RAILY full-page analysis comes FIRST. OCR is now a supporting signal,
        # not the only way the application recognizes a learned document.
        # ------------------------------------------------------------------
        self.notify(f"RAILY full-page analysis: {path.name}")
        self.progress("analyzing", path, signal="yellow")

        ai_result = None
        if str(CFG.get("raily_ai_provider", "Local Full-Page")).casefold() != "local full-page":
            self.notify("RAILY is asking the configured full-page vision model...")
            ai_result = raily_external_vision_analysis(path)
            if ai_result:
                self.notify(
                    f"RAILY vision AI returned {int(ai_result.get('confidence', 0) or 0)}% confidence."
                )

        visual_match = raily_match_visual_document(path)
        if visual_match:
            self.notify(
                f"RAILY visual memory match: {visual_match.get('family', 'Document')} "
                f"({visual_match.get('score', 0):.0f}%)."
            )

        self.progress(
            "reading",
            path,
            visual_score=int(round((visual_match or {}).get("score", 0))),
            signal="yellow"
        )
        self.notify(f"Reading document text/metadata: {path.name}")
        text = get_cached_ocr(path)

        ocr_category, ocr_category_conf, _ = classify(
            text,
            classification_filename_for(path)
        )
        ocr_family, ocr_family_conf, ocr_family_source = resolve_family(
            ocr_category,
            text,
            path
        )

        category = ocr_category
        category_conf = int(ocr_category_conf)
        family = ocr_family
        family_conf = int(ocr_family_conf)
        family_source = ocr_family_source

        # A configured vision model can understand a brand-new whole page.
        if ai_result:
            ai_conf = int(ai_result.get("confidence", 0) or 0)
            ai_category = clean_optional_name(ai_result.get("category", ""))
            ai_family = clean_optional_name(ai_result.get("document_type", ""))

            if ai_conf >= 88 and ai_family:
                if ai_category in CATEGORY_RULES:
                    category = ai_category
                    category_conf = max(category_conf if category == ocr_category else 0, ai_conf)
                else:
                    category = ocr_category
                    category_conf = max(category_conf, min(ai_conf, 96))
                family = ai_family
                family_conf = ai_conf
                family_source = "RAILY full-page vision AI"

        # Local visual memory recognizes already-taught documents from the
        # complete page layout even when OCR words are damaged or run together.
        if visual_match:
            visual_score = int(round(float(visual_match.get("score", 0))))
            visual_category = clean_optional_name(visual_match.get("category", ""))
            visual_family = clean_optional_name(visual_match.get("family", ""))
            auto_visual = int(CFG.get("raily_visual_autofile_threshold", 90))

            if visual_score >= auto_visual and visual_category and visual_family:
                category = visual_category
                family = visual_family
                category_conf = max(category_conf if ocr_category == visual_category else 0, visual_score)
                family_conf = max(family_conf if family == ocr_family else 0, visual_score)
                family_source = "RAILY full-page visual memory"
            elif visual_category == ocr_category and visual_family and visual_score > family_conf:
                family = visual_family
                family_conf = visual_score
                family_source = "RAILY visual + OCR confirmation"

        metadata = raily_extract_metadata(
            text,
            visual_match=visual_match,
            ai_result=ai_result
        )
        self.last_metadata_by_path[key] = dict(metadata)

        ai_date = None
        if ai_result and ai_result.get("date"):
            ai_date = parse_date(str(ai_result.get("date")))

        self.progress(
            "identified",
            path,
            category=category,
            family=family,
            confidence=int(family_conf),
            railroad=metadata.get("railroad", ""),
            location=metadata.get("location", ""),
            person=metadata.get("person", ""),
            date=_date_string(ai_date),
            source=family_source,
            signal="yellow"
        )
        self.progress(
            "route",
            path,
            railroad=metadata.get("railroad", ""),
            location=metadata.get("location", ""),
            person=metadata.get("person", ""),
            signal="yellow"
        )

        self.notify(
            f"RAILY recognized: {category} ({category_conf}%) • "
            f"{family} ({family_conf}%, {family_source})"
        )
        if metadata.get("railroad") or metadata.get("location") or metadata.get("person"):
            self.notify(
                "RAILY metadata: "
                f"Railroad={metadata.get('railroad') or '—'} • "
                f"Location={metadata.get('location') or '—'} • "
                f"Person={metadata.get('person') or '—'}"
            )

        matched_family, matched_score, matched_source, matched_profile = match_learned_family(
            category, text, path
        )
        if visual_match and clean_name(visual_match.get("family", "")).casefold() == clean_name(family).casefold():
            matched_profile = visual_match.get("profile") or matched_profile

        if matched_profile and clean_name(matched_family).casefold() == clean_name(family).casefold():
            breakdown = family_match_breakdown(matched_profile, text, path)
        elif matched_profile and clean_name(matched_profile.get("family", "")).casefold() == clean_name(family).casefold():
            breakdown = family_match_breakdown(matched_profile, text, path)
        else:
            breakdown = {
                "family": family,
                "learned_profile": "",
                "parent_family": "",
                "variant": "",
                "ocr": 0,
                "structure": 0,
                "identity": 0,
                "correction_bonus": 0,
                "growth_bonus": 0,
                "dominant": family_source,
                "total": int(family_conf),
                "examples": 0,
                "successes": 0,
                "streak": 0,
                "last_seen": "",
            }
        breakdown["raily_visual"] = int(round((visual_match or {}).get("score", 0)))

        self.last_recognition = {
            "filename": path.name,
            "category": category,
            "category_confidence": int(category_conf),
            "family": family,
            "family_confidence": int(family_conf),
            "family_source": family_source,
            "railroad": metadata.get("railroad", ""),
            "location": metadata.get("location", ""),
            "person": metadata.get("person", ""),
            "breakdown": breakdown,
        }

        if learned_hint:
            learned_category = learned_hint.get("category", "")
            learned_family = learned_hint.get("family", "")
            if category == learned_category:
                _, score, _, profile = match_learned_family(category, text, path)
                if (
                    profile
                    and learned_family.lower() in {
                        clean_name(profile.get("family", "")).lower(),
                        clean_optional_name(profile.get("parent_family", "")).lower(),
                    }
                    and score >= int(CFG.get("queue_family_threshold", 58))
                ):
                    family = learned_family
                    family_conf = score
                    family_source = "newly learned queue match"
                    self.notify(f"Queue match confirmed: {family} ({score}%)")

        if not force and category_conf < int(CFG.get("category_threshold", 64)):
            self._pause_for_teaching(
                path,
                f"Category confidence is only {category_conf}%.",
                category,
                family
            )
            return

        is_generic_fallback = (
            family_source in {"category fallback", "category-defined family"}
            and family_conf < int(CFG.get("auto_family_threshold", 62))
        )
        if (
            not force
            and CFG.get("ask_to_teach_unknown", True)
            and is_generic_fallback
        ):
            self._pause_for_teaching(
                path,
                f"Document type is not learned confidently yet ({family_conf}%).",
                category,
                family
            )
            return

        status, _ = self._file_known_document(
            path,
            text,
            category,
            family,
            recognition_confidence=family_conf,
            recognition_source=family_source,
            recognition_profile_name=(matched_profile or {}).get("family", ""),
            metadata=metadata,
            ai_date=ai_date
        )

        if status == "needs_date":
            self._pause_for_teaching(
                path,
                (
                    "Document type is recognized, but this learned dated form "
                    "requires a date and RAILY could not read it confidently. "
                    "Review the date or enter it manually."
                ),
                category,
                family
            )
            return

        if status == "needs_route":
            missing = []
            if not metadata.get("railroad"):
                missing.append("Railroad")
            if not metadata.get("location"):
                missing.append("Location")
            self._pause_for_teaching(
                path,
                "RAILY needs you to confirm " + " and ".join(missing) + " before filing.",
                category,
                family
            )
            return

        if status in {"filed", "duplicate"}:
            self.paused.discard(key)

    def unpause(self, path):
        key = self.key(path)
        self.paused.discard(key)
        self.seen.pop(key, None)


    def recheck_after_learning(self, category, family, paths):
        candidates = []
        for raw in paths:
            p = Path(raw)

            # Manually split pieces were explicitly created for individual
            # review. They must each get their own Teach window.
            if is_manual_split_part(p):
                continue

            if p.exists() and self.key(p) in self.paused:
                candidates.append(p)

        if not candidates:
            self.notify(f"Learning applied: no other paused {family} candidates.")
            return

        self.notify(
            f"Learning applied: re-checking {len(candidates)} paused document(s) for {family}."
        )

        matched = 0
        retained = 0
        date_reviews = 0

        for path in candidates:
            try:
                # RAILY checks the entire page first so queued sibling forms do
                # not have to depend on OCR spelling/spacing to benefit from one-teach-many.
                visual_match = raily_match_visual_document(path)
                visual_same = bool(
                    visual_match
                    and clean_optional_name(visual_match.get("category", "")).casefold() == clean_optional_name(category).casefold()
                    and clean_optional_name(visual_match.get("family", "")).casefold() == clean_optional_name(family).casefold()
                    and float(visual_match.get("score", 0)) >= float(CFG.get("raily_visual_match_threshold", 80))
                )

                text = get_cached_ocr(path)
                learned_score = 0
                same_family = visual_same

                if not visual_same:
                    guessed_category, _, _ = classify(
                        text,
                        classification_filename_for(path)
                    )

                    if guessed_category != category:
                        retained += 1
                        continue

                    learned_family, learned_score, _, profile = match_learned_family(
                        category, text, path
                    )
                    same_family = bool(
                        profile
                        and family.lower() in {
                            clean_name(profile.get("family", "")).lower(),
                            clean_optional_name(profile.get("parent_family", "")).lower(),
                            clean_name(learned_family).lower(),
                        }
                    )

                    if not (
                        same_family
                        and learned_score >= int(CFG.get("queue_family_threshold", 58))
                    ):
                        retained += 1
                        continue

                metadata = raily_extract_metadata(text, visual_match=visual_match)
                status, _ = self._file_known_document(
                    path, text, category, family, metadata=metadata
                )

                if status in {"filed", "duplicate"}:
                    self.unpause(path)
                    matched += 1

                elif status in {"needs_date", "needs_route"}:
                    date_reviews += 1
                    retained += 1
                    if status == "needs_route":
                        reason = (
                            "Document type matched automatically, but RAILY needs the "
                            "Railroad and/or Location confirmed before filing."
                        )
                    else:
                        reason = (
                            "Document type matched automatically, but this learned "
                            "dated form's date could not be read. Review the date "
                            "area or enter this document's date manually."
                        )
                    self.teach_callback(
                        str(path),
                        reason,
                        category,
                        family
                    )

            except Exception as exc:
                retained += 1
                self.notify(f"Queue re-check skipped {path.name}: {exc}")

        self.notify(
            f"Batch learning result: {matched} handled automatically; "
            f"{date_reviews} need date review; {retained} remain queued/reviewed."
        )


# ============================================================================
# UI
# ============================================================================

def _learning_work_area(win):
    """Usable monitor rectangle in Tk's Windows coordinate space, sans taskbar."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MonitorInfo(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            owner = win.master.winfo_toplevel() if win.master else win
            monitor = user32.MonitorFromWindow(owner.winfo_id(), 2)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
    return 0, 0, win.winfo_screenwidth(), max(1, win.winfo_screenheight() - 80)


def _fit_learning_window(win, preferred_width=1180, preferred_height=760):
    left, top, right, bottom = _learning_work_area(win)
    # Reserve decorations as well as the taskbar; account for display scaling.
    scale = max(1.0, float(win.tk.call("tk", "scaling")) / (96 / 72))
    border, caption = int(16 * scale), int(48 * scale)
    max_width = max(1, right - left - border - 16)
    max_height = max(1, bottom - top - caption - 16)
    width, height = min(preferred_width, max_width), min(preferred_height, max_height)
    win.geometry(f"{width}x{height}+{left + 8}+{top + 8}")
    win.maxsize(max_width, max_height)
    win.minsize(min(640, width), min(360, height))
    win._learning_compact = bottom - top < 850 or width < 1100
    return width, height


def _learning_form(win):
    """One width-constrained scrolling form and a separately packed action bar."""
    actions = ttk.Frame(win)
    actions.pack(side="bottom", fill="x")
    viewport = ttk.Frame(win)
    viewport.pack(side="top", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(viewport, orient="vertical")
    scrollbar.pack(side="right", fill="y")
    canvas = tk.Canvas(viewport, highlightthickness=0, width=1, height=1,
                       yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.configure(command=canvas.yview)
    content = ttk.Frame(canvas)
    content.columnconfigure(0, weight=1)
    item = canvas.create_window(0, 0, window=content, anchor="nw")
    content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=max(1, e.width)))

    # A toplevel bindtag covers its descendants without global bind_all handlers.
    # It is inserted before widget class bindings so a combobox cannot consume
    # wheel events by silently changing the selected document type.
    wheel_tag = "RAILYFormWheel" + str(id(canvas))

    def wheel(event):
        target = win.winfo_containing(event.x_root, event.y_root)
        if target is None or not str(target).startswith(str(content) + ".") and target is not content:
            return
        delta = getattr(event, "delta", 0)
        units = (-max(1, abs(int(delta)) // 120) if delta > 0 else max(1, abs(int(delta)) // 120)) if delta else (-1 if event.num == 4 else 1)
        canvas.yview_scroll(units, "units")
        return "break"

    bindings = {}
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        bindings[sequence] = win.bind_class(wheel_tag, sequence, wheel)

    def cleanup(event):
        if event.widget is win:
            for sequence, command in bindings.items():
                win.unbind_class(wheel_tag, sequence)
                win._root().deletecommand(command)

    win.bind("<Destroy>", cleanup, add="+")
    content._learning_canvas = canvas
    content._learning_wheel_tag = wheel_tag
    return content, actions


def _finish_learning_form(win, content):
    """Apply compact spacing and width-aware wrapping after controls exist."""
    compact = getattr(win, "_learning_compact", False)

    def visit(widget):
        tags = list(widget.bindtags())
        if content._learning_wheel_tag not in tags:
            tags.insert(1, content._learning_wheel_tag)
            widget.bindtags(tuple(tags))
        if isinstance(widget, (tk.Label, ttk.Label, ttk.Checkbutton)):
            def wrap(event=None, target=widget):
                try:
                    if "wraplength" in target.keys():
                        available = max(40, target.master.winfo_width() - target.winfo_x() - 16)
                        target.configure(wraplength=available)
                except tk.TclError:
                    pass
            widget.master.bind("<Configure>", wrap, add="+")
            widget.after_idle(wrap)
        if compact:
            if isinstance(widget, tk.Frame):
                widget.configure(padx=min(6, int(widget.cget("padx"))),
                                 pady=min(4, int(widget.cget("pady"))))
            elif isinstance(widget, ttk.LabelFrame):
                widget.configure(padding=(6, 4))
            if isinstance(widget, tk.Label):
                try:
                    import tkinter.font as tkfont
                    font = tkfont.Font(font=widget.cget("font"))
                    if abs(font.actual("size")) > 11:
                        font.configure(size=11)
                        widget.configure(font=font)
                        widget._compact_font = font
                except tk.TclError:
                    pass
            if widget.winfo_manager() == "grid":
                widget.grid_configure(pady=2)
            elif widget.winfo_manager() == "pack":
                widget.pack_configure(pady=2)
            for row in range(widget.grid_size()[1]):
                widget.grid_rowconfigure(row, minsize=0, pad=0)
        for child in widget.winfo_children():
            visit(child)

    visit(content)


def _flow_learning_actions(frame):
    """Keep all action buttons visible, wrapping to another row when needed."""
    children = list(frame.winfo_children())
    for child in children:
        child.pack_forget()
    last_width = [None]

    def layout(event=None):
        width = max(1, frame.winfo_width() - 24)
        if width == last_width[0]:
            return
        last_width[0] = width
        row = column = used = 0
        for child in children:
            needed = child.winfo_reqwidth() + 8
            if used and used + needed > width:
                row += 1
                column = used = 0
            child.grid(row=row, column=column, sticky="w", padx=3, pady=3)
            used += needed
            column += 1
    frame.bind("<Configure>", layout, add="+")
    frame.after_idle(layout)


def _learning_tree_scrollbars(tree):
    """Add visible vertical and horizontal navigation to existing manager tables."""
    parent = tree.master
    vertical = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.pack_forget()
    horizontal.pack(side="bottom", fill="x")
    vertical.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_DISPLAY_NAME} v{APP_VERSION}")

        sw = max(1024, root.winfo_screenwidth())
        sh = max(700, root.winfo_screenheight())

        self.compact_ui = (
            sw < 1450
            or sh < 850
        )

        width = min(
            1380,
            max(1040, sw - 50)
        )

        height = min(
            860,
            max(680, sh - 60)
        )

        x = max(
            0,
            (sw - width) // 2
        )

        y = max(
            0,
            (sh - height) // 3
        )

        root.geometry(
            f"{width}x{height}+{x}+{y}"
        )
        root.minsize(
            940,
            620
        )

        configure_tesseract()
        cleanup_index()
        release_backup = ensure_release_backup(APP_RELEASE)
        # v72: fill blank update channels after the existing release backup.
        if not str(CFG.get("raily_update_manifest_url", "") or "").strip():
            CFG["raily_update_manifest_url"] = RAILY_UPDATE_MANIFEST_URL
            save_config()
        initialize_v41_safe_dates()

        imported = migrate_clean_learning_once()

        self.teach_window_open = False
        self.current_teach_path = None
        self.tray_icon = None
        self._quitting = False

        self.session_sorted_count = 0
        self.session_duplicate_count = 0
        self.session_teach_count = 0
        self.session_backup_count = 0

        # Queue entries are dicts:
        # {"path": ..., "reason": ..., "category": ..., "family": ...}
        self.teach_queue = []
        self.teach_queue_keys = set()

        self._build_styles()

        for key in (
            "incoming",
            "sorted",
            "review",
            "duplicates"
        ):
            Path(CFG[key]).mkdir(
                parents=True,
                exist_ok=True
            )

        self.engine = SmartScanEngine(
            self.note,
            self.request_teach,
            self.raily_event
        )

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)
        self.setup_tray_icon()
        self.refresh_persistent_dashboard()
        self.root.after(900, self._mark_raily_update_health_if_requested)

        self.note(f"RAILY SmartScan v{APP_VERSION} — responsive Dispatch Center and visible Incoming source path are ready.")
        self.note(f"Active SmartScan data is organized under: {DATA_DIR}")
        if release_backup:
            self.note(f"Automatic pre-v{APP_RELEASE} rollback backup created: {release_backup.name}")
        self.note(
            "FAST post-recognition is enabled: saved dates and cheap date-label/zone checks run before any expensive handwriting OCR."
        )
        self.note(
            "Full handwriting date OCR is deferred to the already-responsive Teach window when the quick pass cannot confirm a required date."
        )
        self.note(
            "Duplicate comparison now delays page-image hashing until a same-family text/date candidate actually needs it."
        )
        self.note(
            "FAST STACK is enabled: multi-page scanner PDFs are separated into one-page PDFs before OCR."
        )
        self.note(
            "TRUE single-file Incoming mode is enabled: SmartScan selects only one Incoming file after the previous one finishes."
        )
        self.note(
            "v58 split-teach lock is active: manually separated pieces must open in Teach one-by-one."
        )
        self.note(
            "Split-generated filenames are ignored during classification so one source filename cannot bias every piece."
        )

        if imported:
            self.note(
                f"Imported {imported} learned document family/families from the prior clean-start build."
            )

        self.note(
            "One-teach-many is active: after a correction, queued sibling forms are re-checked automatically."
        )
        self.note(
            "RAILY route filing is active: Railroad → Location → documents. Person/type/date stay in filename and metadata."
        )
        self.note(
            "Spatial Date Learning is ON: a taught date area is checked before date-label fallback rules."
        )
        self.note(
            "v69 DATE PRIORITY: exact manual date -> taught date area -> learned/safe printed label -> reliable filename -> scan/file date fallback."
        )
        self.note(
            "v70 TEACH UI: Review/Teach and Date Profile windows maximize to the usable screen, reserve readable row heights, and prevent wrapped help/status text from colliding."
        )
        self.note(
            "Date-required learned forms never substitute the scan date when the printed date cannot be confirmed."
        )
        self.note(
            "Teach Date Area stores only the page location; each document's actual date is read separately."
        )
        self.note(
            "Manual Date Override is available in Teach: a typed date applies only to that one document."
        )
        self.note(
            "Visual Date Trainer opens a large zoomable document and gives an explicit Date Found / Date Not Read result."
        )
        self.note(
            "Date-area OCR is rotation-aware: 0°, 90°, 180°, and 270° are tested automatically."
        )
        self.note(
            "Multiple Date Areas are supported: Priority 1 is tried first, followed by each fallback location."
        )
        self.note(
            "Live watcher fix is active: Start and Sort Existing both guarantee continuous Incoming monitoring."
        )
        self.note(
            "Date Trainer OCR runs in the background so Windows should remain responsive."
        )
        self.note(
            "Handwritten-date mode is active: blue ink isolation and form-line cleanup are enabled."
        )
        self.note(
            "Once a family has a taught date area, unreadable dates pause for Teach/Review instead of filing undated."
        )
        self.note(
            "Date-area selection fix is active: new boxes become active immediately, and existing boxes can be clicked and deleted."
        )
        self.note(
            "v51 uses a separate date-profile database keyed by exact Category → Document Type."
        )
        self.note(
            "Old mixed date boxes are intentionally ignored; document-type learning is preserved."
        )
        self.note(
            "v52 sequential mode is active: one document is fully handled before the next begins, and Teach/Review pauses the queue."
        )
        self.note(
            "v53 stack separation is active: mixed multi-page PDFs are split into separate documents before filing."
        )
        self.note(
            "Original combined PDFs are preserved in Review\\Split Originals as a safety backup."
        )
        self.note(
            "v54 Fast Teach is active: learned date-area OCR loads in the background after the Teach window opens."
        )
        self.note(
            "v55 Forgiving Date Zones are active: each taught box is automatically tightened into several safe internal crops before OCR."
        )
        self.note(
            "v56 Persistent Manual Dates are active: manually entered dates are stored by exact document SHA-256 fingerprint."
        )
        self.note(
            "v57 Date-Aware Duplicate Guard is active: same family + same date + strong content similarity sends later copies to Duplicates Review."
        )
        self.note(
            "v56 Manual Split is available in Teach for multi-page PDFs that contain several documents."
        )
        self.note(
            "v61 Stability Pack is active: backups, managers, Undo Last Filing, Desktop Shortcut and Windows startup are available."
        )
        self.note(
            "v63 flexible date reader + v62 duplicate collision guard are active: multiple date formats are recognized, and same dated destination filenames go to Duplicates Review instead of _2/_3 in Sorted."
        )
        self.note(
            "v68 suite is active: family-aware historical duplicate detection, clean Data/Logs/Backups folders, self-managing New Document Type/New Category menus, Learning Manager, match explanations, confidence growth, preferred date labels, date-area recovery, variants, smarter splitting, Duplicate Review Center, persistent dashboard stats, tray support, EXE builder, and restore-from-backup."
        )

        self.note(
            "v71 RAILY Full-Page Visual Learning is active: the whole page is compared to taught documents before OCR classification."
        )
        self.note(
            "RAILY Conductor Review now captures Railroad, Location and primary Person in one simple review screen."
        )
        self.note(
            "RAILY Safe Update Engine is installed: updates are backed up, verified and applied only through a configured trusted manifest."
        )

        if CFG.get("auto_start_sorting_on_launch", True):
            self.root.after(650, self.start)

        # Check for updates after the UI has fully painted. An empty manifest
        # URL simply leaves the updater dormant until one is configured.
        if str(CFG.get("raily_update_mode", "Recommended")).casefold() != "manual":
            self.root.after(2200, lambda: self.check_for_updates(manual=False))

        if "--minimized" in sys.argv:
            if CFG.get("minimize_to_tray", False) and self.tray_icon is not None:
                self.root.after(900, self.root.withdraw)
            else:
                self.root.after(900, self.root.iconify)

    # ----------------------------------------------------------------------
    # Styles
    # ----------------------------------------------------------------------

    def _build_styles(self):
        style = ttk.Style(self.root)

        if "clam" in style.theme_names():
            try:
                style.theme_use("clam")
            except Exception:
                pass

        self.ui_colors = {
            "bg": "#DCE2E6",
            "surface": "#F4F6F7",
            "navy": "#182126",
            "navy2": "#28343A",
            "accent": "#D69E00",
            "accent_hover": "#B98500",
            "accent_soft": "#F3E8B5",
            "text": "#172026",
            "muted": "#5D6A70",
            "success": "#198754",
            "warning": "#B7791F",
            "error": "#B42318",
            "purple": "#6554C0",
            "cyan": "#147D92",
            "green_bright": "#198754",
            "orange": "#C77800",
            "pink": "#B83280",
            "violet": "#6B5DD3",
            "border": "#AEB8BD",
            "signal_yellow": "#F4C430",
            "rail_steel": "#69757B",
            "dispatch_bg": "#101619",
        }

        C = self.ui_colors

        self.root.configure(
            bg=C["bg"]
        )

        base = (
            9
            if self.compact_ui
            else 10
        )

        title = (
            17
            if self.compact_ui
            else 19
        )

        style.configure(
            ".",
            font=("Segoe UI", base),
            background=C["bg"],
            foreground=C["text"]
        )

        style.configure(
            "TFrame",
            background=C["bg"]
        )

        style.configure(
            "TLabel",
            background=C["bg"],
            foreground=C["text"]
        )

        style.configure(
            "Header.TFrame",
            background=C["navy"],
            padding=(16, 12)
        )

        style.configure(
            "Title.TLabel",
            background=C["navy"],
            foreground="white",
            font=("Segoe UI", title, "bold")
        )

        style.configure(
            "Subtitle.TLabel",
            background=C["navy"],
            foreground="#DCEAFF",
            font=("Segoe UI", base)
        )

        style.configure(
            "HeaderStatus.TLabel",
            background=C["navy"],
            foreground="#9FE3B8",
            font=("Segoe UI", base + 1, "bold")
        )

        style.configure(
            "HeaderHint.TLabel",
            background=C["navy"],
            foreground="#E8D48A",
            font=("Segoe UI", max(8, base - 1), "bold")
        )

        style.configure(
            "Card.TLabelframe",
            background=C["surface"],
            bordercolor=C["border"],
            padding=(10, 7)
        )

        style.configure(
            "Card.TLabelframe.Label",
            background=C["bg"],
            foreground=C["navy"],
            font=("Segoe UI", base + 1, "bold")
        )

        style.configure(
            "Section.TLabel",
            foreground=C["navy"],
            font=("Segoe UI", base, "bold")
        )

        style.configure(
            "Hint.TLabel",
            foreground=C["muted"],
            font=("Segoe UI", max(8, base - 1), "italic")
        )

        style.configure(
            "Status.TLabel",
            foreground=C["accent"],
            font=("Segoe UI", base + 1, "bold")
        )

        style.configure(
            "Primary.TButton",
            background=C["accent"],
            foreground="white",
            bordercolor=C["accent"],
            font=("Segoe UI", base, "bold"),
            padding=(12, 7)
        )

        style.map(
            "Primary.TButton",
            background=[
                ("active", C["accent_hover"]),
                ("pressed", C["accent_hover"]),
            ],
            foreground=[
                ("!disabled", "white")
            ]
        )

        style.configure(
            "Action.TButton",
            background=C["accent_soft"],
            foreground=C["navy"],
            bordercolor=C["border"],
            padding=(9, 6)
        )

        style.map(
            "Action.TButton",
            background=[
                ("active", "#C7DDF8"),
                ("pressed", "#B6D2F3"),
            ]
        )

        style.configure(
            "Small.TButton",
            background=C["surface"],
            foreground=C["navy"],
            bordercolor=C["border"],
            padding=(7, 4)
        )

        style.configure(
            "Path.TEntry",
            fieldbackground="white",
            foreground=C["text"],
            bordercolor=C["border"],
            padding=(5, 3)
        )

        style.configure(
            "TNotebook",
            background=C["bg"],
            borderwidth=0
        )

        style.configure(
            "TNotebook.Tab",
            background="#D9E6F2",
            foreground=C["navy"],
            padding=(14, 7),
            font=("Segoe UI", base, "bold")
        )

        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", C["accent"]),
                ("active", "#C7DDF8"),
            ],
            foreground=[
                ("selected", "white"),
                ("active", C["navy"]),
            ]
        )

    # ----------------------------------------------------------------------
    # Main window
    # ----------------------------------------------------------------------

    def build_ui(self):
        shell = ttk.Frame(self.root)
        shell.pack(
            fill="both",
            expand=True
        )

        header = ttk.Frame(
            shell,
            style="Header.TFrame"
        )
        header.pack(
            fill="x"
        )

        left = ttk.Frame(
            header,
            style="Header.TFrame"
        )
        left.pack(
            side="left",
            fill="x",
            expand=True
        )

        ttk.Label(
            left,
            text="RAILY",
            style="Title.TLabel"
        ).pack(
            anchor="w"
        )

        ttk.Label(
            left,
            text="SmartScan Railroad Document Intelligence • AI Dispatch Center",
            style="Subtitle.TLabel"
        ).pack(
            anchor="w",
            pady=(1, 0)
        )

        right = ttk.Frame(
            header,
            style="Header.TFrame"
        )
        right.pack(
            side="right",
            padx=(12, 0)
        )

        self.app_status = tk.StringVar(
            value="Ready"
        )

        ttk.Label(
            right,
            text="SIGNAL",
            style="HeaderHint.TLabel"
        ).pack(
            anchor="e"
        )

        ttk.Label(
            right,
            textvariable=self.app_status,
            style="HeaderStatus.TLabel"
        ).pack(
            anchor="e"
        )

        notebook = ttk.Notebook(shell)
        notebook.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=(0, 12)
        )

        dashboard = ttk.Frame(notebook)
        dispatch = ttk.Frame(notebook)
        raily_setup = ttk.Frame(notebook)
        settings = ttk.Frame(notebook)
        management = ttk.Frame(notebook)

        notebook.add(
            dashboard,
            text="Yard Board"
        )

        notebook.add(
            dispatch,
            text="RAILY Dispatch"
        )

        notebook.add(
            raily_setup,
            text="RAILY Setup"
        )

        notebook.add(
            settings,
            text="Settings & Maintenance"
        )

        notebook.add(
            management,
            text="Management Center"
        )

        self._build_raily_dispatch(dispatch)
        self._build_raily_setup(raily_setup)

        # Storage
        paths = ttk.LabelFrame(
            dashboard,
            text="Storage Locations",
            style="Card.TLabelframe"
        )
        paths.pack(
            fill="x",
            padx=8,
            pady=(8, 6)
        )

        self.path_vars = {}

        rows = [
            ("incoming", "Incoming"),
            ("sorted", "Sorted"),
            ("review", "Review"),
            ("duplicates", "Duplicates"),
        ]

        for row, (key, label) in enumerate(rows):
            ttk.Label(
                paths,
                text=label,
                style="Section.TLabel",
                width=12
            ).grid(
                row=row,
                column=0,
                sticky="w",
                padx=(4, 6),
                pady=3
            )

            var = tk.StringVar(
                value=CFG[key]
            )
            self.path_vars[key] = var

            ttk.Entry(
                paths,
                textvariable=var,
                style="Path.TEntry"
            ).grid(
                row=row,
                column=1,
                sticky="ew",
                padx=4,
                pady=3
            )

            ttk.Button(
                paths,
                text="Browse",
                style="Small.TButton",
                command=lambda k=key: self.browse_folder(k)
            ).grid(
                row=row,
                column=2,
                padx=(5, 3),
                pady=3
            )

        paths.columnconfigure(
            1,
            weight=1
        )

        # Actions
        actions = ttk.LabelFrame(
            dashboard,
            text="Yard Controls",
            style="Card.TLabelframe"
        )
        actions.pack(
            fill="x",
            padx=8,
            pady=6
        )

        row1 = ttk.Frame(actions)
        row1.pack(
            fill="x",
            pady=(0, 5)
        )

        ttk.Button(
            row1,
            text="▶  Clear Signal / Start RAILY",
            style="Primary.TButton",
            command=self.start
        ).pack(
            side="left",
            padx=(0, 5)
        )

        ttk.Button(
            row1,
            text="■  Stop RAILY",
            style="Action.TButton",
            command=self.stop
        ).pack(
            side="left",
            padx=3
        )

        ttk.Button(
            row1,
            text="Sort Existing Incoming",
            style="Action.TButton",
            command=self.sort_existing
        ).pack(
            side="left",
            padx=3
        )

        ttk.Button(
            row1,
            text="Conductor Review",
            style="Action.TButton",
            command=self.teach_file
        ).pack(
            side="left",
            padx=3
        )

        row2 = ttk.Frame(actions)
        row2.pack(
            fill="x"
        )

        ttk.Button(
            row2,
            text="Rename Existing Documents",
            style="Action.TButton",
            command=self.rename_existing_documents
        ).pack(
            side="left",
            padx=(0, 5)
        )

        ttk.Button(
            row2,
            text="Find Existing Duplicates",
            style="Action.TButton",
            command=self.find_existing_duplicates
        ).pack(
            side="left",
            padx=3
        )

        ttk.Button(
            row2,
            text="Duplicate Review Center",
            style="Action.TButton",
            command=self.open_duplicate_review_center
        ).pack(
            side="left",
            padx=3
        )

        ttk.Button(
            row2,
            text="Why This Match?",
            style="Action.TButton",
            command=self.open_recognition_explanation
        ).pack(
            side="left",
            padx=3
        )

        ttk.Button(
            row2,
            text="Open Incoming",
            style="Action.TButton",
            command=lambda: self.open_folder(CFG["incoming"])
        ).pack(
            side="right",
            padx=3
        )

        ttk.Button(
            row2,
            text="Open Sorted",
            style="Action.TButton",
            command=lambda: self.open_folder(CFG["sorted"])
        ).pack(
            side="right",
            padx=3
        )

        # Colorful session snapshot
        stats = tk.Frame(
            dashboard,
            bg=self.ui_colors["bg"]
        )
        stats.pack(
            fill="x",
            padx=8,
            pady=(1, 4)
        )

        self.session_sorted_var = tk.StringVar(value="0")
        self.session_duplicate_var = tk.StringVar(value="0")
        self.session_teach_var = tk.StringVar(value="0")
        self.session_backup_var = tk.StringVar(value="0")

        stat_defs = [
            ("✓ SORTED", self.session_sorted_var, self.ui_colors["green_bright"]),
            ("◆ DUPLICATES", self.session_duplicate_var, self.ui_colors["orange"]),
            ("✎ TEACH / REVIEW", self.session_teach_var, self.ui_colors["violet"]),
            ("★ BACKUPS", self.session_backup_var, self.ui_colors["cyan"]),
        ]

        for stat_index, (label, variable, color) in enumerate(stat_defs):
            card = tk.Frame(
                stats,
                bg=color,
                padx=12,
                pady=7
            )
            card.grid(
                row=0,
                column=stat_index,
                sticky="ew",
                padx=(0 if stat_index == 0 else 4, 0)
            )
            stats.columnconfigure(stat_index, weight=1)

            tk.Label(
                card,
                text=label,
                bg=color,
                fg="white",
                font=("Segoe UI", 8, "bold")
            ).pack(anchor="w")

            tk.Label(
                card,
                textvariable=variable,
                bg=color,
                fg="white",
                font=("Segoe UI", 17, "bold")
            ).pack(anchor="w")

        # Persistent daily snapshot (survives restarts)
        daily_stats = tk.Frame(
            dashboard,
            bg=self.ui_colors["bg"]
        )
        daily_stats.pack(fill="x", padx=8, pady=(0, 4))

        self.today_filed_var = tk.StringVar(value="0")
        self.today_duplicate_var = tk.StringVar(value="0")
        self.today_review_var = tk.StringVar(value="0")
        self.today_family_var = tk.StringVar(value="0")
        self.today_auto_rate_var = tk.StringVar(value="0%")

        daily_defs = [
            ("TODAY FILED", self.today_filed_var),
            ("TODAY DUPLICATES", self.today_duplicate_var),
            ("NEEDS REVIEW", self.today_review_var),
            ("NEW FAMILIES", self.today_family_var),
            ("AUTO RATE", self.today_auto_rate_var),
        ]

        for index, (label, variable) in enumerate(daily_defs):
            card = ttk.Frame(daily_stats, padding=(8, 5))
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 3, 0))
            daily_stats.columnconfigure(index, weight=1)
            ttk.Label(card, text=label, style="Hint.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=variable, style="Section.TLabel").pack(anchor="w")

        # Queue banner
        queue_frame = tk.Frame(
            dashboard,
            bg=self.ui_colors["accent_soft"],
            padx=10,
            pady=6
        )
        queue_frame.pack(
            fill="x",
            padx=8,
            pady=(2, 4)
        )

        self.teach_queue_status = tk.StringVar(
            value="Switch List clear"
        )

        tk.Label(
            queue_frame,
            textvariable=self.teach_queue_status,
            bg=self.ui_colors["accent_soft"],
            fg=self.ui_colors["navy"],
            font=("Segoe UI", 9, "bold")
        ).pack(
            side="left"
        )

        tk.Label(
            queue_frame,
            text="TRUE SINGLE-FILE MODE",
            bg=self.ui_colors["accent_soft"],
            fg=self.ui_colors["accent"],
            font=("Segoe UI", 9, "bold")
        ).pack(
            side="right"
        )

        # Activity
        activity_card = ttk.LabelFrame(
            dashboard,
            text="Activity Center",
            style="Card.TLabelframe"
        )
        activity_card.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(4, 8)
        )

        activity_toolbar = ttk.Frame(
            activity_card
        )
        activity_toolbar.pack(
            fill="x",
            pady=(0, 5)
        )

        self.activity_status = tk.StringVar(
            value="Ready"
        )

        ttk.Label(
            activity_toolbar,
            textvariable=self.activity_status,
            style="Status.TLabel"
        ).pack(
            side="left"
        )

        ttk.Button(
            activity_toolbar,
            text="Clear",
            style="Small.TButton",
            command=self.clear_activity
        ).pack(
            side="right",
            padx=(4, 0)
        )

        ttk.Button(
            activity_toolbar,
            text="Copy Activity",
            style="Small.TButton",
            command=self.copy_activity
        ).pack(
            side="right",
            padx=(4, 0)
        )

        activity_body = ttk.Frame(
            activity_card
        )
        activity_body.pack(
            fill="both",
            expand=True
        )

        scroll = ttk.Scrollbar(
            activity_body,
            orient="vertical"
        )
        scroll.pack(
            side="right",
            fill="y"
        )

        self.activity = tk.Text(
            activity_body,
            wrap="word",
            font=("Segoe UI", 9 if self.compact_ui else 10),
            bg=self.ui_colors["surface"],
            fg=self.ui_colors["text"],
            relief="flat",
            padx=10,
            pady=7,
            spacing1=1,
            spacing3=3,
            yscrollcommand=scroll.set
        )

        self.activity.pack(
            side="left",
            fill="both",
            expand=True
        )

        scroll.config(
            command=self.activity.yview
        )

        self.activity.tag_configure(
            "time",
            foreground=self.ui_colors["muted"]
        )

        self.activity.tag_configure(
            "info",
            foreground=self.ui_colors["text"]
        )

        self.activity.tag_configure(
            "processing",
            foreground=self.ui_colors["accent"]
        )

        self.activity.tag_configure(
            "success",
            foreground=self.ui_colors["success"],
            font=("Segoe UI", 9, "bold")
        )

        self.activity.tag_configure(
            "warning",
            foreground=self.ui_colors["warning"],
            font=("Segoe UI", 9, "bold")
        )

        self.activity.tag_configure(
            "error",
            foreground=self.ui_colors["error"],
            font=("Segoe UI", 9, "bold")
        )

        self.activity.tag_configure(
            "paused",
            foreground=self.ui_colors["purple"],
            font=("Segoe UI", 9, "bold")
        )

        # Settings tab
        settings_inner = ttk.Frame(
            settings
        )
        settings_inner.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=8
        )

        settings_inner.columnconfigure(
            0,
            weight=1
        )
        settings_inner.columnconfigure(
            1,
            weight=1
        )

        filing = ttk.LabelFrame(
            settings_inner,
            text="Filing & Learning",
            style="Card.TLabelframe"
        )
        filing.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 5),
            pady=(0, 6)
        )

        safeguards = ttk.LabelFrame(
            settings_inner,
            text="Safeguards & Optional Features",
            style="Card.TLabelframe"
        )
        safeguards.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(5, 0),
            pady=(0, 6)
        )

        self.category_threshold = tk.IntVar(
            value=int(CFG["category_threshold"])
        )

        self.rename_by_type = tk.BooleanVar(
            value=bool(CFG["rename_by_type"])
        )

        self.family_subfolders = tk.BooleanVar(
            value=bool(CFG["family_subfolders"])
        )

        self.ask_to_teach_unknown = tk.BooleanVar(
            value=bool(CFG["ask_to_teach_unknown"])
        )

        self.printed_structure = tk.BooleanVar(
            value=bool(CFG["printed_structure_learning"])
        )

        self.auto_apply_queue = tk.BooleanVar(
            value=bool(CFG["auto_apply_new_learning_to_queue"])
        )

        self.auto_split_combined = tk.BooleanVar(
            value=bool(CFG.get("auto_split_combined_scans", True))
        )

        self.fast_stack_mode = tk.BooleanVar(
            value=bool(CFG.get("fast_stack_mode", True))
        )

        self.incoming_one_at_a_time = tk.BooleanVar(
            value=bool(CFG.get("incoming_one_at_a_time", True))
        )

        self.detect_duplicates = tk.BooleanVar(
            value=bool(CFG["detect_duplicates"])
        )

        self.date_aware_duplicates = tk.BooleanVar(
            value=bool(CFG.get("date_aware_duplicates", True))
        )

        self.family_history_duplicates = tk.BooleanVar(
            value=bool(CFG.get("family_history_duplicates", True))
        )

        self.handwritten_date_mode = tk.BooleanVar(
            value=bool(CFG.get("handwritten_date_mode", True))
        )

        self.date_subcrop_search = tk.BooleanVar(
            value=bool(CFG.get("date_subcrop_search", True))
        )

        self.fast_post_recognition = tk.BooleanVar(
            value=bool(CFG.get("fast_post_recognition", True))
        )

        self.pause_required_date = tk.BooleanVar(
            value=bool(CFG.get("pause_when_required_date_missing", True))
        )

        self.add_date = tk.BooleanVar(
            value=bool(CFG["add_date_to_filename"])
        )

        self.date_threshold = tk.IntVar(
            value=int(CFG["date_confidence_threshold"])
        )

        self.auto_start_sorting = tk.BooleanVar(
            value=bool(CFG.get("auto_start_sorting_on_launch", True))
        )

        self.automatic_state_backups = tk.BooleanVar(
            value=bool(CFG.get("automatic_state_backups", True))
        )
        self.minimize_to_tray = tk.BooleanVar(
            value=bool(CFG.get("minimize_to_tray", False))
        )

        self.auto_confidence_growth = tk.BooleanVar(
            value=bool(CFG.get("auto_confidence_growth", True))
        )

        self.date_area_recovery = tk.BooleanVar(
            value=bool(CFG.get("date_area_recovery", True))
        )

        threshold_line = ttk.Frame(filing)
        threshold_line.pack(
            fill="x",
            pady=(0, 5)
        )

        ttk.Label(
            threshold_line,
            text="Category confidence threshold",
            style="Section.TLabel"
        ).pack(
            side="left"
        )

        ttk.Spinbox(
            threshold_line,
            from_=0,
            to=100,
            textvariable=self.category_threshold,
            width=6
        ).pack(
            side="right"
        )

        ttk.Checkbutton(
            filing,
            text="Use RAILY smart filenames (date - person - document type)",
            variable=self.rename_by_type
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            filing,
            text="Legacy category/document-type subfolders (only when RAILY route filing is off)",
            variable=self.family_subfolders
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            filing,
            text="Pause and ask me to teach when uncertain",
            variable=self.ask_to_teach_unknown
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            filing,
            text="Use printed structure as an OCR fallback signal",
            variable=self.printed_structure
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            filing,
            text="After teaching once, automatically handle matching queued forms",
            variable=self.auto_apply_queue
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            filing,
            text="Automatically separate mixed multi-page scan PDFs",
            variable=self.auto_split_combined
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            filing,
            text="FAST STACK: treat every page in a scanner PDF as its own document",
            variable=self.fast_stack_mode
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            filing,
            text="TRUE single-file Incoming processing (do not preload the folder)",
            variable=self.incoming_one_at_a_time
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Label(
            filing,
            text=(
                "FAST STACK: multi-page scanner PDFs are split immediately with NO pre-OCR, "
                "then each page is processed one at a time. This is fastest when each scanned "
                "paper is a separate document. For a true multi-page document, turn FAST STACK off. "
                "Originals are backed up in Review\\Split Originals.\n\n"
                "One-Teach-Many: queued documents must independently match "
                "the newly learned printed structure before they are filed."
            ),
            style="Hint.TLabel",
            wraplength=430
        ).pack(
            anchor="w",
            pady=(8, 0)
        )

        ttk.Checkbutton(
            safeguards,
            text="Detect exact + possible duplicates",
            variable=self.detect_duplicates
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            safeguards,
            text="Use document date to strengthen duplicate matching",
            variable=self.date_aware_duplicates
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            safeguards,
            text="Compare new scans with previous documents in the same family",
            variable=self.family_history_duplicates
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Label(
            safeguards,
            text=(
                "Family-history duplicate checking searches previously filed documents "
                "from the recognized Document Type first. Family membership by itself is "
                "never considered a duplicate: SmartScan still requires matching date or "
                "account/reference information plus strong OCR/content similarity. "
                "Confirmed matches move to Duplicates\\Review for you to inspect.\n\n"
                "Date-aware duplicates require the same Category, Document Type, "
                "same date, and strong file/content similarity. Same date alone "
                "does not mark a document duplicate. Suspected copies move to "
                "Duplicates\\Review; nothing is auto-deleted."
            ),
            style="Hint.TLabel",
            wraplength=430
        ).pack(
            anchor="w",
            pady=(0, 8)
        )

        ttk.Separator(
            safeguards,
            orient="horizontal"
        ).pack(
            fill="x",
            pady=6
        )

        ttk.Checkbutton(
            safeguards,
            text="Add safe document date to filename when highly confident",
            variable=self.add_date
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            safeguards,
            text="Use handwriting-focused date OCR (blue ink + form-line cleanup)",
            variable=self.handwritten_date_mode
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            safeguards,
            text="Automatically tighten taught date zones to find the cleanest crop",
            variable=self.date_subcrop_search
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            safeguards,
            text="FAST post-recognition mode (quick date/duplicate checks; full OCR only in Teach)",
            variable=self.fast_post_recognition
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            safeguards,
            text="Pause learned dated forms when the date cannot be read",
            variable=self.pause_required_date
        ).pack(
            anchor="w",
            pady=3
        )

        date_line = ttk.Frame(
            safeguards
        )
        date_line.pack(
            fill="x",
            pady=(4, 0)
        )

        ttk.Label(
            date_line,
            text="Required date confidence"
        ).pack(
            side="left"
        )

        ttk.Label(
            date_line,
            text="%"
        ).pack(
            side="right",
            padx=(2, 0)
        )

        ttk.Spinbox(
            date_line,
            from_=50,
            to=100,
            textvariable=self.date_threshold,
            width=6
        ).pack(
            side="right"
        )

        ttk.Label(
            safeguards,
            text=(
                "Dates affect filenames only — never the category, document type, or filing folder. "
                "To teach date locations, open Teach / Correct Type and use Teach Date Area. "
                "You can save multiple areas with different pages and priorities."
            ),
            style="Hint.TLabel",
            wraplength=430
        ).pack(
            anchor="w",
            pady=(8, 0)
        )

        ttk.Separator(
            safeguards,
            orient="horizontal"
        ).pack(
            fill="x",
            pady=6
        )

        ttk.Checkbutton(
            safeguards,
            text="Start RAILY automatically whenever SmartScan opens",
            variable=self.auto_start_sorting
        ).pack(
            anchor="w",
            pady=3
        )

        ttk.Checkbutton(
            safeguards,
            text="Keep rolling backups of learning and saved-date databases",
            variable=self.automatic_state_backups
        ).pack(
            anchor="w",
            pady=3
        )
        ttk.Checkbutton(
            safeguards,
            text="Grow learned confidence after successful automatic filings",
            variable=self.auto_confidence_growth
        ).pack(anchor="w", pady=3)

        ttk.Checkbutton(
            safeguards,
            text="Recover learned date areas when scanner alignment shifts",
            variable=self.date_area_recovery
        ).pack(anchor="w", pady=3)

        ttk.Checkbutton(
            safeguards,
            text="Minimize to Windows system tray when closing the window",
            variable=self.minimize_to_tray
        ).pack(anchor="w", pady=3)

        maintenance = ttk.LabelFrame(
            settings_inner,
            text="Maintenance",
            style="Card.TLabelframe"
        )
        maintenance.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(4, 0)
        )

        row = ttk.Frame(
            maintenance
        )
        row.pack(
            fill="x"
        )

        ttk.Button(
            row,
            text="Build / Rebuild Duplicate Index",
            style="Action.TButton",
            command=self.build_index
        ).pack(
            side="left",
            padx=(0, 6)
        )

        ttk.Button(
            row,
            text="Reset Date Profiles",
            style="Action.TButton",
            command=self.reset_date_profiles
        ).pack(
            side="left",
            padx=3
        )

        ttk.Button(
            row,
            text="Reset v40 Learning",
            style="Action.TButton",
            command=self.reset_learning
        ).pack(
            side="left",
            padx=3
        )


        # --------------------------------------------------------------
        # Management Center
        # --------------------------------------------------------------
        management_inner = ttk.Frame(management)
        management_inner.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )
        management_inner.columnconfigure(0, weight=1)
        management_inner.columnconfigure(1, weight=1)

        protection = ttk.LabelFrame(
            management_inner,
            text="Protection & Recovery",
            style="Card.TLabelframe"
        )
        protection.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 5),
            pady=(0, 6)
        )

        ttk.Button(
            protection,
            text="★ Create Stable Backup",
            style="Primary.TButton",
            command=self.create_stable_backup
        ).pack(fill="x", pady=3)
        ttk.Button(
            protection,
            text="↩ Restore Stable Backup",
            style="Action.TButton",
            command=self.restore_stable_backup
        ).pack(fill="x", pady=3)

        ttk.Button(
            protection,
            text="↶ Undo Last Filing",
            style="Action.TButton",
            command=self.undo_last_filing
        ).pack(fill="x", pady=3)

        ttk.Button(
            protection,
            text="✓ Run Health Check",
            style="Action.TButton",
            command=self.health_check
        ).pack(fill="x", pady=3)

        ttk.Button(
            protection,
            text="Open Backup Folder",
            style="Action.TButton",
            command=lambda: self.open_folder(BACKUP_ROOT)
        ).pack(fill="x", pady=3)

        managers = ttk.LabelFrame(
            management_inner,
            text="Learning Managers",
            style="Card.TLabelframe"
        )
        managers.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(5, 0),
            pady=(0, 6)
        )

        ttk.Button(
            managers,
            text="Document Learning Manager",
            style="Action.TButton",
            command=self.open_learning_manager
        ).pack(fill="x", pady=3)

        ttk.Button(
            managers,
            text="Date Profile Manager",
            style="Action.TButton",
            command=self.open_date_profile_manager
        ).pack(fill="x", pady=3)

        ttk.Button(
            managers,
            text="Saved Manual Date Manager",
            style="Action.TButton",
            command=self.open_manual_date_manager
        ).pack(fill="x", pady=3)
        ttk.Button(
            managers,
            text="Duplicate Review Center",
            style="Action.TButton",
            command=self.open_duplicate_review_center
        ).pack(fill="x", pady=3)

        windows_card = ttk.LabelFrame(
            management_inner,
            text="Windows Integration",
            style="Card.TLabelframe"
        )
        windows_card.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(4, 0)
        )

        windows_row = ttk.Frame(windows_card)
        windows_row.pack(fill="x")

        ttk.Button(
            windows_row,
            text="Create Desktop Shortcut",
            style="Primary.TButton",
            command=self.create_desktop_shortcut
        ).pack(side="left", padx=(0, 5), pady=3)

        ttk.Button(
            windows_row,
            text="Enable Start with Windows",
            style="Action.TButton",
            command=self.enable_windows_startup
        ).pack(side="left", padx=3, pady=3)

        ttk.Button(
            windows_row,
            text="Disable Start with Windows",
            style="Action.TButton",
            command=self.disable_windows_startup
        ).pack(side="left", padx=3, pady=3)
        ttk.Button(
            windows_row,
            text="Build Windows EXE",
            style="Action.TButton",
            command=self.build_windows_app
        ).pack(side="left", padx=3, pady=3)

        self.windows_integration_status = tk.StringVar(value="")

        tk.Label(
            windows_card,
            textvariable=self.windows_integration_status,
            bg=self.ui_colors["surface"],
            fg=self.ui_colors["purple"],
            font=("Segoe UI", 9, "bold"),
            anchor="w"
        ).pack(fill="x", pady=(6, 2))

        ttk.Label(
            windows_card,
            text=(
                "Start with Windows launches SmartScan minimized using pythonw.exe. "
                "Automatic sorting starts on its own, so Incoming can be watched "
                "without manually pressing Start."
            ),
            style="Hint.TLabel",
            wraplength=900
        ).pack(anchor="w", pady=(2, 2))

        self.refresh_windows_integration_status()

    # ----------------------------------------------------------------------
    # RAILY Dispatch Center
    # ----------------------------------------------------------------------

    def _build_raily_dispatch(self, parent):
        C = self.ui_colors
        dispatch_bg = C.get("dispatch_bg", "#101619")
        panel_bg = "#172126"
        panel2_bg = "#202D33"
        steel = C.get("rail_steel", "#69757B")
        yellow = C.get("signal_yellow", "#F4C430")
        green = C.get("green_bright", "#198754")
        compact = bool(getattr(self, "compact_ui", False))

        self.raily_stage_names = [
            "Received", "Analyze", "Identify", "Route",
            "Date", "Check", "File", "Clear"
        ]
        self.raily_stage_index = 0
        self.raily_preview_photo = None
        self.raily_current_path = ""

        outer = tk.Frame(parent, bg=dispatch_bg, padx=(8 if compact else 10), pady=(7 if compact else 10))
        outer.pack(fill="both", expand=True)

        top = tk.Frame(outer, bg=dispatch_bg)
        top.pack(fill="x", pady=(0, 8))

        tk.Label(
            top,
            text="RAILY  •  DISPATCH CENTER",
            bg=dispatch_bg,
            fg="white",
            font=("Segoe UI", 16 if compact else 18, "bold")
        ).pack(side="left")

        self.raily_signal_var = tk.StringVar(value="SIGNAL: CLEAR")
        self.raily_signal_label = tk.Label(
            top,
            textvariable=self.raily_signal_var,
            bg=green,
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=5
        )
        self.raily_signal_label.pack(side="right")

        tk.Label(
            top,
            text="Railroad → Location → Documents",
            bg=dispatch_bg,
            fg="#E8D48A",
            font=("Segoe UI", 9, "bold")
        ).pack(side="right", padx=(0, 12))

        # v71.2: Always show the exact source folder RAILY is watching.
        source_bar = tk.Frame(outer, bg="#0B1114", padx=8, pady=(4 if compact else 5))
        source_bar.pack(fill="x", pady=(0, 6))
        tk.Label(
            source_bar,
            text="WATCHING INCOMING",
            bg="#0B1114", fg=yellow,
            font=("Segoe UI", 8, "bold")
        ).pack(side="left", padx=(0, 8))
        self.raily_watch_folder_var = tk.StringVar(value=str(CFG.get("incoming", "")))
        self.raily_watch_folder_entry = tk.Entry(
            source_bar,
            textvariable=self.raily_watch_folder_var,
            state="readonly",
            readonlybackground="#0B1114",
            fg="#D8E0E3",
            relief="flat",
            bd=0,
            font=("Consolas", 8 if compact else 9)
        )
        self.raily_watch_folder_entry.pack(side="left", fill="x", expand=True)
        tk.Button(
            source_bar,
            text="Open Incoming",
            command=lambda: self.open_folder(CFG["incoming"]),
            bg="#263238", fg="white", activebackground="#35454C", activeforeground="white",
            relief="flat", padx=8, pady=2,
            font=("Segoe UI", 8, "bold")
        ).pack(side="right", padx=(8, 0))

        track_frame = tk.Frame(outer, bg=panel_bg, padx=10, pady=(4 if compact else 6))
        track_frame.pack(fill="x", pady=(0, 8))
        self.raily_stage_var = tk.StringVar(value="RAILY is standing by")
        tk.Label(
            track_frame,
            textvariable=self.raily_stage_var,
            bg=panel_bg,
            fg="white",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w")
        self.raily_track_canvas = tk.Canvas(
            track_frame,
            height=(56 if compact else 72),
            bg=panel_bg,
            highlightthickness=0
        )
        self.raily_track_canvas.pack(fill="x", pady=(3, 0))
        self.raily_track_canvas.bind("<Configure>", lambda _e: self._draw_raily_track())

        body = tk.Frame(outer, bg=dispatch_bg)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=5)
        body.grid_columnconfigure(1, weight=4)
        body.grid_rowconfigure(0, weight=1)

        # Whole-page document preview.
        preview_panel = tk.Frame(
            body, bg=panel_bg, padx=(7 if compact else 10), pady=(7 if compact else 10),
            highlightbackground=steel, highlightthickness=1
        )
        preview_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        preview_panel.grid_rowconfigure(1, weight=1)
        preview_panel.grid_columnconfigure(0, weight=1)

        self.raily_document_var = tk.StringVar(value="No document on track")
        tk.Label(
            preview_panel,
            text="DOCUMENT ON TRACK",
            bg=panel_bg,
            fg=yellow,
            font=("Segoe UI", 9, "bold")
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            preview_panel,
            textvariable=self.raily_document_var,
            bg=panel_bg,
            fg="white",
            font=("Segoe UI", 10, "bold"),
            anchor="e"
        ).grid(row=0, column=0, sticky="e")

        self.raily_preview_label = tk.Label(
            preview_panel,
            text="RAILY is ready for the next document.",
            bg="#0A0F11",
            fg="#A8B4BA",
            font=("Segoe UI", 11),
            compound="center"
        )
        self.raily_preview_label.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        # Live AI understanding panel.
        ai_panel = tk.Frame(
            body, bg=panel2_bg, padx=(8 if compact else 12), pady=(7 if compact else 10),
            highlightbackground=steel, highlightthickness=1
        )
        ai_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ai_panel.grid_columnconfigure(1, weight=1)

        tk.Label(
            ai_panel,
            text="RAILY AI DOCUMENT ANALYSIS",
            bg=panel2_bg,
            fg=yellow,
            font=("Segoe UI", 10 if compact else 11, "bold")
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5 if compact else 8))

        self.raily_type_var = tk.StringVar(value="—")
        self.raily_railroad_var = tk.StringVar(value="—")
        self.raily_location_var = tk.StringVar(value="—")
        self.raily_person_var = tk.StringVar(value="—")
        self.raily_date_var = tk.StringVar(value="—")
        self.raily_confidence_var = tk.StringVar(value="—")
        self.raily_source_var = tk.StringVar(value="—")
        self.raily_route_var = tk.StringVar(value="Waiting for document")
        self.raily_destination_var = tk.StringVar(value="—")

        field_defs = [
            ("Document Type", self.raily_type_var),
            ("Railroad", self.raily_railroad_var),
            ("Location", self.raily_location_var),
            ("Primary Person", self.raily_person_var),
            ("Document Date", self.raily_date_var),
            ("Confidence", self.raily_confidence_var),
            ("Recognition", self.raily_source_var),
        ]
        for row, (label, var) in enumerate(field_defs, start=1):
            tk.Label(
                ai_panel, text=label.upper(), bg=panel2_bg, fg="#8FA1A8",
                font=("Segoe UI", 7 if compact else 8, "bold")
            ).grid(row=row, column=0, sticky="w", padx=(0, 8 if compact else 10), pady=(2 if compact else 5))
            tk.Label(
                ai_panel, textvariable=var, bg=panel2_bg, fg="white",
                font=("Segoe UI", 9 if compact else 10, "bold"), anchor="w", justify="left",
                wraplength=(330 if compact else 420)
            ).grid(row=row, column=1, sticky="ew", pady=(2 if compact else 5))

        route_box = tk.Frame(ai_panel, bg="#0F171A", padx=8 if compact else 10, pady=5 if compact else 8)
        route_box.grid(row=9, column=0, columnspan=2, sticky="ew", pady=((5 if compact else 10), (3 if compact else 5)))
        tk.Label(
            route_box, text="ROUTE", bg="#0F171A", fg=yellow,
            font=("Segoe UI", 8, "bold")
        ).pack(anchor="w")
        tk.Label(
            route_box, textvariable=self.raily_route_var, bg="#0F171A", fg="white",
            font=("Segoe UI", 10 if compact else 11, "bold"), wraplength=(350 if compact else 480), justify="left"
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            route_box, textvariable=self.raily_destination_var, bg="#0F171A", fg="#A8B4BA",
            font=("Segoe UI", 7 if compact else 8), wraplength=(350 if compact else 480), justify="left"
        ).pack(anchor="w", pady=(2, 0))

        # Switch List + AI activity feed.
        lower = tk.Frame(outer, bg=dispatch_bg)
        lower.pack(fill="x", pady=((5 if compact else 8), 0))
        lower.grid_columnconfigure(0, weight=1)
        lower.grid_columnconfigure(1, weight=2)

        switch = tk.Frame(lower, bg=panel_bg, padx=7, pady=(5 if compact else 8))
        switch.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        tk.Label(
            switch, text="SWITCH LIST", bg=panel_bg, fg=yellow,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w")
        self.raily_switch_list = tk.Listbox(
            switch, height=(3 if compact else 6), bg="#0A0F11", fg="#D8E0E3",
            selectbackground="#5A4A12", relief="flat", font=("Consolas", 9)
        )
        self.raily_switch_list.pack(fill="both", expand=True, pady=(4, 0))

        feed = tk.Frame(lower, bg=panel_bg, padx=7, pady=(5 if compact else 8))
        feed.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        tk.Label(
            feed, text="RAILY LIVE ACTIVITY", bg=panel_bg, fg=yellow,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w")
        self.raily_activity = tk.Text(
            feed, height=(3 if compact else 6), wrap="word", bg="#0A0F11", fg="#D8E0E3",
            insertbackground="white", relief="flat", font=("Consolas", 9),
            padx=7, pady=5, state="disabled"
        )
        self.raily_activity.pack(fill="both", expand=True, pady=(4, 0))

        self._refresh_raily_watch_folder()
        self._draw_raily_track()
        self.root.after(1000, self._refresh_raily_switch_list)

    def _build_raily_setup(self, parent):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True, padx=12, pady=12)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        self.raily_visual_ai_var = tk.BooleanVar(
            value=bool(CFG.get("raily_visual_ai_enabled", True))
        )
        self.raily_visual_threshold_var = tk.IntVar(
            value=int(CFG.get("raily_visual_autofile_threshold", 90))
        )
        self.raily_route_filing_var = tk.BooleanVar(
            value=bool(CFG.get("raily_route_filing", True))
        )
        self.raily_require_route_var = tk.BooleanVar(
            value=bool(CFG.get("raily_require_railroad_location", True))
        )
        self.raily_person_filename_var = tk.BooleanVar(
            value=bool(CFG.get("raily_include_person_in_filename", True))
        )
        self.raily_ai_provider_var = tk.StringVar(
            value=str(CFG.get("raily_ai_provider", "Local Full-Page"))
        )
        self.raily_vision_url_var = tk.StringVar(
            value=str(CFG.get("raily_vision_api_url", ""))
        )
        self.raily_vision_model_var = tk.StringVar(
            value=str(CFG.get("raily_vision_model", ""))
        )
        self.raily_vision_key_env_var = tk.StringVar(
            value=str(CFG.get("raily_vision_api_key_env", "RAILY_VISION_API_KEY"))
        )
        self.raily_update_mode_var = tk.StringVar(
            value=str(CFG.get("raily_update_mode", "Recommended"))
        )
        self.raily_update_manifest_var = tk.StringVar(
            value=str(CFG.get("raily_update_manifest_url", ""))
        )
        self.raily_update_idle_var = tk.BooleanVar(
            value=bool(CFG.get("raily_update_only_when_idle", True))
        )
        self.raily_update_status_var = tk.StringVar(
            value=f"Installed version: {APP_VERSION} • updater waiting for a trusted manifest"
        )

        brain = ttk.LabelFrame(
            container,
            text="RAILY Full-Page AI Brain",
            style="Card.TLabelframe"
        )
        brain.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))

        ttk.Checkbutton(
            brain,
            text="Use full-page visual learning before OCR classification",
            variable=self.raily_visual_ai_var
        ).pack(anchor="w", pady=4)

        visual_line = ttk.Frame(brain)
        visual_line.pack(fill="x", pady=3)
        ttk.Label(visual_line, text="Automatic visual-match confidence", style="Section.TLabel").pack(side="left")
        ttk.Label(visual_line, text="%").pack(side="right", padx=(2, 0))
        ttk.Spinbox(
            visual_line, from_=70, to=100,
            textvariable=self.raily_visual_threshold_var, width=6
        ).pack(side="right")

        ttk.Label(
            brain,
            text=(
                "RAILY learns the entire page layout, not just OCR words. Taught forms can match from "
                "logos, tables, whitespace and printed structure even when OCR runs words together."
            ),
            style="Hint.TLabel",
            wraplength=500
        ).pack(anchor="w", pady=(5, 10))

        provider_row = ttk.Frame(brain)
        provider_row.pack(fill="x", pady=3)
        ttk.Label(provider_row, text="AI provider", style="Section.TLabel", width=17).pack(side="left")
        ttk.Combobox(
            provider_row,
            textvariable=self.raily_ai_provider_var,
            values=["Local Full-Page", "External Vision Endpoint"],
            state="readonly",
            width=28
        ).pack(side="left", fill="x", expand=True)

        for label, variable in (
            ("Vision endpoint", self.raily_vision_url_var),
            ("Vision model", self.raily_vision_model_var),
            ("API key env var", self.raily_vision_key_env_var),
        ):
            row = ttk.Frame(brain)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=17).pack(side="left")
            ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

        ttk.Label(
            brain,
            text=(
                "External Vision is optional. SmartScan never stores the API key itself; it reads the "
                "Windows environment variable named above. Local Full-Page mode needs no internet."
            ),
            style="Hint.TLabel",
            wraplength=500
        ).pack(anchor="w", pady=(5, 6))

        route = ttk.LabelFrame(
            container,
            text="Railroad Filing Route",
            style="Card.TLabelframe"
        )
        route.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))

        ttk.Checkbutton(
            route,
            text="Use Railroad → Location → Documents",
            variable=self.raily_route_filing_var
        ).pack(anchor="w", pady=4)
        ttk.Checkbutton(
            route,
            text="Stop for Conductor Review if Railroad or Location is missing",
            variable=self.raily_require_route_var
        ).pack(anchor="w", pady=4)
        ttk.Checkbutton(
            route,
            text="Include primary person's name in the filename when found",
            variable=self.raily_person_filename_var
        ).pack(anchor="w", pady=4)

        example = tk.Frame(route, bg="#101619", padx=10, pady=10)
        example.pack(fill="x", pady=(10, 6))
        tk.Label(
            example,
            text="ROUTE EXAMPLE",
            bg="#101619",
            fg=self.ui_colors.get("signal_yellow", "#F4C430"),
            font=("Segoe UI", 8, "bold")
        ).pack(anchor="w")
        tk.Label(
            example,
            text=(
                "IRAIL  →  Burley, Idaho\n"
                "2026-09-12 - John Smith - IRAIL Work Log.pdf"
            ),
            bg="#101619",
            fg="white",
            font=("Consolas", 10, "bold"),
            justify="left"
        ).pack(anchor="w", pady=(4, 0))

        ttk.Label(
            route,
            text=(
                "RAILY still learns Category and Document Type for search, duplicate checks and AI memory, "
                "but they do not create extra folders."
            ),
            style="Hint.TLabel",
            wraplength=500
        ).pack(anchor="w", pady=(5, 6))

        updates = ttk.LabelFrame(
            container,
            text="RAILY Safe Update Engine",
            style="Card.TLabelframe"
        )
        updates.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        updates.columnconfigure(1, weight=1)

        ttk.Label(updates, text="Update mode", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        ttk.Combobox(
            updates,
            textvariable=self.raily_update_mode_var,
            values=["Recommended", "Automatic", "Manual"],
            state="readonly",
            width=18
        ).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(updates, text="Trusted manifest URL", style="Section.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4
        )
        ttk.Entry(updates, textvariable=self.raily_update_manifest_var).grid(
            row=1, column=1, sticky="ew", pady=4
        )

        ttk.Checkbutton(
            updates,
            text="Install automatic updates only when the yard is clear / processing is idle",
            variable=self.raily_update_idle_var
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)

        button_row = ttk.Frame(updates)
        button_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 3))
        ttk.Button(
            button_row,
            text="Check for Updates Now",
            style="Primary.TButton",
            command=lambda: self.check_for_updates(manual=True)
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            button_row,
            text="Open Update Folder",
            style="Action.TButton",
            command=lambda: self.open_folder(UPDATE_DIR)
        ).pack(side="left", padx=3)
        ttk.Button(
            button_row,
            text="RAILY Memory Manager",
            style="Action.TButton",
            command=self.open_raily_memory_manager
        ).pack(side="left", padx=3)

        ttk.Label(
            updates,
            textvariable=self.raily_update_status_var,
            style="Status.TLabel",
            wraplength=1000
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 2))
        ttk.Label(
            updates,
            text=(
                "A trusted update manifest is required because RAILY will not download or run arbitrary code. "
                "Before any install it creates a rollback package, verifies SHA-256, waits for idle when requested, "
                "stages the new build, and restarts through a small Windows updater script."
            ),
            style="Hint.TLabel",
            wraplength=1000
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 4))

    def open_raily_memory_manager(self):
        win = tk.Toplevel(self.root)
        win.title("RAILY Memory Manager")
        _fit_learning_window(win, 820, 560)
        win.transient(self.root)

        ttk.Button(win, text="Close", command=win.destroy).pack(side="bottom", pady=6)
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        def add_entity_tab(title, kind):
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=title)
            entity_actions = ttk.Frame(frame)
            entity_actions.pack(side="bottom", fill="x")
            entity_table = ttk.Frame(frame)
            entity_table.pack(fill="both", expand=True)
            tree = ttk.Treeview(entity_table, columns=("name", "count", "last"), show="headings")
            tree.heading("name", text=title[:-1] if title.endswith("s") else title)
            tree.heading("count", text="Learned")
            tree.heading("last", text="Last Seen")
            tree.column("name", width=330)
            tree.column("count", width=90, anchor="center")
            tree.column("last", width=220)
            tree.pack(fill="both", expand=True, pady=(0, 6))
            for key, item in sorted(RAILY_MEMORY.get(kind, {}).items(), key=lambda kv: kv[1].get("name", "").casefold()):
                tree.insert("", "end", iid=key, values=(item.get("name", ""), item.get("count", 0), item.get("last_seen", "")))

            def delete_selected():
                selection = tree.selection()
                if not selection:
                    return
                key = selection[0]
                name = RAILY_MEMORY.get(kind, {}).get(key, {}).get("name", key)
                if not messagebox.askyesno("RAILY Memory", f"Remove this learned {title.lower()[:-1]}?\n\n{name}", parent=win):
                    return
                RAILY_MEMORY.get(kind, {}).pop(key, None)
                save_raily_memory()
                tree.delete(key)

            ttk.Button(entity_actions, text="Remove Selected", style="Action.TButton", command=delete_selected).pack(anchor="e")

            _learning_tree_scrollbars(tree)

        add_entity_tab("Railroads", "railroads")
        add_entity_tab("Locations", "locations")
        add_entity_tab("People", "people")



    # ------------------------------------------------------------------
    # RAILY safe self-update engine
    # ------------------------------------------------------------------

    @staticmethod
    def _raily_version_tuple(value):
        parts = re.findall(r"\d+", str(value or ""))
        return tuple(int(x) for x in (parts[:4] or [0]))

    def _set_raily_update_status(self, text):
        var = getattr(self, "raily_update_status_var", None)
        if var is not None:
            try:
                var.set(str(text))
            except Exception:
                pass
        self.note(f"RAILY Update: {text}")

    def _yard_clear_for_update(self):
        try:
            if not self.engine._processing_is_idle():
                return False
            if self.teach_window_open or self.teach_queue:
                return False
            incoming = Path(CFG["incoming"])
            if incoming.exists():
                for item in incoming.iterdir():
                    if item.is_file() and item.suffix.lower() in SUPPORTED:
                        return False
            return True
        except Exception:
            return False

    def check_for_updates(self, manual=False):
        try:
            self.sync()
        except Exception:
            pass

        manifest_url = str(CFG.get("raily_update_manifest_url", "") or "").strip()
        if not manifest_url:
            message = (
                "No trusted update manifest is configured yet. Add the manifest URL in "
                "RAILY Setup when you have a release location."
            )
            self._set_raily_update_status(message)
            if manual:
                messagebox.showinfo("RAILY Updates", message, parent=self.root)
            return

        if not (manifest_url.lower().startswith("https://") or manifest_url.lower().startswith("file://")):
            message = "For safety, the update manifest must use HTTPS or a local file:// URL."
            self._set_raily_update_status(message)
            if manual:
                messagebox.showerror("RAILY Updates", message, parent=self.root)
            return

        self._set_raily_update_status("Checking the trusted release signal...")

        def worker():
            try:
                req = urllib.request.Request(
                    manifest_url,
                    headers={"User-Agent": f"RAILY-SmartScan/{APP_VERSION}"}
                )
                with urllib.request.urlopen(req, timeout=20) as response:
                    raw = response.read(512 * 1024)
                manifest = json.loads(raw.decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise RuntimeError("Update manifest is not a JSON object.")
                self.root.after(0, lambda: self._handle_raily_update_manifest(manifest, manual))
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self._raily_update_error(str(exc), manual))

        threading.Thread(target=worker, daemon=True, name="RAILYUpdateCheck").start()

    def _raily_update_error(self, message, manual=False):
        self._set_raily_update_status(f"Update check failed: {message}")
        if manual:
            messagebox.showerror(
                "RAILY Updates",
                f"RAILY could not check for updates.\n\n{message}",
                parent=self.root
            )

    def _handle_raily_update_manifest(self, manifest, manual=False):
        version = str(manifest.get("version", "") or "").strip()
        download_url = str(manifest.get("url", "") or "").strip()
        checksum = str(manifest.get("sha256", "") or "").strip().lower()
        notes = str(manifest.get("notes", "") or "").strip()
        channel = str(manifest.get("channel", "stable") or "stable").strip().casefold()
        wanted_channel = str(CFG.get("raily_update_channel", "stable") or "stable").strip().casefold()

        if not version or not download_url or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            self._raily_update_error(
                "Manifest must contain version, download url, and a 64-character SHA-256 checksum.",
                manual
            )
            return
        if not (download_url.lower().startswith("https://") or download_url.lower().startswith("file://")):
            self._raily_update_error("Update payload must use HTTPS or file://.", manual)
            return
        if channel != wanted_channel:
            self._set_raily_update_status(
                f"Release {version} is on the {channel} channel; this installation follows {wanted_channel}."
            )
            return

        if self._raily_version_tuple(version) <= self._raily_version_tuple(APP_VERSION):
            message = f"RAILY is current — version {APP_VERSION} is the newest trusted release."
            self._set_raily_update_status(message)
            if manual:
                messagebox.showinfo("RAILY Updates", message, parent=self.root)
            return

        self._set_raily_update_status(f"Version {version} is available.")
        mode = str(CFG.get("raily_update_mode", "Recommended"))

        if mode.casefold() == "automatic" and not manual:
            if CFG.get("raily_update_only_when_idle", True) and not self._yard_clear_for_update():
                self._set_raily_update_status(
                    f"Version {version} is ready. RAILY is waiting for the yard to clear before installing."
                )
                self.root.after(
                    15000,
                    lambda m=dict(manifest): self._handle_raily_update_manifest(m, manual=False)
                )
                return
            self.install_raily_update(manifest, ask_first=False)
            return

        details = f"RAILY SmartScan {version} is available.\n\n"
        if notes:
            details += notes[:1600] + "\n\n"
        details += (
            "RAILY will create a rollback backup, verify SHA-256, stage the update, "
            "restart, and automatically restore the previous app if the new build does not pass startup health check.\n\n"
            "Install this update now?"
        )
        if messagebox.askyesno("RAILY Update Available", details, parent=self.root):
            if CFG.get("raily_update_only_when_idle", True) and not self._yard_clear_for_update():
                messagebox.showinfo(
                    "RAILY Update",
                    "The update is verified as available, but the yard is not clear yet. "
                    "Finish the current document/review and click Check for Updates Now again.",
                    parent=self.root
                )
                return
            self.install_raily_update(manifest, ask_first=False)

    def _create_raily_update_backup(self, new_version):
        folder = BACKUP_ROOT / "Updates"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        archive = folder / f"RAILY_BEFORE_{new_version}_{stamp}.zip"
        candidates = [
            current_application_path(), CONFIG_FILE, LEARNING_FILE, INDEX_FILE,
            DATE_PROFILE_FILE, DOCUMENT_DATE_FILE, FILING_HISTORY_FILE,
            FAMILY_DUP_HISTORY_FILE, STATS_FILE, RAILY_MEMORY_FILE,
        ]
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            added = set()
            for item in candidates:
                item = Path(item)
                if not item.exists() or not item.is_file():
                    continue
                key = str(item.resolve()).casefold()
                if key in added:
                    continue
                added.add(key)
                if item == current_application_path():
                    arcname = "Application/" + item.name
                else:
                    try:
                        arcname = "State/" + str(item.relative_to(APP_DIR)).replace("\\", "/")
                    except Exception:
                        arcname = "State/" + item.name
                zf.write(item, arcname=arcname)
        return archive

    def install_raily_update(self, manifest, ask_first=False):
        version = str(manifest.get("version", "") or "").strip()
        url = str(manifest.get("url", "") or "").strip()
        checksum = str(manifest.get("sha256", "") or "").strip().lower()

        if ask_first and not messagebox.askyesno(
            "Install RAILY Update",
            f"Install RAILY SmartScan {version}?",
            parent=self.root
        ):
            return

        self._set_raily_update_status(f"Downloading version {version}...")

        def worker():
            staged = None
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": f"RAILY-SmartScan/{APP_VERSION}"}
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    payload = response.read(100 * 1024 * 1024 + 1)
                if len(payload) > 100 * 1024 * 1024:
                    raise RuntimeError("Update payload exceeds the 100 MB safety limit.")

                actual = hashlib.sha256(payload).hexdigest().lower()
                if actual != checksum:
                    raise RuntimeError(
                        "SHA-256 verification failed. The downloaded update was not installed."
                    )

                current = current_application_path()
                suffix = current.suffix.lower()
                if suffix not in {".py", ".exe"}:
                    raise RuntimeError(f"Self-update is not supported for application type {suffix}.")

                staged = UPDATE_DIR / f"RAILY_SmartScan_v{version}_STAGED{suffix}"
                staged.write_bytes(payload)

                if suffix == ".py":
                    try:
                        source = payload.decode("utf-8")
                        compile(source, str(staged), "exec")
                    except Exception as exc:
                        raise RuntimeError(f"The downloaded Python build failed syntax validation: {exc}")
                elif not payload.startswith(b"MZ"):
                    raise RuntimeError("The downloaded Windows application does not have a valid EXE header.")

                backup = self._create_raily_update_backup(version)
                self.root.after(
                    0,
                    lambda: self._finish_raily_update_stage(manifest, staged, backup)
                )
            except Exception as exc:
                if staged:
                    try:
                        Path(staged).unlink(missing_ok=True)
                    except Exception:
                        pass
                self.root.after(
                    0,
                    lambda exc=exc: messagebox.showerror(
                        "RAILY Update",
                        f"Update was NOT installed.\n\n{exc}",
                        parent=self.root
                    )
                )
                self.root.after(0, lambda exc=exc: self._set_raily_update_status(f"Update stopped safely: {exc}"))

        threading.Thread(target=worker, daemon=True, name="RAILYUpdateDownload").start()

    def _finish_raily_update_stage(self, manifest, staged, backup):
        version = str(manifest.get("version", ""))
        self._set_raily_update_status(
            f"Version {version} downloaded, SHA-256 verified, and rollback backup created."
        )
        auto_restart = str(CFG.get("raily_update_mode", "Recommended")).casefold() == "automatic"
        if not auto_restart:
            if not messagebox.askyesno(
                "RAILY Update Ready",
                (
                    f"RAILY SmartScan {version} is verified and ready.\n\n"
                    f"Rollback backup:\n{backup}\n\n"
                    "Restart now to install?"
                ),
                parent=self.root
            ):
                return

        try:
            current = current_application_path()
            rollback = UPDATE_DIR / f"ROLLBACK_{current.name}"
            shutil.copy2(current, rollback)
            marker = UPDATE_DIR / "RAILY_UPDATE_HEALTHY.marker"
            try:
                marker.unlink()
            except FileNotFoundError:
                pass

            cmd = UPDATE_DIR / "RAILY_APPLY_UPDATE.cmd"
            if current.suffix.lower() == ".py":
                launcher = Path(sys.executable)
                if launcher.name.lower() == "python.exe":
                    pythonw = launcher.with_name("pythonw.exe")
                    if pythonw.exists():
                        launcher = pythonw
                launch_new = f'start "" "{launcher}" "{current}" --raily-update-health "{marker}"'
                launch_old = f'start "" "{launcher}" "{current}"'
            else:
                launch_new = f'start "" "{current}" --raily-update-health "{marker}"'
                launch_old = f'start "" "{current}"'

            script = (
                "@echo off\r\n"
                "setlocal\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                f'copy /Y "{staged}" "{current}" >nul\r\n'
                f'del /Q "{marker}" >nul 2>&1\r\n'
                f'{launch_new}\r\n'
                "timeout /t 25 /nobreak >nul\r\n"
                f'if not exist "{marker}" (\r\n'
                f'  copy /Y "{rollback}" "{current}" >nul\r\n'
                f'  {launch_old}\r\n'
                ")\r\n"
                f'del /Q "{staged}" >nul 2>&1\r\n'
                "del /Q \"%~f0\" >nul 2>&1\r\n"
            )
            cmd.write_text(script, encoding="utf-8")

            RAILY_MEMORY.setdefault("updates", []).append({
                "from": APP_VERSION,
                "to": version,
                "staged_at": datetime.now().isoformat(timespec="seconds"),
                "backup": str(backup),
            })
            RAILY_MEMORY["updates"] = RAILY_MEMORY["updates"][-50:]
            save_raily_memory()

            subprocess.Popen(
                ["cmd.exe", "/c", str(cmd)],
                cwd=str(APP_DIR),
                creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
            )
            self.quit_app()
        except Exception as exc:
            messagebox.showerror(
                "RAILY Update",
                f"The update is staged but restart setup failed. Your current app was not replaced.\n\n{exc}",
                parent=self.root
            )

    def _mark_raily_update_health_if_requested(self):
        try:
            if "--raily-update-health" not in sys.argv:
                return
            index = sys.argv.index("--raily-update-health")
            if index + 1 >= len(sys.argv):
                return
            marker = Path(sys.argv[index + 1])
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                f"RAILY SmartScan {APP_VERSION} healthy at {datetime.now().isoformat()}\n",
                encoding="utf-8"
            )
        except Exception:
            pass

    def _draw_raily_track(self):
        canvas = getattr(self, "raily_track_canvas", None)
        if canvas is None:
            return
        try:
            canvas.delete("all")
            width = max(620, canvas.winfo_width())
            compact = bool(getattr(self, "compact_ui", False))
            y = 20 if compact else 25
            margin = 38 if compact else 45
            count = max(2, len(self.raily_stage_names))
            step = (width - margin * 2) / (count - 1)
            canvas.create_line(margin, y, width - margin, y, fill="#69757B", width=5)
            for i, name in enumerate(self.raily_stage_names):
                x = margin + step * i
                if i < self.raily_stage_index:
                    fill = self.ui_colors.get("green_bright", "#198754")
                elif i == self.raily_stage_index:
                    fill = self.ui_colors.get("signal_yellow", "#F4C430")
                else:
                    fill = "#3D4A50"
                radius = 7 if compact else 9
                canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill, outline="#D5DDE0", width=1)
                canvas.create_text(x, 43 if compact else 53, text=name, fill="#D8E0E3", font=("Segoe UI", 7 if compact else 8, "bold"))
        except Exception:
            pass

    def _refresh_raily_watch_folder(self):
        """Keep the Dispatch Center source-folder banner synced with Storage Locations."""
        var = getattr(self, "raily_watch_folder_var", None)
        if var is None:
            return
        try:
            incoming = str(Path(CFG.get("incoming", "")).expanduser())
        except Exception:
            incoming = str(CFG.get("incoming", "") or "")
        try:
            var.set(incoming or "Incoming folder is not configured")
        except Exception:
            pass

    def _set_raily_signal(self, signal="green", text=None):
        label = getattr(self, "raily_signal_label", None)
        var = getattr(self, "raily_signal_var", None)
        if label is None or var is None:
            return
        colors = {
            "green": self.ui_colors.get("green_bright", "#198754"),
            "yellow": self.ui_colors.get("signal_yellow", "#F4C430"),
            "red": self.ui_colors.get("error", "#B42318"),
        }
        names = {"green": "CLEAR", "yellow": "APPROACH", "red": "STOP"}
        signal = signal if signal in colors else "yellow"
        label.configure(bg=colors[signal], fg=("#111" if signal == "yellow" else "white"))
        var.set(text or f"SIGNAL: {names[signal]}")

    def _raily_stage_position(self, stage):
        mapping = {
            "received": 0, "splitting": 0,
            "analyzing": 1, "reading": 1,
            "identified": 2,
            "route": 3, "review": 3,
            "date": 4,
            "duplicate": 5, "duplicate_found": 5,
            "filing": 6,
            "complete": 7,
        }
        return mapping.get(stage, self.raily_stage_index)

    def _update_raily_preview(self, path):
        try:
            p = Path(path)
            if not p.exists():
                return
            # v71.2: fit the preview to the actual Dispatch Center space instead
            # of forcing a 535px-tall image that can push lower panels off-screen.
            self.raily_preview_label.update_idletasks()
            compact = bool(getattr(self, "compact_ui", False))
            available_w = int(self.raily_preview_label.winfo_width() or 1)
            available_h = int(self.raily_preview_label.winfo_height() or 1)
            fallback_w = 500 if compact else 610
            fallback_h = 275 if compact else 430
            max_width = max(280, min(fallback_w, available_w - 12 if available_w > 80 else fallback_w))
            max_height = max(170, min(fallback_h, available_h - 12 if available_h > 80 else fallback_h))
            img = render_document_preview(
                p, page_index=0, max_width=max_width, max_height=max_height
            )
            if img is None:
                return
            photo = ImageTk.PhotoImage(img)
            self.raily_preview_photo = photo
            self.raily_preview_label.configure(image=photo, text="")
        except Exception:
            pass

    def _append_raily_activity(self, text):
        widget = getattr(self, "raily_activity", None)
        if widget is None:
            return
        try:
            widget.configure(state="normal")
            widget.insert("end", f"{datetime.now():%I:%M:%S %p}  {text}\n")
            # Keep the feed fast even after long runs.
            lines = int(widget.index("end-1c").split(".")[0])
            if lines > 180:
                widget.delete("1.0", "40.0")
            widget.see("end")
            widget.configure(state="disabled")
        except Exception:
            pass

    def raily_event(self, stage, path="", data=None):
        data = dict(data or {})

        def apply():
            try:
                self.raily_stage_index = self._raily_stage_position(stage)
                self._draw_raily_track()

                signal = data.get("signal", "yellow")
                if stage == "complete":
                    signal = "green"
                elif stage in {"review", "duplicate_found"}:
                    signal = "yellow"
                self._set_raily_signal(signal)

                stage_text = {
                    "received": "RAILY received a new document",
                    "splitting": "RAILY is separating the scanner stack",
                    "analyzing": "RAILY is analyzing the entire page",
                    "reading": "RAILY is reading supporting text and fields",
                    "identified": "RAILY identified the document",
                    "route": "RAILY is building the railroad route",
                    "date": "RAILY is confirming the document date",
                    "duplicate": "RAILY is checking the black box for duplicates",
                    "duplicate_found": "RAILY stopped a possible duplicate for review",
                    "review": "RAILY needs Conductor Review",
                    "filing": "RAILY is routing the document into the yard",
                    "complete": "ROUTE CLEAR — document filed",
                }.get(stage, f"RAILY: {stage}")
                self.raily_stage_var.set(stage_text)

                if path:
                    self.raily_current_path = path
                    self.raily_document_var.set(Path(path).name)
                    if stage in {"received", "analyzing", "reading", "identified", "review"}:
                        self._update_raily_preview(path)

                family = data.get("family") or data.get("document_type")
                if family:
                    self.raily_type_var.set(str(family))
                if data.get("railroad"):
                    self.raily_railroad_var.set(str(data["railroad"]))
                if data.get("location"):
                    self.raily_location_var.set(str(data["location"]))
                if data.get("person"):
                    self.raily_person_var.set(str(data["person"]))
                if data.get("date"):
                    self.raily_date_var.set(str(data["date"]))
                if data.get("confidence") not in {None, ""}:
                    self.raily_confidence_var.set(f"{int(float(data['confidence']))}%")
                if data.get("source"):
                    self.raily_source_var.set(str(data["source"]))

                railroad = data.get("railroad") or self.raily_railroad_var.get()
                location = data.get("location") or self.raily_location_var.get()
                if railroad not in {"", "—"} or location not in {"", "—"}:
                    self.raily_route_var.set(
                        f"{railroad if railroad not in {'', '—'} else 'Railroad ?'}  →  "
                        f"{location if location not in {'', '—'} else 'Location ?'}"
                    )
                if data.get("destination"):
                    self.raily_destination_var.set(str(data["destination"]))

                detail = ""
                if family:
                    detail = f" — {family}"
                if stage == "review" and data.get("reason"):
                    detail = f" — {data.get('reason')}"
                self._append_raily_activity(stage_text + detail)
                self._refresh_raily_switch_list(reschedule=False)
            except Exception:
                pass

        try:
            self.root.after(0, apply)
        except Exception:
            pass

    def _refresh_raily_switch_list(self, reschedule=True):
        widget = getattr(self, "raily_switch_list", None)
        if widget is None:
            return
        try:
            widget.delete(0, "end")
            active = getattr(self.engine, "_active_processing_path", None)
            if active:
                widget.insert("end", f"● ON TRACK   {Path(active).name}")

            with self.engine._process_queue_lock:
                queued = list(self.engine._process_queue)
            for i, item in enumerate(queued[:8], start=1):
                widget.insert("end", f"○ WAIT {i:02d}    {Path(item.get('path', '')).name}")

            if self.teach_window_open:
                widget.insert("end", "! CONDUCTOR REVIEW OPEN")
            for item in self.teach_queue[:5]:
                widget.insert("end", f"! REVIEW     {Path(item.get('path', '')).name}")

            if widget.size() == 0:
                widget.insert("end", "✓ Yard clear — no documents waiting")
        except Exception:
            pass
        if reschedule:
            try:
                self.root.after(1200, self._refresh_raily_switch_list)
            except Exception:
                pass

    # ----------------------------------------------------------------------
    # Settings / activity
    # ----------------------------------------------------------------------

    def sync(self):
        for key, var in self.path_vars.items():
            CFG[key] = var.get().strip()

        CFG["category_threshold"] = int(
            self.category_threshold.get()
        )

        CFG["rename_by_type"] = bool(
            self.rename_by_type.get()
        )

        CFG["family_subfolders"] = bool(
            self.family_subfolders.get()
        )

        CFG["ask_to_teach_unknown"] = bool(
            self.ask_to_teach_unknown.get()
        )

        CFG["printed_structure_learning"] = bool(
            self.printed_structure.get()
        )

        CFG["auto_apply_new_learning_to_queue"] = bool(
            self.auto_apply_queue.get()
        )

        CFG["auto_split_combined_scans"] = bool(
            self.auto_split_combined.get()
        )

        CFG["fast_stack_mode"] = bool(
            self.fast_stack_mode.get()
        )

        CFG["incoming_one_at_a_time"] = bool(
            self.incoming_one_at_a_time.get()
        )

        CFG["detect_duplicates"] = bool(
            self.detect_duplicates.get()
        )

        CFG["date_aware_duplicates"] = bool(
            self.date_aware_duplicates.get()
        )

        CFG["family_history_duplicates"] = bool(
            self.family_history_duplicates.get()
        )

        CFG["handwritten_date_mode"] = bool(
            self.handwritten_date_mode.get()
        )

        CFG["date_subcrop_search"] = bool(
            self.date_subcrop_search.get()
        )

        CFG["fast_post_recognition"] = bool(
            self.fast_post_recognition.get()
        )

        CFG["pause_when_required_date_missing"] = bool(
            self.pause_required_date.get()
        )

        CFG["add_date_to_filename"] = bool(
            self.add_date.get()
        )

        CFG["date_confidence_threshold"] = int(
            self.date_threshold.get()
        )

        CFG["auto_start_sorting_on_launch"] = bool(
            self.auto_start_sorting.get()
        )

        CFG["automatic_state_backups"] = bool(
            self.automatic_state_backups.get()
        )
        CFG["minimize_to_tray"] = bool(self.minimize_to_tray.get())
        CFG["auto_confidence_growth"] = bool(self.auto_confidence_growth.get())
        CFG["date_area_recovery"] = bool(self.date_area_recovery.get())

        # v71 RAILY settings
        if hasattr(self, "raily_visual_ai_var"):
            CFG["raily_visual_ai_enabled"] = bool(self.raily_visual_ai_var.get())
            CFG["raily_visual_autofile_threshold"] = int(self.raily_visual_threshold_var.get())
            CFG["raily_route_filing"] = bool(self.raily_route_filing_var.get())
            CFG["raily_require_railroad_location"] = bool(self.raily_require_route_var.get())
            CFG["raily_include_person_in_filename"] = bool(self.raily_person_filename_var.get())
            CFG["raily_ai_provider"] = self.raily_ai_provider_var.get().strip() or "Local Full-Page"
            CFG["raily_vision_api_url"] = self.raily_vision_url_var.get().strip()
            CFG["raily_vision_model"] = self.raily_vision_model_var.get().strip()
            CFG["raily_vision_api_key_env"] = self.raily_vision_key_env_var.get().strip() or "RAILY_VISION_API_KEY"
            CFG["raily_update_mode"] = self.raily_update_mode_var.get().strip() or "Recommended"
            CFG["raily_update_manifest_url"] = self.raily_update_manifest_var.get().strip()
            CFG["raily_update_only_when_idle"] = bool(self.raily_update_idle_var.get())

        for key in (
            "incoming",
            "sorted",
            "review",
            "duplicates"
        ):
            Path(CFG[key]).mkdir(
                parents=True,
                exist_ok=True
            )

        save_config()
        self._refresh_raily_watch_folder()

    def clear_activity(self):
        self.activity.delete(
            "1.0",
            "end"
        )
        self.activity_status.set("Ready")
        self.app_status.set("Ready")

    def copy_activity(self):
        text = self.activity.get(
            "1.0",
            "end-1c"
        )

        self.root.clipboard_clear()
        self.root.clipboard_append(text)

        self.activity_status.set(
            "Activity copied"
        )

    def note(self, msg):
        def add():
            raw = str(msg or "").strip()
            low = raw.lower()

            if "error" in low or "failed" in low:
                tag = "error"
                state = "Needs attention"

            elif "paused" in low or "teaching queue" in low:
                tag = "paused"
                state = "Teaching"

            elif "warning" in low or "duplicate" in low:
                tag = "warning"
                state = "Review recommended"

            elif (
                "reading document" in low
                or "scanning" in low
                or "re-checking" in low
            ):
                tag = "processing"
                state = "Processing"

            elif (
                "filed successfully" in low
                or "ready" in low
                or "result:" in low
                or "complete" in low
            ):
                tag = "success"
                state = "Ready"

            else:
                tag = "info"
                state = "Active"

            if "filed successfully" in low:
                self.session_sorted_count += 1
                if hasattr(self, "session_sorted_var"):
                    self.session_sorted_var.set(str(self.session_sorted_count))

            if "duplicate moved to review" in low:
                self.session_duplicate_count += 1
                if hasattr(self, "session_duplicate_var"):
                    self.session_duplicate_var.set(str(self.session_duplicate_count))

            if "paused for teaching" in low:
                self.session_teach_count += 1
                if hasattr(self, "session_teach_var"):
                    self.session_teach_var.set(str(self.session_teach_count))

            if "stable backup created" in low:
                self.session_backup_count += 1
                if hasattr(self, "session_backup_var"):
                    self.session_backup_var.set(str(self.session_backup_count))

            timestamp = datetime.now().strftime(
                "%I:%M:%S %p"
            )

            self.activity.insert(
                "end",
                timestamp + "   ",
                "time"
            )

            self.activity.insert(
                "end",
                raw + "\n",
                tag
            )

            self.activity.see("end")

            if hasattr(self, "raily_activity"):
                self._append_raily_activity(raw)

            self.activity_status.set(state)
            self.app_status.set(state)
            self.refresh_persistent_dashboard()

        try:
            self.root.after(
                0,
                add
            )
        except Exception:
            pass

    # ----------------------------------------------------------------------
    # Basic actions
    # ----------------------------------------------------------------------

    def browse_folder(self, key):
        folder = filedialog.askdirectory(
            initialdir=self.path_vars[key].get()
        )

        if folder:
            self.path_vars[key].set(folder)

    def open_folder(self, path):
        folder = Path(path)
        folder.mkdir(
            parents=True,
            exist_ok=True
        )
        os.startfile(str(folder))


    def start(self):
        self.sync()
        self.engine.start()
        self.app_status.set("RAILY Active")
        self.activity_status.set("RAILY is watching Incoming")
        self._set_raily_signal("green", "SIGNAL: CLEAR / WATCHING")
        self._refresh_raily_watch_folder()
        if hasattr(self, "raily_stage_var"):
            self.raily_stage_var.set(f"RAILY is watching: {CFG.get('incoming', '')}")


    def stop(self):
        self.engine.stop()
        self.app_status.set("Stopped")
        self.activity_status.set("Automatic sorting stopped")
        self._set_raily_signal("red", "SIGNAL: STOPPED")
        if hasattr(self, "raily_stage_var"):
            self.raily_stage_var.set("RAILY is stopped")


    def sort_existing(self):
        self.sync()

        # v47: Sort Existing also guarantees the live watcher is online.
        # Older builds set engine.running=True without starting a watcher,
        # which made the Start button think auto-sort was active when it wasn't.
        if (
            not self.engine.running
            or not self.engine._watcher_is_alive()
        ):
            self.engine.start()
        else:
            self.note(
                "Sorting current Incoming documents; live watcher remains online."
            )
            threading.Thread(
                target=self.engine.scan_incoming,
                daemon=True,
                name="SmartScanManualIncomingScan"
            ).start()

    # ----------------------------------------------------------------------
    # Teach queue
    # ----------------------------------------------------------------------



    def request_teach(self, path, reason, category="", family=""):
        self.engine.set_teach_blocked(True)

        def enqueue():
            p = Path(path)

            if not p.exists():
                self.engine.set_teach_blocked(False)
                return

            try:
                key = str(p.resolve()).lower()
            except Exception:
                key = str(p).lower()

            updated = False

            for item in self.teach_queue:
                try:
                    item_key = str(
                        Path(item["path"]).resolve()
                    ).lower()
                except Exception:
                    item_key = str(
                        item.get("path", "")
                    ).lower()

                if item_key == key:
                    item["reason"] = reason
                    item["category"] = category
                    item["family"] = family
                    # Keep any individual_teach/split metadata already present.
                    updated = True
                    break

            current_key = ""

            if self.current_teach_path:
                try:
                    current_key = str(
                        Path(self.current_teach_path).resolve()
                    ).lower()
                except Exception:
                    current_key = str(
                        self.current_teach_path
                    ).lower()

            if (
                not updated
                and key != current_key
                and key not in self.teach_queue_keys
            ):
                self.teach_queue.append({
                    "path": str(p),
                    "reason": reason,
                    "category": category,
                    "family": family,
                })
                self.teach_queue_keys.add(key)

            self._update_queue_status()
            self._open_next_teach()

        self.root.after(0, enqueue)

    def _update_queue_status(self):
        waiting = len(self.teach_queue)

        if self.teach_window_open:
            text = (
                f"Teaching now • {waiting} waiting"
            )
        elif waiting:
            text = (
                f"{waiting} document(s) waiting for teaching"
            )
        else:
            text = "Switch List clear"

        if hasattr(
            self,
            "teach_queue_status"
        ):
            self.teach_queue_status.set(text)




    def _open_next_teach(self):
        if self.teach_window_open:
            self.engine.set_teach_blocked(True)
            self._update_queue_status()
            return

        while self.teach_queue:
            item = self.teach_queue.pop(0)
            path = item["path"]

            try:
                key = str(
                    Path(path).resolve()
                ).lower()
            except Exception:
                key = str(path).lower()

            self.teach_queue_keys.discard(
                key
            )

            if not Path(path).exists():
                continue

            self.current_teach_path = path
            self.engine.set_teach_blocked(True)
            self._update_queue_status()

            self.teach_file(
                preselected=path,
                reason=item.get("reason", ""),
                from_queue=True,
                initial_category=item.get("category", ""),
                initial_family=item.get("family", ""),
                individual_teach=bool(
                    item.get("individual_teach", False)
                ),
                split_index=item.get("split_index"),
                split_total=item.get("split_total"),
                split_session=item.get("split_session", ""),
            )
            return

        self.current_teach_path = None
        self.engine.set_teach_blocked(False)
        self._update_queue_status()

    def _queued_and_paused_paths(self):
        paths = []

        if self.current_teach_path:
            paths.append(
                self.current_teach_path
            )

        for item in self.teach_queue:
            paths.append(
                item["path"]
            )

        # Also include paused Incoming files that may not currently be represented
        # in the visible queue.
        incoming = Path(CFG["incoming"])

        if incoming.exists():
            for p in incoming.iterdir():
                if (
                    p.is_file()
                    and p.suffix.lower() in SUPPORTED
                    and self.engine.key(p) in self.engine.paused
                ):
                    paths.append(str(p))

        # Deduplicate paths.
        output = []
        seen = set()

        for raw in paths:
            try:
                key = str(
                    Path(raw).resolve()
                ).lower()
            except Exception:
                key = str(raw).lower()

            if key not in seen:
                seen.add(key)
                output.append(raw)

        return output

    def _remove_queue_paths(self, processed_paths):
        processed_keys = set()

        for raw in processed_paths:
            try:
                processed_keys.add(
                    str(Path(raw).resolve()).lower()
                )
            except Exception:
                processed_keys.add(
                    str(raw).lower()
                )

        new_queue = []

        for item in self.teach_queue:
            raw = item["path"]

            try:
                key = str(
                    Path(raw).resolve()
                ).lower()
            except Exception:
                key = str(raw).lower()

            if key in processed_keys:
                self.teach_queue_keys.discard(key)
                continue

            new_queue.append(item)

        self.teach_queue = new_queue
        self._update_queue_status()


    def _finish_teach_window(self):
        self.teach_window_open = False
        self.current_teach_path = None
        self._update_queue_status()

        if self.teach_queue:
            self.engine.set_teach_blocked(True)
            self.root.after(
                150,
                self._open_next_teach
            )
        else:
            self.engine.set_teach_blocked(False)

    # ----------------------------------------------------------------------
    # Teach window
    # v70: full-screen/maximized teaching workspace + anti-overlap row sizing
    # ----------------------------------------------------------------------

    def teach_file(
        self,
        preselected=None,
        reason="",
        from_queue=False,
        initial_category="",
        initial_family="",
        individual_teach=False,
        split_index=None,
        split_total=None,
        split_session=""
    ):
        self.sync()

        path = preselected or filedialog.askopenfilename(
            title="Choose document to teach",
            initialdir=CFG["incoming"],
            filetypes=[
                (
                    "Supported documents",
                    "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"
                ),
                ("All files", "*.*"),
            ]
        )

        if not path:
            if from_queue:
                self._finish_teach_window()
            return

        p = Path(path)

        if not p.exists():
            messagebox.showerror(
                "SmartScan",
                "That file no longer exists."
            )

            if from_queue:
                self._finish_teach_window()

            return

        if not isinstance(_OCR_CACHE.get(file_cache_key(p)), StructuredOCRText):
            if getattr(self, "_teach_ocr_pending", False):
                return
            self._teach_ocr_pending = True
            self.teach_window_open = True
            self.engine.set_teach_blocked(True)
            self.activity_status.set("Reading structured OCR in background...")
            def prepare_teach():
                try:
                    get_cached_ocr(p)
                    printed_structure_fingerprint(p)
                    error = None
                except Exception as exc:
                    error = str(exc)
                def ready():
                    self._teach_ocr_pending = False
                    if error:
                        messagebox.showerror("OCR Error", error, parent=self.root)
                        self._finish_teach_window()
                    else:
                        self.teach_file(preselected=p, reason=reason, from_queue=from_queue,
                                        initial_category=initial_category, initial_family=initial_family,
                                        individual_teach=individual_teach, split_index=split_index,
                                        split_total=split_total, split_session=split_session)
                try:
                    self.root.after(0, ready)
                except Exception:
                    pass
            threading.Thread(target=prepare_teach, daemon=True, name="RAILYStructuredTeachOCR").start()
            return

        self.teach_window_open = True
        self.current_teach_path = str(p)
        self.engine.set_teach_blocked(True)
        self._update_queue_status()

        self.activity_status.set(
            "Opening Teach window"
        )

        C = self.ui_colors

        win = tk.Toplevel(self.root)
        win.title(
            "RAILY — Conductor Review"
        )
        win.configure(
            bg=C["bg"]
        )
        win.transient(
            self.root
        )

        width, height = _fit_learning_window(win, 1240, 820)
        teach_content, teach_footer = _learning_form(win)
        compact_teach = win._learning_compact

        def skip_for_now():
            self.note(
                f"Teaching postponed: {p.name} remains paused in Incoming."
            )

            win.destroy()
            self._finish_teach_window()

        win.protocol(
            "WM_DELETE_WINDOW",
            skip_for_now
        )

        # Header
        header = tk.Frame(
            teach_content,
            bg=C["navy"],
            padx=16,
            pady=10
        )
        header.grid(
            row=0,
            column=0,
            sticky="ew"
        )

        tk.Label(
            header,
            text="RAILY Conductor Review",
            bg=C["navy"],
            fg="white",
            font=("Segoe UI", 15, "bold")
        ).pack(
            anchor="w"
        )

        tk.Label(
            header,
            text=p.name,
            bg=C["navy"],
            fg="#DCEAFF",
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=max(680, width - 80)
        ).pack(
            anchor="w",
            fill="x",
            pady=(2, 0)
        )

        teach_badge_text = (
            (
                f"Split Document {split_index} of {split_total} • "
                f"{len(self.teach_queue)} waiting after this document"
            )
            if (
                individual_teach
                and split_index
                and split_total
            )
            else (
                f"Teaching Queue • {len(self.teach_queue)} waiting after this document"
                if from_queue
                else "Manual teaching"
            )
        )

        tk.Label(
            header,
            text=teach_badge_text,
            bg=C["navy2"],
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=7,
            pady=3
        ).pack(
            anchor="w",
            pady=(6, 0)
        )

        # Attention
        attention_holder = ttk.Frame(teach_content)
        attention_holder.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=14,
            pady=(8, 4)
        )

        if reason:
            attention = tk.Frame(
                attention_holder,
                bg="#FDECEC",
                highlightbackground="#F2B8B5",
                highlightthickness=1,
                padx=10,
                pady=6
            )
            attention.pack(
                fill="x"
            )

            tk.Label(
                attention,
                text="Needs Attention",
                bg="#FDECEC",
                fg=C["error"],
                font=("Segoe UI", 9, "bold")
            ).pack(
                anchor="w"
            )

            tk.Label(
                attention,
                text=reason,
                bg="#FDECEC",
                fg="#7F1D1D",
                font=("Segoe UI", 9),
                wraplength=max(620, width - 90),
                justify="left",
                anchor="w"
            ).pack(
                anchor="w",
                fill="x",
                pady=(2, 1)
            )

        # Recognition
        try:
            text = get_cached_ocr(p)
        except Exception as exc:
            messagebox.showerror(
                "OCR Error",
                str(exc),
                parent=win
            )

            win.destroy()
            self._finish_teach_window()
            return

        guessed_category, category_conf, _ = classify(
            text,
            classification_filename_for(p)
        )

        guessed_family, family_conf, family_source = resolve_family(
            guessed_category,
            text,
            p
        )

        resolved_category = (
            clean_name(initial_category)
            if initial_category in CATEGORY_RULES
            else guessed_category
        )

        resolved_family = (
            clean_name(initial_family)
            if initial_family
            else guessed_family
        )

        struct_fp = printed_structure_fingerprint(
            p,
            text
        )

        teach_visual_match = raily_match_visual_document(p)
        initial_metadata = dict(
            self.engine.last_metadata_by_path.get(self.engine.key(p), {})
            or raily_extract_metadata(text, visual_match=teach_visual_match)
        )

        # v54 FAST TEACH:
        # Do NOT run image/date-area OCR before drawing the Teach window.
        # First do a cheap text/label-only pass, then check taught date areas
        # on a background thread after the window is visible.
        preview_date, preview_date_conf, preview_date_label, preview_date_source = detect_family_date(
            resolved_category,
            resolved_family,
            text,
            path=None
        )

        preview_date_state = {
            "date": preview_date,
            "confidence": preview_date_conf,
            "label": preview_date_label,
            "source": preview_date_source,
            "busy": False,
            "finished": bool(preview_date),
        }

        # Spatial date-teaching state. The region is not written to learning
        # until Save Learning & File is clicked.
        pending_date_regions = {"value": []}
        clear_date_region_requested = {"value": False}
        date_selection_mode = {"value": False}
        date_drag_start = {"value": None}
        date_drag_rect = {"value": None}
        preview_geometry = {
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "page": 0,
        }

        card = ttk.LabelFrame(
            teach_content,
            text="Document Classification",
            style="Card.TLabelframe"
        )
        card.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=14,
            pady=(2, 6)
        )

        card.columnconfigure(
            0,
            minsize=132
        )
        card.columnconfigure(
            1,
            weight=1
        )

        # v70: never allow wrapped helper/status labels to be compressed
        # into the next row.  These minimums are deliberately a little
        # generous because Windows display scaling can increase font height.
        card.grid_rowconfigure(0, minsize=30, pad=2)
        card.grid_rowconfigure(1, minsize=34, pad=2)
        card.grid_rowconfigure(2, minsize=34, pad=2)
        card.grid_rowconfigure(3, minsize=38, pad=3)
        card.grid_rowconfigure(4, minsize=38, pad=3)
        card.grid_rowconfigure(5, minsize=38, pad=3)
        card.grid_rowconfigure(6, minsize=44, pad=3)
        card.grid_rowconfigure(7, minsize=32, pad=2)

        ttk.Label(
            card,
            text="RAILY guess",
            style="Section.TLabel"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=3
        )

        ttk.Label(
            card,
            text=(
                f"{guessed_category} ({category_conf}%) • "
                f"{guessed_family} ({family_conf}%, {family_source})"
            ),
            wraplength=max(500, width - 250)
        ).grid(
            row=0,
            column=1,
            sticky="w",
            pady=3
        )

        ttk.Label(
            card,
            text="Category",
            style="Section.TLabel"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=4
        )

        category_var = tk.StringVar(
            value=(
                resolved_category
                if resolved_category in CATEGORY_RULES
                else guessed_category
            )
        )

        def category_choices():
            return sorted(
                CATEGORY_RULES,
                key=str.casefold
            ) + [NEW_CATEGORY_OPTION]

        category_combo = ttk.Combobox(
            card,
            textvariable=category_var,
            values=category_choices(),
            state="readonly"
        )
        category_combo.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=4
        )

        ttk.Label(
            card,
            text="Document type",
            style="Section.TLabel"
        ).grid(
            row=2,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=4
        )

        family_var = tk.StringVar(
            value=resolved_family
        )

        def family_choices(selected_category=None):
            selected_category = clean_optional_name(
                selected_category
                if selected_category is not None
                else category_var.get()
            )

            names = []

            for name, _terms in STANDARD_FAMILIES.get(
                selected_category,
                []
            ):
                cleaned = clean_optional_name(name)
                if cleaned:
                    names.append(cleaned)

            for profile in LEARNED.get("profiles", []):
                if profile.get("category") != selected_category:
                    continue

                for key in ("parent_family", "family"):
                    cleaned = clean_optional_name(
                        profile.get(key, "")
                    )
                    if cleaned:
                        names.append(cleaned)

            fallback_name = clean_optional_name(
                GENERIC_FAMILY.get(
                    selected_category,
                    selected_category
                )
            )
            if fallback_name:
                names.append(fallback_name)

            unique = []
            seen = set()

            for name in sorted(names, key=str.casefold):
                marker = name.casefold()
                if marker in seen:
                    continue
                seen.add(marker)
                unique.append(name)

            unique.append(NEW_FAMILY_OPTION)
            return unique

        family_combo = ttk.Combobox(
            card,
            textvariable=family_var,
            values=family_choices(category_var.get()),
            state="normal"
        )
        family_combo.grid(
            row=2,
            column=1,
            sticky="ew",
            pady=4
        )

        def refresh_learn_menu_values():
            category_combo.configure(
                values=category_choices()
            )
            family_combo.configure(
                values=family_choices(
                    category_var.get()
                )
            )

        def create_new_document_type(parent=win):
            selected_category = clean_optional_name(
                category_var.get()
            )

            if (
                not selected_category
                or selected_category == NEW_CATEGORY_OPTION
                or selected_category not in CATEGORY_RULES
            ):
                messagebox.showinfo(
                    "New Document Type",
                    "Choose or create a Category first.",
                    parent=parent
                )
                return ""

            typed = simpledialog.askstring(
                "New Document Type",
                (
                    f"Create a document type under {selected_category}.\n\n"
                    "Examples: LENTEGRITY, Utility Disconnect Notice, "
                    "Hotel Folio, Insurance Renewal"
                ),
                parent=parent
            )

            cleaned = clean_optional_name(typed)

            if not cleaned:
                family_var.set("")
                return ""

            existing_lookup = {
                clean_optional_name(item).casefold(): clean_optional_name(item)
                for item in family_choices(selected_category)
                if item != NEW_FAMILY_OPTION
                and clean_optional_name(item)
            }

            existing = existing_lookup.get(
                cleaned.casefold()
            )

            if existing:
                family_var.set(existing)
                messagebox.showinfo(
                    "Document Type Already Exists",
                    (
                        f"{existing} is already in {selected_category}.\n\n"
                        "This scan will be added as another learning example "
                        "when you click Save Learning & File."
                    ),
                    parent=parent
                )
                return existing

            family_var.set(cleaned)
            refresh_learn_menu_values()

            # Keep the newly typed item visible immediately in this window,
            # even though it is not persisted until Save Learning & File.
            current_values = [
                item
                for item in family_combo.cget("values")
                if item != NEW_FAMILY_OPTION
            ]
            current_values.append(cleaned)

            unique_values = []
            seen_values = set()
            for item in sorted(current_values, key=str.casefold):
                marker = str(item).casefold()
                if marker in seen_values:
                    continue
                seen_values.add(marker)
                unique_values.append(item)

            unique_values.append(NEW_FAMILY_OPTION)
            family_combo.configure(
                values=unique_values
            )
            family_combo.set(cleaned)
            family_combo.focus_set()

            return cleaned

        def create_new_category(parent=win):
            typed = simpledialog.askstring(
                "New Category",
                (
                    "Create a new SmartScan filing category.\n\n"
                    "Examples: Loans, Utilities, Certifications, Travel"
                ),
                parent=parent
            )

            cleaned = clean_optional_name(typed)

            if not cleaned:
                previous = (
                    resolved_category
                    if resolved_category in CATEGORY_RULES
                    else guessed_category
                )
                category_var.set(previous)
                refresh_learn_menu_values()
                return ""

            existing = next(
                (
                    name
                    for name in CATEGORY_RULES
                    if name.casefold() == cleaned.casefold()
                ),
                ""
            )

            if existing:
                category_var.set(existing)
            else:
                category_var.set(
                    register_custom_category(
                        cleaned,
                        persist=True
                    )
                )

            family_var.set("")
            refresh_learn_menu_values()

            messagebox.showinfo(
                "Category Ready",
                (
                    f"{category_var.get()} was added to SmartScan.\n\n"
                    "Now choose + New Document Type... and name the "
                    "document you are teaching."
                ),
                parent=parent
            )

            family_combo.focus_set()
            return category_var.get()

        def refresh_family_after_category_change(event=None):
            selected_category = category_var.get().strip()

            if selected_category == NEW_CATEGORY_OPTION:
                create_new_category()
                return

            if selected_category not in CATEGORY_RULES:
                return

            refresh_learn_menu_values()

            allowed_lookup = {
                clean_optional_name(name).casefold(): clean_optional_name(name)
                for name in family_choices(selected_category)
                if name != NEW_FAMILY_OPTION
                and clean_optional_name(name)
            }

            current = clean_optional_name(
                family_var.get()
            )
            current_key = current.casefold() if current else ""

            if current_key not in allowed_lookup:
                corrected_family, _, _ = resolve_family(
                    selected_category,
                    text,
                    p
                )
                family_var.set(corrected_family)

            redraw = locals().get("redraw_overlays")
            if callable(redraw):
                try:
                    redraw()
                except Exception:
                    pass

        def on_family_combo_selected(event=None):
            if family_var.get().strip() == NEW_FAMILY_OPTION:
                create_new_document_type()

        category_combo.bind(
            "<<ComboboxSelected>>",
            refresh_family_after_category_change
        )

        family_combo.bind(
            "<<ComboboxSelected>>",
            on_family_combo_selected
        )

        ttk.Label(
            card,
            text="Printed structure: " + structure_summary(struct_fp),
            style="Hint.TLabel",
            wraplength=max(620, width - 190),
            justify="left",
            anchor="w"
        ).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 5)
        )

        date_is_required = family_requires_date(
            resolved_category,
            resolved_family
        )

        if preview_date:
            date_preview_text = (
                f"Safe date preview: {preview_date:%Y-%m-%d} "
                f"from '{preview_date_label}' ({preview_date_conf}%)"
            )
        elif date_is_required:
            date_preview_text = (
                "Checking learned date areas in background — Teach window is ready to use."
            )
        else:
            date_preview_text = (
                "Safe date preview: no trusted date found. This family is not yet date-required."
            )

        date_preview_var = tk.StringVar(
            value=date_preview_text
        )

        ttk.Label(
            card,
            textvariable=date_preview_var,
            style="Hint.TLabel",
            wraplength=max(620, width - 190),
            justify="left",
            anchor="w"
        ).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 5)
        )

        # v43 manual date override. This value belongs to THIS document only.
        remembered_manual_date, remembered_manual_entry = get_saved_manual_date(p)

        manual_date_var = tk.StringVar(
            value=(
                remembered_manual_date.strftime("%m/%d/%Y")
                if remembered_manual_date
                else ""
            )
        )

        manual_date_row = ttk.Frame(card)
        manual_date_row.grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 2)
        )
        manual_date_row.columnconfigure(1, weight=1)

        ttk.Label(
            manual_date_row,
            text="Manual date",
            style="Section.TLabel"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8)
        )

        manual_date_entry = ttk.Entry(
            manual_date_row,
            textvariable=manual_date_var,
            width=22
        )
        manual_date_entry.grid(
            row=0,
            column=1,
            sticky="ew"
        )

        def use_detected_date():
            detected = preview_date_state.get(
                "date"
            )

            if not detected:
                if preview_date_state.get("busy"):
                    messagebox.showinfo(
                        "Use Detected Date",
                        "SmartScan is still checking the learned date areas in the background.",
                        parent=win
                    )
                else:
                    messagebox.showinfo(
                        "Use Detected Date",
                        "SmartScan does not currently have a trusted detected date for this document.",
                        parent=win
                    )
                return

            manual_date_var.set(
                detected.strftime("%m/%d/%Y")
            )

        use_detected_button = ttk.Button(
            manual_date_row,
            text="Use Detected",
            style="Small.TButton",
            command=use_detected_date
        )
        use_detected_button.grid(
            row=0,
            column=2,
            padx=(6, 3)
        )

        # Enabled immediately only when the cheap label-only pass already
        # found a date. Background learned-area OCR can enable it later.
        if not preview_date_state.get("date"):
            use_detected_button.state(["disabled"])

        ttk.Button(
            manual_date_row,
            text="Clear",
            style="Small.TButton",
            command=lambda: manual_date_var.set("")
        ).grid(
            row=0,
            column=3,
            padx=(3, 0)
        )

        ttk.Label(
            card,
            text=(
                "Saved to this exact document fingerprint — never copied to other files. Examples: "
                "6/17/26, 06-17-2026, 2026-06-17, June 17, 2026."
            ),
            style="Hint.TLabel",
            wraplength=max(620, width - 190),
            justify="left",
            anchor="w"
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 6)
        )

        file_now = tk.BooleanVar(
            value=True
        )

        ttk.Checkbutton(
            card,
            text="File this document immediately after learning",
            variable=file_now
        ).grid(
            row=7,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 5)
        )

        # v71: simple route/identity correction is front-and-center. Only
        # Railroad and Location create folders; Person stays in the filename.
        route_card = ttk.LabelFrame(
            teach_content,
            text="RAILY Route & Identity",
            style="Card.TLabelframe"
        )
        route_card.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=14,
            pady=(0, 6)
        )
        for col in range(6):
            route_card.columnconfigure(col, weight=(1 if col in {1, 3, 5} else 0))

        railroad_var = tk.StringVar(value=initial_metadata.get("railroad", ""))
        location_var = tk.StringVar(value=initial_metadata.get("location", ""))
        person_var = tk.StringVar(value=initial_metadata.get("person", ""))

        ttk.Label(route_card, text="Railroad", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4
        )
        ttk.Combobox(
            route_card, textvariable=railroad_var,
            values=known_raily_entities("railroads"), state="normal"
        ).grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=4)

        ttk.Label(route_card, text="Location", style="Section.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 6), pady=4
        )
        ttk.Combobox(
            route_card, textvariable=location_var,
            values=known_raily_entities("locations"), state="normal"
        ).grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=4)

        ttk.Label(route_card, text="Primary person", style="Section.TLabel").grid(
            row=0, column=4, sticky="w", padx=(0, 6), pady=4
        )
        ttk.Combobox(
            route_card, textvariable=person_var,
            values=known_raily_entities("people"), state="normal"
        ).grid(row=0, column=5, sticky="ew", pady=4)

        ttk.Label(
            route_card,
            text=(
                "Filing route: Railroad → Location → documents. Person and document type are "
                "kept in the filename/metadata and do not create extra folders."
            ),
            style="Hint.TLabel",
            wraplength=max(680, width - 70)
        ).grid(row=1, column=0, columnspan=6, sticky="ew", pady=(1, 4))

        def start_background_date_preview():
            # No image OCR needed if the cheap printed-label pass already
            # found a trusted date.
            if preview_date_state.get("date"):
                return

            # Only learned dated families need the expensive spatial check.
            if not family_requires_date(
                resolved_category,
                resolved_family
            ):
                return

            if preview_date_state.get("busy"):
                return

            preview_date_state["busy"] = True

            date_preview_var.set(
                "Checking learned date areas in background... You can continue using Teach."
            )

            def worker():
                try:
                    detected, confidence, label, source = detect_family_date(
                        resolved_category,
                        resolved_family,
                        text,
                        path=p,
                        progress=lambda message: win.after(
                            0, lambda message=message: date_preview_var.set(message)
                        )
                    )
                    error_text = ""
                except Exception as exc:
                    detected = None
                    confidence = 0
                    label = "none"
                    source = "none"
                    error_text = str(exc)

                def finish():
                    try:
                        if not win.winfo_exists():
                            return
                    except Exception:
                        return

                    preview_date_state["busy"] = False
                    preview_date_state["finished"] = True
                    preview_date_state["date"] = detected
                    preview_date_state["confidence"] = confidence
                    preview_date_state["label"] = label
                    preview_date_state["source"] = source

                    if detected:
                        date_preview_var.set(
                            f"Safe date preview: {detected:%Y-%m-%d} "
                            f"from '{label}' ({confidence}%)"
                        )

                        use_detected_button.state(
                            ["!disabled"]
                        )

                    elif error_text:
                        date_preview_var.set(
                            "DATE REVIEW REQUIRED: background date check failed. "
                            "Use Teach Date Area or enter the date manually."
                        )

                    else:
                        date_preview_var.set(
                            "DATE REVIEW REQUIRED: document type is known, but "
                            "no learned date area produced a trusted date."
                        )

                        use_detected_button.state(
                            ["disabled"]
                        )

                try:
                    win.after(
                        0,
                        finish
                    )
                except Exception:
                    pass

            threading.Thread(
                target=worker,
                daemon=True,
                name="SmartScanTeachDatePreview"
            ).start()

        # Give Tk time to paint the complete Teach window first.
        win.after(
            125,
            start_background_date_preview
        )

        # Preview tabs
        preview_book = ttk.Notebook(teach_content, height=330 if compact_teach else 460)
        preview_book.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=14,
            pady=(0, 6)
        )

        document_tab = ttk.Frame(
            preview_book
        )

        learning_tab = ttk.Frame(
            preview_book
        )

        ocr_tab = ttk.Frame(
            preview_book
        )

        preview_book.add(
            document_tab,
            text="Document Preview"
        )

        preview_book.add(
            learning_tab,
            text="Learning Summary"
        )

        preview_book.add(
            ocr_tab,
            text="OCR Preview"
        )

        if reason and "date" in reason.lower():
            preview_book.select(document_tab)

        # Actual document preview + spatial date teaching
        toolbar = ttk.Frame(
            document_tab
        )
        toolbar.pack(
            fill="x",
            padx=8,
            pady=(7, 3)
        )

        total_pages = document_page_count(p)

        current_page = tk.IntVar(
            value=0
        )

        page_status = tk.StringVar(
            value=f"Page 1 of {total_pages}"
        )

        date_area_status = tk.StringVar()

        existing_profile = _family_profile(
            guessed_category,
            guessed_family
        )

        existing_regions = get_family_date_regions(
            existing_profile
        )

        if existing_regions:
            date_area_status.set(
                f"{len(existing_regions)} date area(s) already learned. "
                "Click Teach Date Area to add another location."
            )
        else:
            date_area_status.set(
                "No date areas taught yet. Click Teach Date Area to add the first location."
            )

        ttk.Label(
            toolbar,
            textvariable=page_status,
            style="Status.TLabel"
        ).pack(
            side="left"
        )

        preview_area = tk.Frame(
            document_tab,
            bg="#D7E3EF"
        )
        preview_area.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(0, 4)
        )

        preview_canvas = tk.Canvas(
            preview_area,
            bg="#D7E3EF",
            highlightthickness=0,
            cursor="arrow"
        )
        preview_canvas.pack(
            fill="both",
            expand=True
        )

        status_row = ttk.Frame(
            document_tab
        )
        status_row.pack(
            fill="x",
            padx=8,
            pady=(0, 7)
        )

        ttk.Label(
            status_row,
            textvariable=date_area_status,
            style="Hint.TLabel",
            wraplength=max(480, width - 250)
        ).pack(
            side="left",
            fill="x",
            expand=True
        )

        def draw_region_overlay(
            region,
            tag,
            outline,
            dash=None,
            width_px=2,
            label_text=""
        ):
            if not region:
                return

            if int(region.get("page", 0)) != current_page.get():
                return

            gx = preview_geometry

            x0 = gx["x"] + float(region.get("x0", 0.0)) * gx["w"]
            y0 = gx["y"] + float(region.get("y0", 0.0)) * gx["h"]
            x1 = gx["x"] + float(region.get("x1", 1.0)) * gx["w"]
            y1 = gx["y"] + float(region.get("y1", 1.0)) * gx["h"]

            preview_canvas.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                outline=outline,
                width=width_px,
                dash=dash,
                tags=(tag,)
            )

            if label_text:
                preview_canvas.create_text(
                    x0 + 3,
                    max(gx["y"] + 8, y0 - 7),
                    text=label_text,
                    anchor="sw",
                    fill=outline,
                    font=("Segoe UI", 8, "bold"),
                    tags=(tag,)
                )

        def redraw_overlays():
            preview_canvas.delete("learned_date_region")
            preview_canvas.delete("pending_date_region")

            profile_now = _family_profile(
                category_var.get().strip(),
                family_var.get().strip()
            )

            learned_regions = (
                get_family_date_regions(profile_now)
                if profile_now
                else []
            )

            if not clear_date_region_requested["value"]:
                for region in learned_regions:
                    draw_region_overlay(
                        region,
                        "learned_date_region",
                        "#D97706",
                        dash=(5, 3),
                        width_px=2,
                        label_text=(
                            f"P{region.get('priority', '?')} "
                            f"{region.get('name', 'Date Area')}"
                        )
                    )

            for region in pending_date_regions["value"]:
                draw_region_overlay(
                    region,
                    "pending_date_region",
                    "#16A34A",
                    dash=None,
                    width_px=3,
                    label_text=(
                        f"P{region.get('priority', '?')} "
                        f"{region.get('name', 'New Date Area')}"
                    )
                )

        def show_page(index):
            index = max(
                0,
                min(int(index), total_pages - 1)
            )

            current_page.set(index)

            page_status.set(
                f"Page {index + 1} of {total_pages}"
            )

            try:
                img = render_document_preview(
                    p,
                    index,
                    max_width=max(40, preview_canvas.winfo_width() - 20),
                    max_height=max(40, preview_canvas.winfo_height() - 20)
                )

                if img is None:
                    preview_canvas.delete("all")
                    preview_canvas.create_text(
                        20,
                        20,
                        anchor="nw",
                        text="Preview is unavailable.",
                        fill=C["muted"],
                        font=("Segoe UI", 9)
                    )
                    return

                photo = ImageTk.PhotoImage(img)

                preview_canvas.delete("all")
                preview_canvas.update_idletasks()

                canvas_width = max(
                    preview_canvas.winfo_width(),
                    img.width + 20
                )

                x = max(
                    8,
                    (canvas_width - img.width) // 2
                )
                y = 8

                preview_canvas.create_image(
                    x,
                    y,
                    image=photo,
                    anchor="nw",
                    tags=("document_image",)
                )

                preview_canvas.image = photo
                win._preview_photo = photo

                preview_geometry.update({
                    "x": x,
                    "y": y,
                    "w": img.width,
                    "h": img.height,
                    "page": index,
                })

                redraw_overlays()

            except Exception as exc:
                preview_canvas.delete("all")
                preview_canvas.create_text(
                    20,
                    20,
                    anchor="nw",
                    text=f"Preview unavailable: {exc}",
                    fill=C["error"],
                    font=("Segoe UI", 9)
                )

        previous_button = ttk.Button(
            toolbar,
            text="◀ Previous",
            style="Small.TButton",
            command=lambda: show_page(
                current_page.get() - 1
            )
        )
        previous_button.pack(
            side="right",
            padx=(4, 0)
        )

        next_button = ttk.Button(
            toolbar,
            text="Next ▶",
            style="Small.TButton",
            command=lambda: show_page(
                current_page.get() + 1
            )
        )
        next_button.pack(
            side="right",
            padx=(4, 0)
        )

        def open_date_area_trainer():
            """Large dedicated window for one isolated Category → Document Type."""
            trainer_category = clean_name(category_var.get().strip())
            trainer_family = clean_name(family_var.get().strip())

            if (
                trainer_category not in CATEGORY_RULES
                or not trainer_family
            ):
                messagebox.showerror(
                    "Date Profile",
                    "Choose the correct Category and Document type before teaching date locations.",
                    parent=win
                )
                return

            trainer = tk.Toplevel(win)
            trainer.title(
                f"SmartScan — Date Profile — {trainer_category} → {trainer_family}"
            )
            trainer.configure(bg=C["bg"])
            trainer.transient(win)

            tw, th = _fit_learning_window(trainer, 1240, 820)
            trainer_content, trainer_footer = _learning_form(trainer)
            compact_trainer = trainer._learning_compact

            # ---------------------------------------------------------
            # Header
            # ---------------------------------------------------------
            trainer_header = tk.Frame(
                trainer_content,
                bg=C["navy"],
                padx=16,
                pady=10
            )
            trainer_header.grid(
                row=0,
                column=0,
                sticky="ew"
            )

            tk.Label(
                trainer_header,
                text="Teach This Form's Date Locations",
                bg=C["navy"],
                fg="white",
                font=("Segoe UI", 15, "bold")
            ).pack(anchor="w")

            tk.Label(
                trainer_header,
                text=(
                    f"DATE PROFILE: {trainer_category} → {trainer_family}   •   "
                    "Only this exact document type can use these date areas."
                ),
                bg=C["navy"],
                fg="#93C5FD",
                font=("Segoe UI", 10, "bold"),
                justify="left",
                anchor="w",
                wraplength=max(760, tw - 80)
            ).pack(anchor="w", fill="x", pady=(3, 2))

            tk.Label(
                trainer_header,
                text=(
                    "Drag to add a date location, or click an existing colored box to select/manage it. "
                    "Other categories and document families cannot share these areas."
                ),
                bg=C["navy"],
                fg="#DCEAFF",
                font=("Segoe UI", 9),
                justify="left",
                anchor="w",
                wraplength=max(760, tw - 80)
            ).pack(anchor="w", fill="x", pady=(2, 1))

            # ---------------------------------------------------------
            # Toolbar
            # ---------------------------------------------------------
            trainer_toolbar = ttk.Frame(trainer_content)
            trainer_toolbar.grid(
                row=1,
                column=0,
                sticky="ew",
                padx=12,
                pady=(8, 5)
            )

            trainer_page = tk.IntVar(value=current_page.get())
            zoom_percent = tk.IntVar(value=100)
            trainer_page_text = tk.StringVar(
                value=f"Page {trainer_page.get() + 1} of {total_pages}"
            )

            ttk.Label(
                trainer_toolbar,
                textvariable=trainer_page_text,
                style="Status.TLabel"
            ).pack(side="left")

            ttk.Label(
                trainer_toolbar,
                text="Zoom"
            ).pack(side="left", padx=(20, 5))

            zoom_box = ttk.Combobox(
                trainer_toolbar,
                textvariable=zoom_percent,
                values=(75, 100, 125, 150, 175, 200, 250),
                state="readonly",
                width=6
            )
            zoom_box.pack(side="left")

            ttk.Label(
                trainer_toolbar,
                text="%"
            ).pack(side="left", padx=(3, 0))

            # ---------------------------------------------------------
            # Large scrollable canvas
            # ---------------------------------------------------------
            canvas_frame = tk.Frame(
                trainer_content,
                bg="#B7C6D4"
            )
            canvas_frame.grid(
                row=2,
                column=0,
                sticky="nsew",
                padx=12,
                pady=(0, 7)
            )
            canvas_frame.grid_columnconfigure(0, weight=1)
            canvas_frame.grid_rowconfigure(0, weight=1)

            hbar = ttk.Scrollbar(
                canvas_frame,
                orient="horizontal"
            )
            vbar = ttk.Scrollbar(
                canvas_frame,
                orient="vertical"
            )

            trainer_canvas = tk.Canvas(
                canvas_frame,
                bg="#B7C6D4",
                width=1, height=260 if compact_trainer else 420,
                highlightthickness=0,
                xscrollcommand=hbar.set,
                yscrollcommand=vbar.set,
                cursor="crosshair"
            )

            trainer_canvas.grid(
                row=0,
                column=0,
                sticky="nsew"
            )
            vbar.grid(
                row=0,
                column=1,
                sticky="ns"
            )
            hbar.grid(
                row=1,
                column=0,
                sticky="ew"
            )

            hbar.config(command=trainer_canvas.xview)
            vbar.config(command=trainer_canvas.yview)

            # ---------------------------------------------------------
            # Result panel
            # ---------------------------------------------------------
            result_frame = tk.Frame(
                trainer_content,
                bg=C["surface"],
                highlightbackground=C["border"],
                highlightthickness=1,
                padx=10,
                pady=7
            )
            result_frame.grid(
                row=3,
                column=0,
                sticky="ew",
                padx=12,
                pady=(0, 7)
            )
            result_frame.grid_columnconfigure(1, weight=1)

            result_status = tk.StringVar(
                value="Draw a box around the date value to test it."
            )

            result_label = tk.Label(
                result_frame,
                textvariable=result_status,
                bg=C["surface"],
                fg=C["navy"],
                font=("Segoe UI", 9, "bold"),
                anchor="w",
                justify="left",
                wraplength=max(650, tw - 220)
            )
            result_label.grid(
                row=0,
                column=0,
                columnspan=4,
                sticky="ew",
                pady=(0, 6)
            )

            ttk.Label(
                result_frame,
                text="Current document date",
                style="Section.TLabel"
            ).grid(
                row=1,
                column=0,
                sticky="w",
                padx=(0, 8)
            )

            trainer_manual_date = tk.StringVar(value=manual_date_var.get())

            trainer_manual_entry = ttk.Entry(
                result_frame,
                textvariable=trainer_manual_date,
                width=24
            )
            trainer_manual_entry.grid(
                row=1,
                column=1,
                sticky="ew"
            )

            ttk.Label(
                result_frame,
                text="Optional if OCR can't read this sample. This value applies only to this file.",
                style="Hint.TLabel"
            ).grid(
                row=2,
                column=1,
                columnspan=3,
                sticky="w",
                pady=(3, 4)
            )

            current_profile_for_count = _family_profile(
                trainer_category,
                trainer_family
            )

            learned_area_count = len(
                get_family_date_regions(
                    current_profile_for_count
                )
            )

            pending_area_count = len(
                pending_date_regions["value"]
            )

            default_area_number = (
                learned_area_count
                + pending_area_count
                + 1
            )

            area_name_var = tk.StringVar(
                value=(
                    "Primary Date"
                    if default_area_number == 1
                    else f"Date Area {default_area_number}"
                )
            )

            existing_priorities = [
                int(region.get("priority", 0))
                for region in get_family_date_regions(
                    current_profile_for_count
                )
            ] + [
                int(region.get("priority", 0))
                for region in pending_date_regions["value"]
            ]

            area_priority_var = tk.IntVar(
                value=max(existing_priorities, default=0) + 1
            )

            ttk.Label(
                result_frame,
                text="Area name",
                style="Section.TLabel"
            ).grid(
                row=3,
                column=0,
                sticky="w",
                padx=(0, 8),
                pady=(6, 2)
            )

            ttk.Entry(
                result_frame,
                textvariable=area_name_var,
                width=28
            ).grid(
                row=3,
                column=1,
                sticky="ew",
                pady=(6, 2)
            )

            ttk.Label(
                result_frame,
                text="Priority",
                style="Section.TLabel"
            ).grid(
                row=3,
                column=2,
                sticky="e",
                padx=(12, 5),
                pady=(6, 2)
            )

            ttk.Spinbox(
                result_frame,
                from_=1,
                to=50,
                textvariable=area_priority_var,
                width=5
            ).grid(
                row=3,
                column=3,
                sticky="e",
                pady=(6, 2)
            )

            ttk.Label(
                result_frame,
                text=(
                    "Priority 1 is checked first. If it fails, SmartScan tries "
                    "Priority 2, then 3, and so on."
                ),
                style="Hint.TLabel"
            ).grid(
                row=4,
                column=1,
                columnspan=3,
                sticky="w",
                pady=(1, 0)
            )

            # ---------------------------------------------------------
            # Trainer state
            # ---------------------------------------------------------
            trainer_state = {
                "base_image": None,
                "photo": None,
                "display_scale": 1.0,
                "image_x": 20,
                "image_y": 20,
                "image_w": 1,
                "image_h": 1,
                "selection_start": None,
                "selection_rect": None,
                "candidate_region": None,
                "candidate_date": None,
                "candidate_conf": 0,
                "ocr_busy": False,
                "selected_region": None,
                "selected_region_kind": None,
            }

            def load_trainer_page():
                if trainer_state.get("ocr_busy"):
                    result_status.set(
                        "Finish the current date read before changing page or zoom."
                    )
                    return

                page_index = trainer_page.get()

                trainer_page_text.set(
                    f"Page {page_index + 1} of {total_pages}"
                )

                try:
                    if trainer_state.get("base_page") == page_index:
                        base = trainer_state.get("base_image")
                    else:
                        base = _load_page_image(p, page_index, scale=2.0)
                        trainer_state["base_page"] = page_index
                except Exception as exc:
                    base = None
                    result_status.set(f"Could not load page preview: {exc}")

                trainer_state["base_image"] = base

                if base is None:
                    trainer_canvas.delete("all")
                    trainer_canvas.create_text(
                        30,
                        30,
                        anchor="nw",
                        text="Document preview unavailable.",
                        fill=C["error"],
                        font=("Segoe UI", 10, "bold")
                    )
                    return

                trainer.update_idletasks()

                available_w = max(
                    40,
                    trainer_canvas.winfo_width() - 50
                )
                available_h = max(
                    40,
                    trainer_canvas.winfo_height() - 50
                )

                fit_scale = min(
                    available_w / base.width,
                    available_h / base.height,
                    1.0
                )

                user_zoom = max(
                    0.50,
                    zoom_percent.get() / 100.0
                )

                scale = fit_scale * user_zoom

                display_w = max(
                    100,
                    int(base.width * scale)
                )
                display_h = max(
                    100,
                    int(base.height * scale)
                )

                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS

                shown = base.resize(
                    (display_w, display_h),
                    resample
                )

                photo = ImageTk.PhotoImage(shown)

                trainer_canvas.delete("all")

                image_x = 20
                image_y = 20

                trainer_canvas.create_image(
                    image_x,
                    image_y,
                    image=photo,
                    anchor="nw",
                    tags=("trainer_document",)
                )

                trainer_state.update({
                    "photo": photo,
                    "display_scale": scale,
                    "image_x": image_x,
                    "image_y": image_y,
                    "image_w": display_w,
                    "image_h": display_h,
                    "selection_start": None,
                    "selection_rect": None,
                    "candidate_region": None,
                    "candidate_date": None,
                    "candidate_conf": 0,
                    "ocr_busy": False,
                    "selected_region": None,
                    "selected_region_kind": None,
                })

                trainer._date_trainer_photo = photo

                trainer_canvas.configure(
                    scrollregion=(
                        0,
                        0,
                        display_w + 40,
                        display_h + 40
                    )
                )

                result_status.set(
                    "Drag a new box around the date value, or CLICK an orange/green learned box to select it."
                )
                result_label.configure(
                    fg=C["navy"]
                )

                try:
                    use_area_button.state(
                        ["disabled"]
                    )
                    delete_area_button.state(
                        ["disabled"]
                    )
                except Exception:
                    pass

                # Show every already-learned date area on this page.
                profile_now = _family_profile(
                    trainer_category,
                    trainer_family
                )

                learned_regions_now = get_family_date_regions(
                    profile_now
                )

                for learned in learned_regions_now:
                    if int(learned.get("page", 0)) != page_index:
                        continue

                    lx0 = image_x + float(learned["x0"]) * display_w
                    ly0 = image_y + float(learned["y0"]) * display_h
                    lx1 = image_x + float(learned["x1"]) * display_w
                    ly1 = image_y + float(learned["y1"]) * display_h

                    trainer_canvas.create_rectangle(
                        lx0,
                        ly0,
                        lx1,
                        ly1,
                        outline="#D97706",
                        width=2,
                        dash=(6, 4),
                        tags=("existing_date_area",)
                    )

                    trainer_canvas.create_text(
                        lx0 + 3,
                        max(image_y + 8, ly0 - 5),
                        text=(
                            f"P{learned.get('priority', '?')} "
                            f"{learned.get('name', 'Date Area')}"
                        ),
                        anchor="sw",
                        fill="#D97706",
                        font=("Segoe UI", 8, "bold"),
                        tags=("existing_date_area",)
                    )

                # Pending areas accepted in this Teach session are green.
                for pending in pending_date_regions["value"]:
                    if int(pending.get("page", 0)) != page_index:
                        continue

                    px0 = image_x + float(pending["x0"]) * display_w
                    py0 = image_y + float(pending["y0"]) * display_h
                    px1 = image_x + float(pending["x1"]) * display_w
                    py1 = image_y + float(pending["y1"]) * display_h

                    trainer_canvas.create_rectangle(
                        px0,
                        py0,
                        px1,
                        py1,
                        outline="#16A34A",
                        width=3,
                        tags=("pending_date_area",)
                    )

                    trainer_canvas.create_text(
                        px0 + 3,
                        max(image_y + 8, py0 - 5),
                        text=(
                            f"P{pending.get('priority', '?')} "
                            f"{pending.get('name', 'New Date Area')}"
                        ),
                        anchor="sw",
                        fill="#16A34A",
                        font=("Segoe UI", 8, "bold"),
                        tags=("pending_date_area",)
                    )

            def trainer_canvas_point(event):
                return (
                    trainer_canvas.canvasx(event.x),
                    trainer_canvas.canvasy(event.y)
                )

            def inside_trainer_image(x, y):
                st = trainer_state

                return (
                    st["image_x"] <= x <= st["image_x"] + st["image_w"]
                    and st["image_y"] <= y <= st["image_y"] + st["image_h"]
                )

            def clamp_trainer_point(x, y):
                st = trainer_state

                return (
                    max(
                        st["image_x"],
                        min(st["image_x"] + st["image_w"], x)
                    ),
                    max(
                        st["image_y"],
                        min(st["image_y"] + st["image_h"], y)
                    )
                )

            def region_display_box(region):
                st = trainer_state

                if int(region.get("page", 0)) != trainer_page.get():
                    return None

                return (
                    st["image_x"] + float(region.get("x0", 0.0)) * st["image_w"],
                    st["image_y"] + float(region.get("y0", 0.0)) * st["image_h"],
                    st["image_x"] + float(region.get("x1", 1.0)) * st["image_w"],
                    st["image_y"] + float(region.get("y1", 1.0)) * st["image_h"],
                )

            def region_screen_area(region):
                box = region_display_box(region)

                if not box:
                    return 10**18

                return max(
                    1.0,
                    (box[2] - box[0]) * (box[3] - box[1])
                )

            def region_contains_point(region, x, y, tolerance=5):
                box = region_display_box(region)

                if not box:
                    return False

                return (
                    box[0] - tolerance <= x <= box[2] + tolerance
                    and box[1] - tolerance <= y <= box[3] + tolerance
                )

            def learned_regions_for_current_family():
                profile = _family_profile(
                    trainer_category,
                    trainer_family
                )

                return get_family_date_regions(profile)

            def clear_active_region_highlight():
                trainer_canvas.delete(
                    "selected_region_highlight"
                )

            def show_selected_region(region, kind):
                if not region:
                    return

                trainer_state["selected_region"] = dict(region)
                trainer_state["selected_region_kind"] = kind
                trainer_state["candidate_region"] = dict(region)
                trainer_state["candidate_date"] = None
                trainer_state["candidate_conf"] = 0

                clear_active_region_highlight()

                box = region_display_box(region)

                if box:
                    trainer_canvas.create_rectangle(
                        box[0],
                        box[1],
                        box[2],
                        box[3],
                        outline="#7C3AED",
                        width=4,
                        tags=("selected_region_highlight",)
                    )

                area_name_var.set(
                    region.get(
                        "name",
                        "Date Area"
                    )
                )

                try:
                    area_priority_var.set(
                        int(region.get("priority", 1))
                    )
                except Exception:
                    area_priority_var.set(1)

                kind_text = (
                    "LEARNED AREA SELECTED"
                    if kind == "learned"
                    else "NEW/PENDING AREA SELECTED"
                )

                result_status.set(
                    f"{kind_text}: P{region.get('priority', '?')} "
                    f"{region.get('name', 'Date Area')}. "
                    "You can delete it, reuse it, or drag a new box."
                )
                result_label.configure(
                    fg="#7C3AED"
                )

                try:
                    use_area_button.state(
                        ["!disabled"]
                    )
                except Exception:
                    pass

                try:
                    delete_area_button.state(
                        ["!disabled"]
                    )
                except Exception:
                    pass

            def hit_test_date_region(x, y):
                candidates = []

                for region in learned_regions_for_current_family():
                    if region_contains_point(region, x, y):
                        candidates.append(
                            (
                                region_screen_area(region),
                                "learned",
                                region
                            )
                        )

                for region in pending_date_regions["value"]:
                    if region_contains_point(region, x, y):
                        candidates.append(
                            (
                                region_screen_area(region),
                                "pending",
                                region
                            )
                        )

                if not candidates:
                    return None

                # When old boxes overlap, select the smallest region under the
                # pointer. That makes cleaning up nested/accidental boxes easier.
                candidates.sort(
                    key=lambda item: item[0]
                )

                return (
                    candidates[0][1],
                    candidates[0][2]
                )

            def trainer_press(event):
                if trainer_state.get("ocr_busy"):
                    result_status.set(
                        "SmartScan is still reading the previous selection..."
                    )
                    return

                x, y = trainer_canvas_point(event)

                if not inside_trainer_image(x, y):
                    result_status.set(
                        "Start the selection inside the white document page."
                    )
                    result_label.configure(
                        fg=C["error"]
                    )
                    return

                # A normal click inside an existing/pending box selects it for
                # management. Dragging still creates a brand-new selection.
                hit = hit_test_date_region(x, y)

                trainer_state["click_hit"] = hit
                trainer_state["press_point"] = (x, y)

                x, y = clamp_trainer_point(
                    x,
                    y
                )

                trainer_state["selection_start"] = (
                    x,
                    y
                )

                trainer_state["candidate_region"] = None
                trainer_state["candidate_date"] = None
                trainer_state["candidate_conf"] = 0
                trainer_state["selected_region"] = None
                trainer_state["selected_region_kind"] = None

                clear_active_region_highlight()

                try:
                    use_area_button.state(
                        ["disabled"]
                    )
                    delete_area_button.state(
                        ["disabled"]
                    )
                except Exception:
                    pass

                if trainer_state["selection_rect"]:
                    try:
                        trainer_canvas.delete(
                            trainer_state["selection_rect"]
                        )
                    except Exception:
                        pass

                trainer_state["selection_rect"] = trainer_canvas.create_rectangle(
                    x,
                    y,
                    x,
                    y,
                    outline=C["accent"],
                    width=3,
                    tags=("active_date_selection",)
                )

                result_status.set(
                    "Drawing new date area..."
                )
                result_label.configure(
                    fg=C["accent"]
                )

            def trainer_drag(event):
                start = trainer_state["selection_start"]

                if not start:
                    return

                x, y = trainer_canvas_point(event)
                x, y = clamp_trainer_point(x, y)

                trainer_canvas.coords(
                    trainer_state["selection_rect"],
                    start[0],
                    start[1],
                    x,
                    y
                )

            def trainer_release(event):
                start_point = trainer_state.get(
                    "selection_start"
                )

                if not start_point:
                    return

                x, y = trainer_canvas_point(event)
                x, y = clamp_trainer_point(
                    x,
                    y
                )

                x0, y0 = start_point
                x1, y1 = x, y

                drag_distance = (
                    abs(x1 - x0)
                    + abs(y1 - y0)
                )

                # Tiny movement = user clicked an existing box instead of drawing.
                if drag_distance < 14:
                    try:
                        if trainer_state["selection_rect"]:
                            trainer_canvas.delete(
                                trainer_state["selection_rect"]
                            )
                    except Exception:
                        pass

                    trainer_state["selection_rect"] = None
                    trainer_state["selection_start"] = None

                    hit = trainer_state.get(
                        "click_hit"
                    )

                    if hit:
                        kind, region = hit
                        show_selected_region(
                            region,
                            kind
                        )
                    else:
                        result_status.set(
                            "No existing date area selected. Drag a box around the date value."
                        )
                        result_label.configure(
                            fg=C["navy"]
                        )

                    return

                if x1 < x0:
                    x0, x1 = x1, x0

                if y1 < y0:
                    y0, y1 = y1, y0

                st = trainer_state

                if (
                    x1 - x0 < 30
                    or y1 - y0 < 18
                ):
                    result_status.set(
                        "Selection is too small. Draw around the entire date."
                    )
                    result_label.configure(
                        fg=C["error"]
                    )
                    st["selection_start"] = None
                    st["candidate_region"] = None

                    try:
                        use_area_button.state(
                            ["disabled"]
                        )
                    except Exception:
                        pass

                    return

                region = {
                    "page": trainer_page.get(),
                    "x0": (x0 - st["image_x"]) / st["image_w"],
                    "y0": (y0 - st["image_y"]) / st["image_h"],
                    "x1": (x1 - st["image_x"]) / st["image_w"],
                    "y1": (y1 - st["image_y"]) / st["image_h"],
                }

                # The region becomes active IMMEDIATELY, before OCR starts.
                st["candidate_region"] = region
                st["selected_region"] = dict(region)
                st["selected_region_kind"] = "new"
                st["selection_start"] = None
                st["ocr_busy"] = True
                st["candidate_date"] = None
                st["candidate_conf"] = 0

                try:
                    use_area_button.state(
                        ["disabled"]
                    )
                    delete_area_button.state(
                        ["disabled"]
                    )
                except Exception:
                    pass

                result_status.set(
                    "✓ NEW SELECTION READY — reading the date inside this box..."
                )
                result_label.configure(
                    fg=C["accent"]
                )

                trainer_canvas.configure(
                    cursor="watch"
                )

                try:
                    trainer_canvas.itemconfigure(
                        st["selection_rect"],
                        outline="#7C3AED",
                        width=4
                    )
                except Exception:
                    pass

                def safe_ui(callback):
                    try:
                        if trainer.winfo_exists():
                            trainer.after(
                                0,
                                callback
                            )
                    except Exception:
                        pass

                def progress_update(message):
                    safe_ui(
                        lambda message=message: result_status.set(
                            "✓ NEW SELECTION READY • " + message
                        )
                    )

                def ocr_worker():
                    try:
                        result = _ocr_date_region(
                            p,
                            region,
                            progress=progress_update,
                            page_image=trainer_state.get("base_image")
                        )
                    except Exception as exc:
                        result = None
                        error_text = str(exc)
                    else:
                        error_text = ""

                    def finish():
                        try:
                            if not trainer.winfo_exists():
                                return
                        except Exception:
                            return

                        st["ocr_busy"] = False

                        trainer_canvas.configure(
                            cursor="crosshair"
                        )

                        # The box stays valid even if OCR cannot read the date,
                        # because a manual date can confirm this document.
                        try:
                            use_area_button.state(
                                ["!disabled"]
                            )
                            delete_area_button.state(
                                ["!disabled"]
                            )
                        except Exception:
                            pass

                        if result:
                            dt, confidence, region_text, details = result

                            st["candidate_date"] = dt
                            st["candidate_conf"] = confidence

                            result_status.set(
                                f"✓ NEW SELECTION READY • DATE FOUND: "
                                f"{dt:%Y-%m-%d} • {confidence}% • {details}. "
                                "Click Use This Date Area."
                            )
                            result_label.configure(
                                fg=C["success"]
                            )

                            try:
                                trainer_canvas.itemconfigure(
                                    st["selection_rect"],
                                    outline=C["success"],
                                    width=4
                                )
                            except Exception:
                                pass

                        else:
                            st["candidate_date"] = None
                            st["candidate_conf"] = 0

                            if error_text:
                                result_status.set(
                                    "✓ NEW SELECTION READY • "
                                    f"DATE READER ERROR: {error_text}. "
                                    "Type this document's date below, then use this area."
                                )
                            else:
                                result_status.set(
                                    "✓ NEW SELECTION READY • DATE NOT READ after automatic crop tightening. "
                                    "The zone is still selected. Redraw it or type this document's "
                                    "date below, then click Use This Date Area."
                                )

                            result_label.configure(
                                fg=C["warning"]
                            )

                            try:
                                trainer_canvas.itemconfigure(
                                    st["selection_rect"],
                                    outline=C["warning"],
                                    width=4
                                )
                            except Exception:
                                pass

                            trainer_manual_entry.focus_set()

                    safe_ui(
                        finish
                    )

                threading.Thread(
                    target=ocr_worker,
                    daemon=True,
                    name="SmartScanDateOCR"
                ).start()

            trainer_canvas.bind(
                "<ButtonPress-1>",
                trainer_press
            )
            trainer_canvas.bind(
                "<B1-Motion>",
                trainer_drag
            )
            trainer_canvas.bind(
                "<ButtonRelease-1>",
                trainer_release
            )

            def previous_trainer_page():
                if trainer_page.get() <= 0:
                    return

                trainer_page.set(
                    trainer_page.get() - 1
                )
                load_trainer_page()

            def next_trainer_page():
                if trainer_page.get() >= total_pages - 1:
                    return

                trainer_page.set(
                    trainer_page.get() + 1
                )
                load_trainer_page()

            ttk.Button(
                trainer_toolbar,
                text="◀ Previous Page",
                style="Small.TButton",
                command=previous_trainer_page
            ).pack(
                side="right",
                padx=(4, 0)
            )

            ttk.Button(
                trainer_toolbar,
                text="Next Page ▶",
                style="Small.TButton",
                command=next_trainer_page
            ).pack(
                side="right",
                padx=(4, 0)
            )

            zoom_box.bind(
                "<<ComboboxSelected>>",
                lambda event: load_trainer_page()
            )

            # ---------------------------------------------------------
            # Always-visible bottom actions
            # ---------------------------------------------------------
            trainer_actions = tk.Frame(
                trainer_footer,
                bg=C["navy"],
                padx=12,
                pady=9
            )
            trainer_actions.pack(fill="x")

            def accept_date_area():
                if trainer_state.get("ocr_busy"):
                    messagebox.showinfo(
                        "Date Reader",
                        "SmartScan is still reading the selected date area. Please wait for DATE FOUND or DATE NOT READ.",
                        parent=trainer
                    )
                    return

                region = trainer_state["candidate_region"]

                if not region:
                    messagebox.showerror(
                        "Teach Date Area",
                        "No date area is selected. Drag a new box, or click an existing learned box to select it.",
                        parent=trainer
                    )
                    return

                typed = trainer_manual_date.get().strip()
                typed_date = None

                if typed:
                    typed_date = parse_date(typed)

                    if not typed_date:
                        messagebox.showerror(
                            "Current Document Date",
                            (
                                "That date could not be read. Try 6/17/26, "
                                "06-17-2026, 2026-06-17, June 17, 2026, "
                                "17 June 2026, or 06 17 2026."
                            ),
                            parent=trainer
                        )
                        trainer_manual_entry.focus_set()
                        return

                candidate = trainer_state["candidate_date"]

                # If OCR could not read this sample, manual confirmation is required
                # before accepting the location. That gives the user an explicit
                # successful step 4 instead of silently doing nothing.
                if candidate is None and typed_date is None:
                    messagebox.showerror(
                        "Date Not Confirmed",
                        (
                            "SmartScan could not read a date from the selected area. "
                            "Either redraw the box or type this document's date in the "
                            "Current document date field, then click Use This Date Area."
                        ),
                        parent=trainer
                    )
                    return

                area_name = clean_name(
                    area_name_var.get().strip()
                    or f"Date Area {len(pending_date_regions['value']) + 1}"
                )

                try:
                    priority = max(
                        1,
                        int(area_priority_var.get())
                    )
                except Exception:
                    priority = 1

                region["name"] = area_name
                region["priority"] = priority

                selected_date = typed_date or candidate
                region["sample"] = selected_date.strftime("%Y-%m-%d")

                # If this new pending box overlaps a pending box from the same
                # session, update it rather than creating an accidental duplicate.
                replaced_pending = False

                for pending_index, pending in enumerate(
                    pending_date_regions["value"]
                ):
                    if _region_iou(region, pending) >= 0.72:
                        pending_date_regions["value"][pending_index] = region
                        replaced_pending = True
                        break

                if not replaced_pending:
                    pending_date_regions["value"].append(
                        region
                    )

                pending_date_regions["value"].sort(
                    key=lambda item: (
                        int(item.get("priority", 999)),
                        int(item.get("page", 0)),
                        item.get("name", "")
                    )
                )

                clear_date_region_requested["value"] = False

                # A typed date is a one-document override only.
                if typed_date:
                    manual_date_var.set(
                        typed_date.strftime("%m/%d/%Y")
                    )

                date_area_status.set(
                    (
                        f"✓ {len(pending_date_regions['value'])} new date area(s) ready. "
                        "Click Teach Date Area again to add another, or Save Learning & File."
                    )
                )

                redraw_overlays()

                messagebox.showinfo(
                    "Date Area Ready",
                    (
                        f"{area_name} accepted as Priority {priority}.\n\n"
                        f"This document: {selected_date:%Y-%m-%d}\n\n"
                        "You can click Teach Date Area again to add another location "
                        "before saving. Future documents will try the learned areas "
                        "in priority order and read their own date values."
                    ),
                    parent=trainer
                )

                trainer.destroy()

            def delete_selected_area():
                if trainer_state.get("ocr_busy"):
                    messagebox.showinfo(
                        "Date Reader",
                        "Wait for the current date read to finish before deleting an area.",
                        parent=trainer
                    )
                    return

                selected = trainer_state.get(
                    "selected_region"
                )
                kind = trainer_state.get(
                    "selected_region_kind"
                )

                if not selected:
                    messagebox.showinfo(
                        "Delete Date Area",
                        "Click a learned or pending date box first.",
                        parent=trainer
                    )
                    return

                name = selected.get(
                    "name",
                    "Date Area"
                )

                priority = selected.get(
                    "priority",
                    "?"
                )

                if not messagebox.askyesno(
                    "Delete Selected Date Area",
                    (
                        f"Delete P{priority} {name}?\\n\\n"
                        "Only this learned date location will be removed. "
                        "The document itself will not be deleted."
                    ),
                    parent=trainer
                ):
                    return

                removed = False

                if kind == "pending":
                    remaining = []

                    for region in pending_date_regions["value"]:
                        if (
                            int(region.get("page", 0)) == int(selected.get("page", 0))
                            and _region_iou(region, selected) >= 0.72
                        ):
                            if not removed:
                                removed = True
                                continue

                        remaining.append(
                            region
                        )

                    pending_date_regions["value"] = remaining

                elif kind == "learned":
                    removed_region = delete_family_date_region(
                        trainer_category,
                        trainer_family,
                        selected
                    )
                    removed = bool(
                        removed_region
                    )

                if not removed:
                    messagebox.showwarning(
                        "Delete Date Area",
                        "SmartScan could not match that selected area for deletion.",
                        parent=trainer
                    )
                    return

                trainer_state["selected_region"] = None
                trainer_state["selected_region_kind"] = None
                trainer_state["candidate_region"] = None
                trainer_state["candidate_date"] = None
                trainer_state["candidate_conf"] = 0

                clear_active_region_highlight()

                try:
                    use_area_button.state(
                        ["disabled"]
                    )
                    delete_area_button.state(
                        ["disabled"]
                    )
                except Exception:
                    pass

                date_area_status.set(
                    "Selected date area deleted. You can teach a replacement area if needed."
                )

                result_status.set(
                    f"✓ DELETED: P{priority} {name}. "
                    "Click another learned box to manage it, or drag a new date area."
                )
                result_label.configure(
                    fg=C["success"]
                )

                # Redraw all remaining learned/pending overlays.
                load_trainer_page()
                redraw_overlays()

            ttk.Button(
                trainer_actions,
                text="Cancel",
                style="Action.TButton",
                command=trainer.destroy
            ).pack(
                side="right",
                padx=(6, 0)
            )

            use_area_button = ttk.Button(
                trainer_actions,
                text="Use This Date Area",
                style="Primary.TButton",
                command=accept_date_area
            )
            use_area_button.pack(
                side="right"
            )
            use_area_button.state(
                ["disabled"]
            )

            delete_area_button = ttk.Button(
                trainer_actions,
                text="Delete Selected Area",
                style="Action.TButton",
                command=delete_selected_area
            )
            delete_area_button.pack(
                side="left",
                padx=(6, 0)
            )
            delete_area_button.state(
                ["disabled"]
            )

            ttk.Button(
                trainer_actions,
                text="Reset View",
                style="Action.TButton",
                command=lambda: (
                    zoom_percent.set(100),
                    load_trainer_page()
                )
            ).pack(
                side="left"
            )

            _finish_learning_form(trainer, trainer_content)
            _flow_learning_actions(trainer_actions)
            trainer_resize = {"job": None}
            def resize_trainer(event=None):
                if trainer_resize["job"]:
                    trainer.after_cancel(trainer_resize["job"])
                trainer_resize["job"] = trainer.after(120, load_trainer_page)
            trainer_canvas.bind("<Configure>", resize_trainer, add="+")
            trainer.after(100, load_trainer_page)

        def start_date_area_teaching():
            date_selection_mode["value"] = True
            date_drag_start["value"] = None
            preview_canvas.configure(
                cursor="crosshair"
            )

            date_area_status.set(
                "TEACHING DATE AREA: drag a box around the DATE VALUE. "
                "Include a little blank space around it for scan-to-scan movement."
            )

        def clear_date_area():
            if not messagebox.askyesno(
                "Clear All Date Areas",
                (
                    "Clear every learned date location for this document type "
                    "when you click Save Learning & File?"
                ),
                parent=win
            ):
                return

            pending_date_regions["value"] = []
            clear_date_region_requested["value"] = True
            date_selection_mode["value"] = False

            preview_canvas.configure(
                cursor="arrow"
            )

            redraw_overlays()

            date_area_status.set(
                "All learned date areas will be cleared when you click Save Learning & File."
            )

        teach_date_button = ttk.Button(
            toolbar,
            text="Teach Date Area",
            style="Primary.TButton",
            command=open_date_area_trainer
        )
        teach_date_button.pack(
            side="right",
            padx=(8, 4)
        )

        clear_date_button = ttk.Button(
            toolbar,
            text="Clear All Date Areas",
            style="Small.TButton",
            command=clear_date_area
        )
        clear_date_button.pack(
            side="right",
            padx=(4, 4)
        )

        def update_page_buttons(*_):
            page = current_page.get()

            previous_button.state(
                ["disabled"]
                if page <= 0
                else ["!disabled"]
            )

            next_button.state(
                ["disabled"]
                if page >= total_pages - 1
                else ["!disabled"]
            )

        current_page.trace_add(
            "write",
            update_page_buttons
        )

        update_page_buttons()

        def point_inside_image(x, y):
            gx = preview_geometry

            return (
                gx["x"] <= x <= gx["x"] + gx["w"]
                and gx["y"] <= y <= gx["y"] + gx["h"]
            )

        def clamp_to_image(x, y):
            gx = preview_geometry

            return (
                max(gx["x"], min(gx["x"] + gx["w"], x)),
                max(gx["y"], min(gx["y"] + gx["h"], y)),
            )

        def on_date_press(event):
            if not date_selection_mode["value"]:
                return

            if not point_inside_image(event.x, event.y):
                date_area_status.set(
                    "Start the selection inside the document page."
                )
                return

            x, y = clamp_to_image(
                event.x,
                event.y
            )

            date_drag_start["value"] = (
                x,
                y
            )

            if date_drag_rect["value"]:
                preview_canvas.delete(
                    date_drag_rect["value"]
                )

            date_drag_rect["value"] = preview_canvas.create_rectangle(
                x,
                y,
                x,
                y,
                outline="#2563EB",
                width=3
            )

        def on_date_drag(event):
            start_point = date_drag_start["value"]

            if (
                not date_selection_mode["value"]
                or not start_point
            ):
                return

            x, y = clamp_to_image(
                event.x,
                event.y
            )

            preview_canvas.coords(
                date_drag_rect["value"],
                start_point[0],
                start_point[1],
                x,
                y
            )

        def on_date_release(event):
            start_point = date_drag_start["value"]

            if (
                not date_selection_mode["value"]
                or not start_point
            ):
                return

            x, y = clamp_to_image(
                event.x,
                event.y
            )

            x0, y0 = start_point
            x1, y1 = x, y

            if x1 < x0:
                x0, x1 = x1, x0

            if y1 < y0:
                y0, y1 = y1, y0

            gx = preview_geometry

            if (
                x1 - x0 < 25
                or y1 - y0 < 15
            ):
                date_area_status.set(
                    "That box is too small. Drag a larger box around the complete date."
                )
                return

            region = {
                "page": current_page.get(),
                "x0": (x0 - gx["x"]) / gx["w"],
                "y0": (y0 - gx["y"]) / gx["h"],
                "x1": (x1 - gx["x"]) / gx["w"],
                "y1": (y1 - gx["y"]) / gx["h"],
            }

            result = _ocr_date_region(
                p,
                region
            )

            preview_canvas.delete(
                date_drag_rect["value"]
            )
            date_drag_rect["value"] = None
            date_drag_start["value"] = None

            if not result:
                pending_date_regions["value"] = []

                date_area_status.set(
                    "No single valid date was read from that box. "
                    "Try again and draw around only the date value with a little margin."
                )

                redraw_overlays()
                return

            dt, confidence, region_text, details = result

            region["sample"] = dt.strftime("%Y-%m-%d")
            region.setdefault(
                "name",
                f"Date Area {len(pending_date_regions['value']) + 1}"
            )
            region.setdefault(
                "priority",
                len(pending_date_regions["value"]) + 1
            )
            pending_date_regions["value"].append(region)
            clear_date_region_requested["value"] = False
            date_selection_mode["value"] = False

            preview_canvas.configure(
                cursor="arrow"
            )

            redraw_overlays()

            date_area_status.set(
                f"✓ Date area ready: {dt:%Y-%m-%d} read at {confidence}% "
                f"({details}). It will be learned when you save."
            )

        preview_canvas.bind(
            "<ButtonPress-1>",
            on_date_press
        )
        preview_canvas.bind(
            "<B1-Motion>",
            on_date_drag
        )
        preview_canvas.bind(
            "<ButtonRelease-1>",
            on_date_release
        )

        win.after(
            75,
            lambda: show_page(0)
        )

        win.after(
            100,
            lambda: self.activity_status.set(
                "Teach window ready"
            )
        )

        # Learning summary
        summary_text = tk.Text(
            learning_tab,
            wrap="word",
            font=("Segoe UI", 9),
            bg=C["surface"],
            fg=C["text"],
            relief="flat",
            padx=10,
            pady=8
        )

        summary_text.pack(
            fill="both",
            expand=True
        )

        summary_text.insert(
            "end",
            (
                "SmartScan learns the stable printed form structure for this document type.\n\n"
                f"Category: {guessed_category}\n"
                f"Suggested document type: {guessed_family}\n"
                f"Printed structure: {structure_summary(struct_fp)}\n\n"
                "After you save this correction, SmartScan will immediately compare every "
                "other paused document against the new family. Strong matches will be filed "
                "automatically so you do not have to teach the same form repeatedly.\n\n"
                "Handwritten names, amounts and changing values are not primary family features.\n\n"
                "Multiple Date Areas: click Teach Date Area as many times as needed. Each area has "
                "a name, page, location, and priority. SmartScan checks Priority 1 first, then 2, 3, "
                "and so on until one location produces a valid date.\n\n"
                "The saved learning is the location of each field, never the example date value. "
                "Every future document still reads its own date independently.\n\n"
                "If the learned area cannot produce one valid date, SmartScan falls back to the safe "
                "family-specific date label rules. It never searches the whole page and guesses.\n\n"
                "Manual Date Override: you can also type a date for the current document. That value "
                "is used only for this one filename. It is never stored as family learning and is never "
                "copied to the other One-Teach-Many queued documents."
            )
        )

        summary_text.configure(
            state="disabled"
        )

        # OCR tab
        ocr_frame = ttk.Frame(
            ocr_tab
        )
        ocr_frame.pack(
            fill="both",
            expand=True
        )

        ocr_scroll = ttk.Scrollbar(
            ocr_frame,
            orient="vertical"
        )
        ocr_scroll.pack(
            side="right",
            fill="y"
        )

        ocr_preview = tk.Text(
            ocr_frame,
            wrap="word",
            font=("Consolas", 9),
            bg="#0F172A",
            fg="#D9E6F2",
            relief="flat",
            padx=10,
            pady=8,
            yscrollcommand=ocr_scroll.set
        )

        ocr_preview.pack(
            side="left",
            fill="both",
            expand=True
        )

        ocr_scroll.config(
            command=ocr_preview.yview
        )

        ocr_preview.insert(
            "end",
            structured_ocr_preview(text)
        )

        ocr_preview.configure(
            state="disabled"
        )

        # Bottom actions
        action_bar = tk.Frame(
            teach_footer,
            bg=C["navy"],
            padx=14,
            pady=9
        )
        action_bar.pack(fill="x")

        def open_document():
            try:
                os.startfile(
                    str(p)
                )
            except Exception as exc:
                messagebox.showerror(
                    "Open Document",
                    str(exc),
                    parent=win
                )

        def split_and_teach_pages():
            total = document_page_count(p)

            if p.suffix.lower() != ".pdf" or total <= 1:
                messagebox.showinfo(
                    "Split / Teach Pages",
                    "This document does not contain multiple PDF pages.",
                    parent=win
                )
                return

            dialog = tk.Toplevel(win)
            dialog.title("SmartScan — Split PDF Into Separate Documents")
            dialog.transient(win)
            dialog.grab_set()
            dialog.configure(bg=C["bg"])

            dw, dh = _fit_learning_window(dialog, 650, 400)
            split_content, split_footer = _learning_form(dialog)

            header = tk.Frame(
                split_content,
                bg=C["navy"],
                padx=16,
                pady=12
            )
            header.pack(fill="x")

            tk.Label(
                header,
                text="Split This PDF Into Separate Documents",
                bg=C["navy"],
                fg="white",
                font=("Segoe UI", 14, "bold")
            ).pack(anchor="w")

            tk.Label(
                header,
                text=(
                    f"{p.name} has {total} pages. Each comma-separated group becomes one document."
                ),
                bg=C["navy"],
                fg="#DCEAFF",
                font=("Segoe UI", 9)
            ).pack(anchor="w", pady=(3, 0))

            body = ttk.Frame(split_content, padding=8)
            body.pack(fill="both", expand=True)

            ttk.Label(
                body,
                text="Page groups",
                style="Section.TLabel"
            ).pack(anchor="w")

            groups_var = tk.StringVar(
                value=", ".join(str(i) for i in range(1, total + 1))
            )

            groups_entry = ttk.Entry(
                body,
                textvariable=groups_var,
                font=("Segoe UI", 11)
            )
            groups_entry.pack(fill="x", pady=(5, 7))

            ttk.Label(
                body,
                text=(
                    "Examples:  1, 2, 3, 4  = four separate documents\n"
                    "           1-2, 3, 4-5 = three documents (pages 1-2 stay together)\n\n"
                    "Every page must appear exactly once. The original combined PDF is backed up "
                    "in Review\\Split Originals."
                ),
                style="Hint.TLabel",
                justify="left",
                wraplength=max(480, dw - 50)
            ).pack(anchor="w", pady=(0, 10))

            button_row = ttk.Frame(split_footer)
            button_row.pack(fill="x", side="bottom")

            def set_one_page_each():
                groups_var.set(
                    ", ".join(str(i) for i in range(1, total + 1))
                )

            ttk.Button(
                button_row,
                text="One Page = One Document",
                style="Action.TButton",
                command=set_one_page_each
            ).pack(side="left")

            def do_split():
                try:
                    groups = parse_manual_page_groups(
                        groups_var.get(),
                        total
                    )
                except Exception as exc:
                    messagebox.showerror(
                        "Page Groups",
                        str(exc),
                        parent=dialog
                    )
                    groups_entry.focus_set()
                    return

                description = []
                for number, pages in enumerate(groups, 1):
                    if len(pages) == 1:
                        page_text = str(pages[0])
                    else:
                        page_text = f"{pages[0]}-{pages[-1]}"
                    description.append(f"Document {number}: page(s) {page_text}")

                if not messagebox.askyesno(
                    "Split & Teach",
                    "Create these separate documents?\n\n"
                    + "\n".join(description)
                    + "\n\nEach new document will enter Teach/Review individually.",
                    parent=dialog
                ):
                    return

                try:
                    dialog.configure(cursor="watch")
                    dialog.update_idletasks()

                    parts, backup = manual_split_pdf_groups(
                        p,
                        groups,
                        notify=self.note
                    )
                except Exception as exc:
                    dialog.configure(cursor="")
                    messagebox.showerror(
                        "Split PDF",
                        str(exc),
                        parent=dialog
                    )
                    return

                # The combined source is no longer the teach target.
                self.engine.unpause(p)

                # Every manually separated piece is locked to individual
                # Teach. Put the whole split session at the FRONT of the queue.
                split_session_id = (
                    f"{p.stem}-{int(time.time() * 1000)}"
                )

                split_items = []

                for split_number, part in enumerate(
                    parts,
                    1
                ):
                    part_key = self.engine.key(part)
                    self.engine.paused.add(part_key)

                    try:
                        queue_key = str(
                            part.resolve()
                        ).lower()
                    except Exception:
                        queue_key = str(part).lower()

                    if queue_key in self.teach_queue_keys:
                        continue

                    split_items.append({
                        "path": str(part),
                        "reason": (
                            f"Split document {split_number} of {len(parts)}. "
                            "This piece requires individual Teach before moving "
                            "to the next piece from this combined PDF."
                        ),
                        "category": "",
                        "family": "",
                        "individual_teach": True,
                        "split_index": split_number,
                        "split_total": len(parts),
                        "split_session": split_session_id,
                    })

                    self.teach_queue_keys.add(
                        queue_key
                    )

                self.teach_queue = (
                    split_items
                    + self.teach_queue
                )

                dialog.destroy()
                win.destroy()

                self.teach_window_open = False
                self.current_teach_path = None
                self._update_queue_status()

                self.note(
                    f"Manual split ready: {len(parts)} separate document(s) queued for individual teaching."
                )

                self.root.after(
                    125,
                    self._open_next_teach
                )

            ttk.Button(
                button_row,
                text="Cancel",
                style="Action.TButton",
                command=dialog.destroy
            ).pack(side="right", padx=(6, 0))

            ttk.Button(
                button_row,
                text="Split & Teach",
                style="Primary.TButton",
                command=do_split
            ).pack(side="right")

            _finish_learning_form(dialog, split_content)
            _flow_learning_actions(button_row)
            groups_entry.focus_set()

        def learn_and_continue():
            category = category_var.get().strip()
            family = family_var.get().strip()

            manual_date = None
            manual_date_text = manual_date_var.get().strip()

            if manual_date_text:
                manual_date = parse_date(
                    manual_date_text
                )

                if not manual_date:
                    messagebox.showerror(
                        "Manual Date",
                        (
                            "That manual date could not be read.\n\n"
                            "Try a format such as 6/17/26, 06-17-2026, "
                            "2026-06-17, or June 17, 2026."
                        ),
                        parent=win
                    )
                    manual_date_entry.focus_set()
                    return

            if (
                not category
                or category == NEW_CATEGORY_OPTION
                or category not in CATEGORY_RULES
            ):
                messagebox.showerror(
                    "Category",
                    "Choose a valid category.",
                    parent=win
                )
                return

            if (
                not family
                or family == NEW_FAMILY_OPTION
            ):
                messagebox.showerror(
                    "Document Type",
                    "Choose a document type or select + New Document Type....",
                    parent=win
                )
                family_combo.focus_set()
                return

            if manual_date:
                save_manual_document_date(
                    p,
                    manual_date,
                    category,
                    family
                )
                self.note(
                    f"Manual date remembered for this exact document: {manual_date:%Y-%m-%d}."
                )

            # Snapshot every waiting/paused file BEFORE moving current file.
            queued_before = self._queued_and_paused_paths()

            try:
                learned_text = teach_family(
                    p,
                    category,
                    family,
                    railroad=railroad_var.get().strip(),
                    location=location_var.get().strip(),
                    person=person_var.get().strip()
                )

                # v46: save every date area taught in this session.
                if clear_date_region_requested["value"]:
                    clear_family_date_region(
                        category,
                        family
                    )

                    self.note(
                        f"All learned date areas cleared for {category} › {family}."
                    )

                if pending_date_regions["value"]:
                    saved_count = 0

                    for region in pending_date_regions["value"]:
                        sample_date = None

                        if region.get("sample"):
                            try:
                                sample_date = datetime.strptime(
                                    region["sample"],
                                    "%Y-%m-%d"
                                )
                            except Exception:
                                sample_date = None

                        saved = save_family_date_region(
                            category,
                            family,
                            region,
                            sample_date
                        )

                        if saved:
                            saved_count += 1

                    if saved_count:
                        total_regions = len(
                            get_family_date_regions(
                                _family_profile(
                                    category,
                                    family
                                )
                            )
                        )

                        self.note(
                            (
                                f"{saved_count} date area(s) saved for {category} › {family}. "
                                f"{total_regions} total learned location(s) are now available."
                            )
                        )

                if file_now.get() and p.exists():
                    teach_metadata = {
                        "railroad": railroad_var.get().strip(),
                        "location": location_var.get().strip(),
                        "person": person_var.get().strip(),
                        "source": "Conductor Review"
                    }
                    status, destination = self.engine._file_known_document(
                        p,
                        learned_text,
                        category,
                        family,
                        manual_date=manual_date,
                        metadata=teach_metadata,
                        recognition_source="RAILY Conductor Review",
                        recognition_confidence=100
                    )

                    if status in {"needs_date", "needs_route"}:
                        self.engine.paused.add(self.engine.key(p))
                        if status == "needs_route":
                            messagebox.showwarning(
                                "Route Review Required",
                                (
                                    "RAILY needs both Railroad and Location before filing.\n\n"
                                    "Enter them in RAILY Route & Identity, then click Save Learning & File."
                                ),
                                parent=win
                            )
                        else:
                            messagebox.showwarning(
                                "Date Review Required",
                                (
                                    "RAILY recognizes this document type, but the "
                                    "required date still could not be read.\n\n"
                                    "Use Teach Date Area again or type this document's "
                                    "date in Manual date, then click Save Learning & File."
                                ),
                                parent=win
                            )
                            manual_date_entry.focus_set()
                        return

                    self.engine.unpause(p)

                    if status in {"filed", "duplicate"}:
                        self.note(
                            f"Learning saved: {category} › {family}"
                        )
                else:
                    self.note(
                        f"Learning saved: {category} › {family}"
                    )

            except Exception as exc:
                messagebox.showerror(
                    "Learning Error",
                    str(exc),
                    parent=win
                )
                return

            # Current file no longer belongs in the pending set.
            remaining = [
                raw for raw in queued_before
                if Path(raw).exists()
                and str(Path(raw)).lower() != str(p).lower()
            ]

            win.destroy()

            self.teach_window_open = False
            self.current_teach_path = None

            # v58: manually split documents are guaranteed one-by-one Teach.
            # Do NOT let One-Teach-Many silently file sibling split pieces.
            if individual_teach:
                self.note(
                    (
                        f"Split document {split_index or '?'} of "
                        f"{split_total or '?'} finished — opening the next "
                        "split document for Teach."
                    )
                )

                self.engine.set_teach_blocked(True)

                self.root.after(
                    125,
                    self._open_next_teach
                )
                return

            # Normal non-split behavior may use One-Teach-Many.
            if (
                CFG.get("auto_apply_new_learning_to_queue", True)
                and remaining
            ):
                def batch_worker():
                    before_exists = {
                        str(Path(raw))
                        for raw in remaining
                        if Path(raw).exists()
                    }

                    self.engine.recheck_after_learning(
                        category,
                        family,
                        remaining
                    )

                    # Anything that disappeared from Incoming was handled.
                    processed = [
                        raw for raw in remaining
                        if not Path(raw).exists()
                    ]

                    self.root.after(
                        0,
                        lambda: self._remove_queue_paths(processed)
                    )

                    self.root.after(
                        150,
                        self._open_next_teach
                    )

                threading.Thread(
                    target=batch_worker,
                    daemon=True
                ).start()
            else:
                self.root.after(
                    150,
                    self._open_next_teach
                )

        ttk.Button(
            action_bar,
            text="Open Full Document",
            style="Action.TButton",
            command=open_document
        ).pack(
            side="left"
        )

        split_pages_button = ttk.Button(
            action_bar,
            text="Split / Teach Pages",
            style="Action.TButton",
            command=split_and_teach_pages
        )
        split_pages_button.pack(
            side="left",
            padx=(6, 0)
        )

        if p.suffix.lower() != ".pdf" or document_page_count(p) <= 1:
            split_pages_button.state(["disabled"])

        ttk.Button(
            action_bar,
            text="Skip for Now",
            style="Action.TButton",
            command=skip_for_now
        ).pack(
            side="right",
            padx=(6, 0)
        )

        save_button = ttk.Button(
            action_bar,
            text="Save Learning & File",
            style="Primary.TButton",
            command=learn_and_continue
        )

        save_button.pack(
            side="right"
        )

        win.bind(
            "<Control-Return>",
            lambda event: learn_and_continue()
        )

        win.bind(
            "<Escape>",
            lambda event: skip_for_now()
        )

        if compact_teach:
            # Stack route fields instead of squeezing three wide comboboxes.
            for child in route_card.winfo_children():
                info = child.grid_info()
                if int(info.get("row", 0)) == 0:
                    col = int(info["column"])
                    child.grid_configure(row=col // 2, column=col % 2)
                else:
                    child.grid_configure(row=3, column=0, columnspan=2)
            for col in range(6):
                route_card.columnconfigure(col, weight=1 if col == 1 else 0)
        _finish_learning_form(win, teach_content)
        _flow_learning_actions(action_bar)
        preview_resize = {"job": None}
        def resize_preview(event=None):
            if preview_resize["job"]:
                win.after_cancel(preview_resize["job"])
            preview_resize["job"] = win.after(120, lambda: show_page(current_page.get()))
        preview_canvas.bind("<Configure>", resize_preview, add="+")
        save_button.focus_set()

    # ----------------------------------------------------------------------
    # Rename existing
    # ----------------------------------------------------------------------

    def rename_existing_documents(self):
        self.sync()

        root = Path(CFG["sorted"])

        files = [
            p for p in root.rglob("*")
            if p.is_file()
            and p.suffix.lower() in SUPPORTED
        ]

        if not files:
            messagebox.showinfo(
                "Rename Existing Documents",
                "No documents were found in Sorted."
            )
            return

        if not messagebox.askyesno(
            "Rename Existing Documents",
            f"Rename {len(files)} existing document(s) using document type and safe date metadata?\n\n"
            "A date is added only when a trusted family-specific label is found. "
            "Files remain in their current folders. Nothing is deleted."
        ):
            return

        def worker():
            renamed = 0
            skipped = 0

            for path in live_files:
                try:
                    text = get_cached_ocr(path)

                    try:
                        index_entry = INDEX.get("files", {}).get(str(path.resolve()).lower(), {})
                    except Exception:
                        index_entry = {}

                    category = clean_optional_name(index_entry.get("category", ""))
                    if category not in CATEGORY_RULES:
                        category, _, _ = classify(text, path.name)

                    family = clean_optional_name(index_entry.get("family", ""))
                    if not family:
                        family, confidence, source = resolve_family(
                            category,
                            text,
                            path
                        )
                        if (
                            source == "category fallback"
                            and confidence < int(CFG.get("family_threshold", 50))
                        ):
                            skipped += 1
                            continue

                    visual_match = raily_match_visual_document(path)
                    metadata = raily_extract_metadata(text, visual_match=visual_match)
                    person = (
                        clean_optional_name(index_entry.get("person", ""))
                        or metadata.get("person", "")
                    )

                    desired = build_filename(
                        path.name,
                        category,
                        family,
                        text,
                        path=path,
                        person=person
                    )

                    if path.name.lower() == desired.lower():
                        continue

                    target = unique_path(
                        path.with_name(desired)
                    )

                    old_key = str(
                        path.resolve()
                    ).lower()

                    path.rename(target)

                    entry = INDEX["files"].pop(
                        old_key,
                        None
                    )

                    if entry:
                        entry["path"] = str(target)

                        INDEX["files"][
                            str(target.resolve()).lower()
                        ] = entry

                    renamed += 1

                except Exception:
                    skipped += 1

            save_index()

            self.note(
                f"Rename Existing complete: {renamed} renamed; {skipped} skipped."
            )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # ----------------------------------------------------------------------
    # Duplicate maintenance
    # ----------------------------------------------------------------------

    def build_index(self):
        self.sync()

        files = [
            p for p in Path(CFG["sorted"]).rglob("*")
            if p.is_file()
            and p.suffix.lower() in SUPPORTED
        ]

        if not files:
            messagebox.showinfo(
                "Duplicate Index",
                "No Sorted documents found."
            )
            return

        if not messagebox.askyesno(
            "Build / Rebuild Duplicate Index",
            f"Index {len(files)} document(s)?\n\n"
            "Existing documents are read once. Future duplicate checks use the saved index."
        ):
            return

        def worker():
            INDEX["files"] = {}
            save_index()

            total = len(files)

            for i, path in enumerate(files, 1):
                self.note(
                    f"Indexing {i}/{total}: {path.name}"
                )

                try:
                    text = get_cached_ocr(path)

                    category = category_from_sorted_path(path)

                    if not category:
                        category, _, _ = classify(
                            text,
                            path.name
                        )

                    family = family_from_sorted_path(path)

                    if not family:
                        family, _, _ = resolve_family(
                            category,
                            text,
                            path
                        )

                    visual_match = raily_match_visual_document(path)
                    metadata = raily_extract_metadata(text, visual_match=visual_match)
                    index_document(
                        path,
                        text,
                        category,
                        family,
                        railroad=metadata.get("railroad", ""),
                        location=metadata.get("location", ""),
                        person=metadata.get("person", "")
                    )

                except Exception as exc:
                    self.note(
                        f"Index skipped {path.name}: {exc}"
                    )

            self.note(
                f"Duplicate index complete: {len(INDEX['files'])} documents."
            )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()


    def find_existing_duplicates(self):
        self.sync()

        root = Path(
            CFG["sorted"]
        )

        duplicate_root = Path(
            CFG["duplicates"]
        )

        duplicate_root.mkdir(
            parents=True,
            exist_ok=True
        )

        files = [
            p for p in root.rglob("*")
            if p.is_file()
            and p.suffix.lower() in SUPPORTED
        ]

        if len(files) < 2:
            messagebox.showinfo(
                "Find Existing Duplicates",
                "Not enough Sorted documents to compare."
            )
            return

        if not messagebox.askyesno(
            "Find Existing Duplicates",
            (
                f"Scan {len(files)} Sorted document(s)?\\n\\n"
                "SmartScan will first catch numbered same-name/date collisions "
                "such as _2 and _3, then compare category, document type, "
                "saved/detected filename date, OCR content, visual similarity, "
                "and file size.\\n\\n"
                "Suspected extra copies will be moved to Duplicates\\\\Review. "
                "Nothing is deleted."
            )
        ):
            return

        def worker():
            updated_index = False
            collision_moved = 0

            # v63 keeps the v62 duplicate pre-pass unchanged.
            # v62 PRE-PASS:
            # A file such as 2025-06-01_IRAIL_Work_Log_2.pdf means SmartScan
            # previously encountered the exact same intended dated filename.
            # Group those by folder + canonical filename and move numbered
            # extras to Duplicate Review before slower OCR similarity checks.
            collision_groups = {}

            for candidate in list(files):
                if not candidate.exists():
                    continue

                key = dated_family_collision_key(
                    candidate
                )

                collision_groups.setdefault(
                    key,
                    []
                ).append(
                    candidate
                )

            for group in collision_groups.values():
                if len(group) < 2:
                    continue

                # Prefer the clean unnumbered filename as keeper.
                group.sort(
                    key=lambda p: (
                        is_numbered_collision_copy(p),
                        len(p.name),
                        p.stat().st_mtime
                    )
                )

                keeper = group[0]

                for extra in group[1:]:
                    if not extra.exists():
                        continue

                    target = unique_path(
                        duplicate_root
                        / extra.name
                    )

                    old_key = str(
                        extra.resolve()
                    ).lower()

                    shutil.move(
                        str(extra),
                        str(target)
                    )

                    INDEX.get(
                        "files",
                        {}
                    ).pop(
                        old_key,
                        None
                    )

                    collision_moved += 1

                    self.note(
                        f"Numbered collision moved to Duplicates Review: "
                        f"{extra.name} • matches intended filename "
                        f"{canonical_numbered_filename(extra)}"
                    )

            if collision_moved:
                save_index()

            # Refresh the working list after collision cleanup.
            live_files = [
                p for p in files
                if p.exists()
            ]

            # Ensure every Sorted file has a current index record and backfill
            # a known date from saved manual dates / dated filenames.
            for path in files:
                try:
                    key = str(
                        path.resolve()
                    ).lower()

                    entry = INDEX["files"].get(
                        key
                    )

                    if not entry:
                        text = get_cached_ocr(
                            path
                        )

                        category = category_from_sorted_path(path)
                        if not category:
                            category, _, _ = classify(text, path.name)

                        family = family_from_sorted_path(path)
                        if not family:
                            family, _, _ = resolve_family(category, text, path)

                        visual_match = raily_match_visual_document(path)
                        metadata = raily_extract_metadata(text, visual_match=visual_match)
                        index_document(
                            path,
                            text,
                            category,
                            family,
                            railroad=metadata.get("railroad", ""),
                            location=metadata.get("location", ""),
                            person=metadata.get("person", "")
                        )

                        entry = INDEX["files"].get(
                            key
                        )

                    if entry and not entry.get(
                        "document_date"
                    ):
                        inferred = duplicate_entry_date(
                            entry
                        )

                        if inferred:
                            entry[
                                "document_date"
                            ] = inferred
                            entry[
                                "date_source"
                            ] = "backfilled"
                            updated_index = True

                except Exception as exc:
                    self.note(
                        f"Duplicate index warning: {path.name}: {exc}"
                    )

            if updated_index:
                save_index()

            entries = [
                e
                for e in INDEX["files"].values()
                if Path(
                    e.get(
                        "path",
                        ""
                    )
                ).exists()
            ]

            # Prefer an unnumbered/original-looking file as the keeper.
            entries.sort(
                key=lambda e: (
                    bool(
                        re.search(
                            r"_\\d+$",
                            Path(
                                e.get(
                                    "path",
                                    ""
                                )
                            ).stem
                        )
                    ),
                    len(
                        Path(
                            e.get(
                                "path",
                                ""
                            )
                        ).name
                    ),
                    e.get(
                        "indexed_at",
                        ""
                    ),
                )
            )

            kept = []
            moved = 0
            date_matched = 0

            for entry in entries:
                path = Path(
                    entry.get(
                        "path",
                        ""
                    )
                )

                if not path.exists():
                    continue

                duplicate_of = None
                duplicate_result = None

                for prior in kept:
                    if (
                        entry.get("category")
                        and prior.get("category")
                        and entry.get("category")
                        != prior.get("category")
                    ):
                        continue

                    if (
                        entry.get("family")
                        and prior.get("family")
                        and clean_name(
                            str(entry.get("family"))
                        ).casefold()
                        != clean_name(
                            str(prior.get("family"))
                        ).casefold()
                    ):
                        continue

                    result = duplicate_score(
                        entry,
                        prior
                    )

                    if result:
                        duplicate_of = prior
                        duplicate_result = result
                        break

                if not duplicate_result:
                    kept.append(
                        entry
                    )
                    continue

                kind, score, details = duplicate_result

                target = unique_path(
                    duplicate_root
                    / path.name
                )

                old_key = str(
                    path.resolve()
                ).lower()

                shutil.move(
                    str(path),
                    str(target)
                )

                INDEX["files"].pop(
                    old_key,
                    None
                )

                moved += 1

                if kind == "same-date":
                    date_matched += 1

                self.note(
                    f"Existing duplicate moved: {path.name} • "
                    f"{kind} {score:.0%} • {details}"
                )

            save_index()

            total_moved = (
                moved
                + collision_moved
            )

            self.note(
                f"Existing duplicate scan complete: {total_moved} suspected "
                f"duplicate copy/copies moved; {collision_moved} were numbered "
                f"same-name/date collisions and {date_matched} were strengthened "
                f"by matching document dates."
            )

        threading.Thread(
            target=worker,
            daemon=True,
            name="SmartScanExistingDuplicateScan"
        ).start()

    # ----------------------------------------------------------------------
    # Reset
    # ----------------------------------------------------------------------


    # ----------------------------------------------------------------------
    # v65 intelligence / review / background helpers
    # ----------------------------------------------------------------------

    def refresh_persistent_dashboard(self):
        stats = today_stats()
        filed = int(stats.get("filed", 0))
        duplicates = int(stats.get("duplicates", 0))
        reviews = int(stats.get("needs_review", 0))
        new_families = int(stats.get("new_families", 0))
        denominator = filed + reviews
        auto_rate = int(round(100 * filed / denominator)) if denominator else 100

        values = (
            ("today_filed_var", str(filed)),
            ("today_duplicate_var", str(duplicates)),
            ("today_review_var", str(reviews)),
            ("today_family_var", str(new_families)),
            ("today_auto_rate_var", f"{auto_rate}%"),
        )
        for name, value in values:
            var = getattr(self, name, None)
            if var is not None:
                try:
                    var.set(value)
                except Exception:
                    pass

    def open_recognition_explanation(self):
        info = getattr(self.engine, "last_recognition", {}) or {}
        if not info:
            messagebox.showinfo(
                "Why This Match?",
                "SmartScan has not recognized a document in this session yet.",
                parent=self.root
            )
            return

        breakdown = info.get("breakdown", {}) or {}
        win = tk.Toplevel(self.root)
        win.title("RAILY — Why This Match?")
        _fit_learning_window(win, 720, 560)
        win.transient(self.root)

        heading = ttk.LabelFrame(win, text="RAILY Last Recognition", style="Card.TLabelframe")
        heading.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(
            heading,
            text=(
                f"{info.get('filename', '')}\n"
                f"{info.get('category', '')} ({info.get('category_confidence', 0)}%)  →  "
                f"{info.get('family', '')} ({info.get('family_confidence', 0)}%)"
            ),
            style="Section.TLabel"
        ).pack(anchor="w")

        body = tk.Text(win, wrap="word", font=("Consolas", 10), padx=10, pady=10)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        lines = [
            f"Dominant signal:       {breakdown.get('dominant', info.get('family_source', ''))}",
            f"RAILY full-page visual:{breakdown.get('raily_visual', 0):>4}%",
            f"Printed identity/name: {breakdown.get('identity', 0)}%",
            f"Printed structure:     {breakdown.get('structure', 0)}%",
            f"OCR fingerprint:       {breakdown.get('ocr', 0)}%",
            f"Teaching bonus:        +{breakdown.get('correction_bonus', 0)}",
            f"Success-growth bonus:  +{breakdown.get('growth_bonus', 0)}",
            f"Final family score:    {breakdown.get('total', info.get('family_confidence', 0))}%",
            "",
            f"Training examples:     {breakdown.get('examples', 0)}",
            f"Successful auto files: {breakdown.get('successes', 0)}",
            f"Current success streak:{breakdown.get('streak', 0):>4}",
            f"Parent family:          {breakdown.get('parent_family', '') or '—'}",
            f"Variant:                {breakdown.get('variant', '') or '—'}",
            "",
            f"Railroad:               {info.get('railroad', '') or '—'}",
            f"Location:               {info.get('location', '') or '—'}",
            f"Primary person:         {info.get('person', '') or '—'}",
        ]
        body.insert("1.0", "\n".join(lines))
        body.configure(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))

    def setup_tray_icon(self):
        if pystray is None or self.tray_icon is not None:
            return
        try:
            icon_image = Image.new("RGB", (64, 64), "white")
            # Simple high-contrast document icon without requiring an external asset.
            for x in range(14, 50):
                for y in range(10, 54):
                    if x in {14, 49} or y in {10, 53}:
                        icon_image.putpixel((x, y), (20, 43, 74))
            menu = pystray.Menu(
                pystray.MenuItem("Open RAILY SmartScan", lambda icon, item: self.root.after(0, self.show_from_tray)),
                pystray.MenuItem("Start Sorting", lambda icon, item: self.root.after(0, self.start)),
                pystray.MenuItem("Stop Sorting", lambda icon, item: self.root.after(0, self.stop)),
                pystray.MenuItem("Quit", lambda icon, item: self.root.after(0, self.quit_app)),
            )
            self.tray_icon = pystray.Icon("SmartScanSorter", icon_image, "RAILY — SmartScan", menu)
            try:
                self.tray_icon.run_detached()
            except Exception:
                threading.Thread(target=self.tray_icon.run, daemon=True, name="SmartScanTray").start()
        except Exception as exc:
            self.tray_icon = None
            log(f"Tray setup warning: {exc}")

    def show_from_tray(self):
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def on_close_window(self):
        self.sync()
        if CFG.get("minimize_to_tray", False):
            if self.tray_icon is not None:
                self.root.withdraw()
                self.note("SmartScan is still running in the Windows system tray.")
            else:
                self.root.iconify()
                self.note("System-tray support is unavailable; SmartScan was minimized to the taskbar instead.")
            return
        self.quit_app()

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        try:
            self.sync()
        except Exception:
            pass
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def build_windows_app(self):
        """Build a one-file windowed EXE on the user's Windows PC via PyInstaller."""
        self.sync()

        def worker():
            try:
                check = subprocess.run(
                    [sys.executable, "-m", "PyInstaller", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if check.returncode != 0:
                    raise RuntimeError(
                        "PyInstaller is not installed in this Python environment.\n\n"
                        "Install it once with:  py -m pip install pyinstaller"
                    )

                command = [
                    sys.executable, "-m", "PyInstaller",
                    "--noconfirm", "--clean", "--onefile", "--windowed",
                    "--name", "SmartScan Sorter",
                    str(Path(__file__).resolve())
                ]
                result = subprocess.run(
                    command,
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout)[-4000:])

                exe = APP_DIR / "dist" / "SmartScan Sorter.exe"
                self.root.after(0, lambda: messagebox.showinfo(
                    "Windows App Built",
                    f"SmartScan EXE created successfully.\n\n{exe}",
                    parent=self.root
                ))
                self.note(f"Windows app built: {exe}")
            except Exception as exc:
                self.root.after(0, lambda exc=exc: messagebox.showerror(
                    "Build Windows App",
                    str(exc),
                    parent=self.root
                ))

        threading.Thread(target=worker, daemon=True, name="SmartScanExeBuilder").start()

    def restore_stable_backup(self):
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        archive = filedialog.askopenfilename(
            title="Restore SmartScan Stable Backup",
            initialdir=str(BACKUP_ROOT),
            filetypes=[("SmartScan backups", "*.zip"), ("All files", "*.*")]
        )
        if not archive:
            return
        if not messagebox.askyesno(
            "Restore Stable Backup",
            "Restore SmartScan learning/settings from this backup?\n\n"
            "A safety copy of the current state will be created first. Documents are not changed.",
            parent=self.root
        ):
            return

        try:
            BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safety = BACKUP_ROOT / f"SmartScan_PRE_RESTORE_{stamp}.zip"
            state_paths = [
                CONFIG_FILE, LEARNING_FILE, INDEX_FILE, DATE_PROFILE_FILE,
                DOCUMENT_DATE_FILE, FILING_HISTORY_FILE, STATS_FILE, RAILY_MEMORY_FILE
            ]
            with zipfile.ZipFile(safety, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for state_path in state_paths:
                    if state_path.exists():
                        zf.write(state_path, arcname=state_path.name)

            with zipfile.ZipFile(archive, "r") as zf:
                names = set(zf.namelist())
                for state_path in state_paths:
                    if state_path.name in names:
                        state_path.write_bytes(zf.read(state_path.name))

            # Reload mutable dictionaries so the restored state is immediately active.
            global CFG, LEARNED, INDEX, DATE_PROFILES, DOCUMENT_DATES, FILING_HISTORY, STATS, RAILY_MEMORY
            restored_cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
            CFG.clear(); CFG.update(DEFAULT_CONFIG); CFG.update(restored_cfg)
            restored = load_json(LEARNING_FILE, {"version": 66, "profiles": []})
            LEARNED.clear(); LEARNED.update(restored); LEARNED.setdefault("profiles", [])
            restored = load_json(INDEX_FILE, {"version": 66, "files": {}})
            INDEX.clear(); INDEX.update(restored); INDEX.setdefault("files", {})
            restored = load_json(DATE_PROFILE_FILE, {"version": 51, "families": {}})
            DATE_PROFILES.clear(); DATE_PROFILES.update(restored); DATE_PROFILES.setdefault("families", {})
            restored = load_json(DOCUMENT_DATE_FILE, {"version": 56, "documents": {}})
            DOCUMENT_DATES.clear(); DOCUMENT_DATES.update(restored); DOCUMENT_DATES.setdefault("documents", {})
            restored = load_json(FILING_HISTORY_FILE, {"version": 61, "entries": []})
            FILING_HISTORY.clear(); FILING_HISTORY.update(restored); FILING_HISTORY.setdefault("entries", [])
            restored = load_json(STATS_FILE, {"version": 67, "days": {}, "lifetime": {}, "recognitions": []})
            STATS.clear(); STATS.update(restored); STATS.setdefault("days", {}); STATS.setdefault("lifetime", {}); STATS.setdefault("recognitions", [])
            restored = load_json(RAILY_MEMORY_FILE, {"version": 71, "people": {}, "railroads": {}, "locations": {}, "document_overrides": {}, "updates": []})
            RAILY_MEMORY.clear(); RAILY_MEMORY.update(restored)
            RAILY_MEMORY.setdefault("people", {}); RAILY_MEMORY.setdefault("railroads", {}); RAILY_MEMORY.setdefault("locations", {}); RAILY_MEMORY.setdefault("document_overrides", {}); RAILY_MEMORY.setdefault("updates", [])

            RESTORE_MARKER_FILE.write_text(f"Restored {archive} at {datetime.now().isoformat()}\n", encoding="utf-8")
            self.refresh_persistent_dashboard()
            self.note(f"Stable backup restored: {Path(archive).name}")
            messagebox.showinfo(
                "Restore Complete",
                f"Backup restored.\n\nSafety copy created:\n{safety}",
                parent=self.root
            )
        except Exception as exc:
            messagebox.showerror("Restore Stable Backup", f"Restore failed:\n\n{exc}", parent=self.root)

    def open_duplicate_review_center(self):
        win = tk.Toplevel(self.root)
        win.title("Duplicate Review Center")
        _fit_learning_window(win, 1120, 650)
        win.transient(self.root)

        manager_footer = ttk.Frame(win)
        manager_footer.pack(side="bottom", fill="x")
        table_frame = ttk.Frame(win)
        table_frame.pack(fill="both", expand=True, padx=10, pady=6)
        tree = ttk.Treeview(
            table_frame,
            columns=("file", "size", "match", "score", "details"),
            show="headings",
            selectmode="browse"
        )
        for column, heading, width in (
            ("file", "Duplicate Candidate", 280),
            ("size", "Size", 90),
            ("match", "Possible Original", 260),
            ("score", "Match", 90),
            ("details", "Reason", 330),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        row_paths = {}
        comparison = {}

        def refresh():
            row_paths.clear(); comparison.clear()
            for item in tree.get_children():
                tree.delete(item)
            root = Path(CFG["duplicates"])
            root.mkdir(parents=True, exist_ok=True)
            files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED]
            files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            for index, path in enumerate(files):
                iid = str(index)
                row_paths[iid] = path
                try:
                    size = f"{path.stat().st_size / 1024:.0f} KB"
                except Exception:
                    size = ""
                tree.insert("", "end", iid=iid, values=(path.name, size, "Not compared", "", "Click Compare Selected"))

        def selected_path():
            selection = tree.selection()
            return row_paths.get(selection[0]) if selection else None

        def compare_selected():
            path = selected_path()
            if not path:
                return
            if _defer_review_ocr(win, path, compare_selected, selected_path):
                return
            iid = tree.selection()[0]
            tree.set(iid, "details", "Comparing...")
            win.update_idletasks()
            try:
                text = get_cached_ocr(path)
                category, _, _ = classify(text, classification_filename_for(path))
                family, _, _ = resolve_family(category, text, path)
                dt, _, _, _ = detect_family_date_fast(category, family, text, path=path)
                duplicate = find_duplicate(path, text, category, family, document_date=dt, skip_exact=False)
                if duplicate:
                    kind, existing, score, details = duplicate
                    comparison[str(path)] = existing
                    tree.set(iid, "match", existing.name)
                    tree.set(iid, "score", f"{score:.0%}")
                    tree.set(iid, "details", f"{kind}: {details}")
                else:
                    tree.set(iid, "match", "No strong match")
                    tree.set(iid, "score", "")
                    tree.set(iid, "details", "No indexed original met duplicate thresholds")
            except Exception as exc:
                tree.set(iid, "details", f"Compare error: {exc}")

        def open_selected():
            path = selected_path()
            if path:
                try:
                    os.startfile(str(path))
                except Exception:
                    self.open_folder(path.parent)

        def open_original():
            path = selected_path()
            original = comparison.get(str(path)) if path else None
            if original and Path(original).exists():
                try:
                    os.startfile(str(original))
                except Exception:
                    self.open_folder(Path(original).parent)

        def keep_both():
            path = selected_path()
            if not path:
                return
            if _defer_review_ocr(win, path, keep_both, selected_path):
                return
            try:
                text = get_cached_ocr(path)
                category, _, _ = classify(text, classification_filename_for(path))
                family, _, _ = resolve_family(category, text, path)
                visual_match = raily_match_visual_document(path)
                metadata = raily_extract_metadata(text, visual_match=visual_match)
                destination = build_destination(
                    category, family,
                    railroad=metadata.get("railroad", ""),
                    location=metadata.get("location", "")
                )
                destination.mkdir(parents=True, exist_ok=True)
                target = unique_path(destination / path.name)
                shutil.move(str(path), str(target))
                index_document(
                    target, text, category, family,
                    railroad=metadata.get("railroad", ""),
                    location=metadata.get("location", ""),
                    person=metadata.get("person", "")
                )
                record_family_duplicate_history(
                    target,
                    text,
                    category,
                    family
                )
                self.note(f"Duplicate Review: kept both; restored {target.name} to Sorted.")
                refresh()
            except Exception as exc:
                messagebox.showerror("Keep Both", str(exc), parent=win)

        def not_duplicate():
            path = selected_path()
            if not path:
                return
            try:
                destination = Path(CFG["review"]) / "Not Duplicates"
                destination.mkdir(parents=True, exist_ok=True)
                target = unique_path(destination / path.name)
                shutil.move(str(path), str(target))
                self.note(f"Duplicate Review: marked not duplicate: {target.name}")
                refresh()
            except Exception as exc:
                messagebox.showerror("Not a Duplicate", str(exc), parent=win)

        def delete_selected():
            path = selected_path()
            if not path:
                return
            if not messagebox.askyesno(
                "Delete Duplicate",
                f"Delete this duplicate candidate?\n\n{path.name}\n\nThe sorted original is not touched.",
                parent=win
            ):
                return
            try:
                if send2trash is not None:
                    send2trash(str(path))
                else:
                    path.unlink()
                self.note(f"Duplicate Review: deleted duplicate candidate {path.name}")
                refresh()
            except Exception as exc:
                messagebox.showerror("Delete Duplicate", str(exc), parent=win)

        buttons = ttk.Frame(manager_footer)
        buttons.pack(fill="x", padx=10, pady=(4, 10))
        for text, command, style in (
            ("Compare Selected", compare_selected, "Primary.TButton"),
            ("Open Duplicate", open_selected, "Action.TButton"),
            ("Open Original", open_original, "Action.TButton"),
            ("Keep Both", keep_both, "Action.TButton"),
            ("Not a Duplicate", not_duplicate, "Action.TButton"),
            ("Delete Duplicate", delete_selected, "Action.TButton"),
        ):
            ttk.Button(buttons, text=text, style=style, command=command).pack(side="left", padx=3)
        ttk.Button(buttons, text="Refresh", style="Small.TButton", command=refresh).pack(side="right")
        refresh()

        _learning_tree_scrollbars(tree)
        _flow_learning_actions(buttons)

    # ----------------------------------------------------------------------
    # v61 Management Center
    # ----------------------------------------------------------------------

    def refresh_windows_integration_status(self):
        parts = [
            "Desktop shortcut: " + ("READY ✓" if _desktop_shortcut_path().exists() else "not installed"),
            "Start with Windows: " + ("ON ✓" if _startup_shortcut_path().exists() else "OFF"),
        ]

        if hasattr(self, "windows_integration_status"):
            self.windows_integration_status.set("   •   ".join(parts))

    def create_desktop_shortcut(self):
        try:
            shortcut = create_windows_shortcut(_desktop_shortcut_path(), minimized=False)
            self.note(f"Desktop shortcut created: {shortcut}")

            messagebox.showinfo(
                "Desktop Shortcut",
                "SmartScan Sorter was added to your Desktop.\n\n"
                "Double-click it just like a normal app.",
                parent=self.root
            )
        except Exception as exc:
            messagebox.showerror(
                "Desktop Shortcut",
                f"Could not create the Desktop shortcut:\n\n{exc}",
                parent=self.root
            )

        self.refresh_windows_integration_status()

    def enable_windows_startup(self):
        try:
            self.auto_start_sorting.set(True)
            CFG["auto_start_sorting_on_launch"] = True
            save_config()

            create_windows_shortcut(_startup_shortcut_path(), minimized=True)

            self.note(
                "Start with Windows enabled — SmartScan will launch minimized and begin sorting automatically."
            )

            messagebox.showinfo(
                "Start with Windows",
                "Enabled.\n\nThe next time you sign into Windows, SmartScan will "
                "start minimized in the taskbar and automatically watch Incoming.",
                parent=self.root
            )
        except Exception as exc:
            messagebox.showerror(
                "Start with Windows",
                f"Could not enable Windows startup:\n\n{exc}",
                parent=self.root
            )

        self.refresh_windows_integration_status()

    def disable_windows_startup(self):
        try:
            path = _startup_shortcut_path()

            if path.exists():
                path.unlink()

            self.note("Start with Windows disabled.")
        except Exception as exc:
            messagebox.showerror(
                "Start with Windows",
                f"Could not disable Windows startup:\n\n{exc}",
                parent=self.root
            )

        self.refresh_windows_integration_status()

    def create_stable_backup(self):
        self.sync()

        try:
            BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            archive = BACKUP_ROOT / f"SmartScan_STABLE_{stamp}.zip"

            candidates = [
                current_application_path(),
                CONFIG_FILE,
                LEARNING_FILE,
                INDEX_FILE,
                DATE_PROFILE_FILE,
                DOCUMENT_DATE_FILE,
                FILING_HISTORY_FILE,
                STATS_FILE,
                RAILY_MEMORY_FILE,
                LEGACY_CLEAN_LEARNING,
            ]

            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                added = set()

                for path in candidates:
                    path = Path(path)

                    if not path.exists() or not path.is_file():
                        continue

                    key = str(path.resolve()).lower()

                    if key in added:
                        continue

                    added.add(key)
                    zf.write(path, arcname=path.name)

            self.note(f"Stable backup created: {archive.name}")

            messagebox.showinfo(
                "Stable Backup",
                f"Backup created successfully.\n\n{archive}",
                parent=self.root
            )
        except Exception as exc:
            messagebox.showerror(
                "Stable Backup",
                f"Backup failed:\n\n{exc}",
                parent=self.root
            )

    def undo_last_filing(self):
        candidate = None

        for entry in reversed(FILING_HISTORY.get("entries", [])):
            if entry.get("undone"):
                continue

            target = Path(entry.get("target_path", ""))

            if target.exists():
                candidate = entry
                break

        if not candidate:
            messagebox.showinfo(
                "Undo Last Filing",
                "There is no recent SmartScan filing available to undo.",
                parent=self.root
            )
            return

        target = Path(candidate["target_path"])

        if not messagebox.askyesno(
            "Undo Last Filing",
            f"Send this document back to Incoming for correction?\n\n{target.name}\n\n"
            "SmartScan will pause it and open Teach/Review.",
            parent=self.root
        ):
            return

        try:
            incoming = Path(CFG["incoming"])
            incoming.mkdir(parents=True, exist_ok=True)

            original_name = Path(candidate.get("source_path", target.name)).name
            restored = unique_path(incoming / original_name)

            shutil.move(str(target), str(restored))

            for key, value in list(INDEX.get("files", {}).items()):
                indexed_path = Path(value.get("path", ""))

                try:
                    same = indexed_path.resolve() == target.resolve()
                except Exception:
                    same = str(indexed_path).casefold() == str(target).casefold()

                if same:
                    INDEX["files"].pop(key, None)

            save_index()
            update_saved_manual_date_path(restored)

            candidate["undone"] = True
            candidate["undone_at"] = datetime.now().isoformat(timespec="seconds")
            candidate["restored_path"] = str(restored)
            save_filing_history()

            self.engine.paused.add(self.engine.key(restored))

            self.note(
                f"Undo Last Filing: returned {restored.name} to Incoming for Teach/Review."
            )

            self.request_teach(
                str(restored),
                "Undo Last Filing — review and correct this document before filing again.",
                candidate.get("category", ""),
                candidate.get("family", "")
            )

        except Exception as exc:
            messagebox.showerror(
                "Undo Last Filing",
                f"Could not undo the filing:\n\n{exc}",
                parent=self.root
            )

    def health_check(self):
        win = tk.Toplevel(self.root)
        win.title("RAILY SmartScan Health Check")
        win.geometry("760x560")
        win.transient(self.root)

        C = self.ui_colors

        header = tk.Frame(win, bg=C["navy"], padx=16, pady=12)
        header.pack(fill="x")

        tk.Label(
            header,
            text="RAILY SmartScan Health Check",
            bg=C["navy"],
            fg="white",
            font=("Segoe UI", 16, "bold")
        ).pack(anchor="w")

        body = tk.Text(
            win,
            wrap="word",
            bg=C["surface"],
            fg=C["text"],
            relief="flat",
            padx=14,
            pady=12,
            font=("Segoe UI", 10)
        )
        body.pack(fill="both", expand=True, padx=10, pady=10)

        body.tag_configure("ok", foreground=C["success"], font=("Segoe UI", 10, "bold"))
        body.tag_configure("bad", foreground=C["error"], font=("Segoe UI", 10, "bold"))
        body.tag_configure("warn", foreground=C["warning"], font=("Segoe UI", 10, "bold"))

        def row(ok, title, detail="", warning=False):
            tag = "warn" if warning else ("ok" if ok else "bad")
            symbol = "✓" if ok else "✕"
            body.insert("end", f"{symbol}  {title}\n", tag)

            if detail:
                body.insert("end", f"    {detail}\n\n")
            else:
                body.insert("end", "\n")

        tess = str(pytesseract.pytesseract.tesseract_cmd)
        tess_ok = False
        tess_detail = tess

        try:
            flags = (
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )

            result = subprocess.run(
                [tess, "--version"],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=flags
            )

            tess_ok = result.returncode == 0

            if result.stdout:
                tess_detail = result.stdout.splitlines()[0]
        except Exception as exc:
            tess_detail = str(exc)

        row(tess_ok, "Tesseract OCR", tess_detail)

        for key in ("incoming", "sorted", "review", "duplicates"):
            folder = Path(CFG[key])
            ok = False
            detail = str(folder)

            try:
                folder.mkdir(parents=True, exist_ok=True)
                test = folder / ".smartscan_write_test"
                test.write_text("ok", encoding="utf-8")
                test.unlink()
                ok = True
            except Exception as exc:
                detail += " — " + str(exc)

            row(ok, f"{key.title()} folder", detail)

        state_files = [
            ("Config", CONFIG_FILE),
            ("Document learning", LEARNING_FILE),
            ("Date profiles", DATE_PROFILE_FILE),
            ("Saved manual dates", DOCUMENT_DATE_FILE),
            ("Duplicate index", INDEX_FILE),
            ("Dashboard stats", STATS_FILE),
            ("RAILY memory", RAILY_MEMORY_FILE),
        ]

        for label, path in state_files:
            try:
                if path.exists():
                    json.loads(path.read_text(encoding="utf-8"))

                row(True, label, str(path))
            except Exception as exc:
                row(False, label, str(exc))

        row(True, "PDF engine", "PyMuPDF is loaded.")

        visual_profiles = sum(
            1 for profile in LEARNED.get("profiles", [])
            if profile.get("visual_signature") or profile.get("visual_examples")
        )
        row(
            bool(CFG.get("raily_visual_ai_enabled", True)),
            "RAILY full-page visual learning",
            f"{visual_profiles} learned document type(s) currently have whole-page visual memory.",
            warning=not bool(CFG.get("raily_visual_ai_enabled", True))
        )
        row(
            bool(CFG.get("raily_route_filing", True)),
            "Railroad → Location routing",
            "Enabled" if CFG.get("raily_route_filing", True) else "Disabled / legacy folder mode",
            warning=not bool(CFG.get("raily_route_filing", True))
        )
        manifest_configured = bool(str(CFG.get("raily_update_manifest_url", "")).strip())
        row(
            True,
            "RAILY safe updater",
            ("Trusted manifest configured" if manifest_configured else "Ready, but no trusted manifest URL is configured yet"),
            warning=not manifest_configured
        )

        watcher_ok = self.engine._watcher_is_alive()
        row(
            watcher_ok,
            "Live watcher",
            "Running" if watcher_ok else "Not currently running",
            warning=not watcher_ok
        )

        worker_ok = self.engine._process_worker_is_alive()
        row(
            worker_ok,
            "Sequential processor",
            "Running" if worker_ok else "Not currently running",
            warning=not worker_ok
        )

        desktop_ok = _desktop_shortcut_path().exists()
        row(
            desktop_ok,
            "Desktop shortcut",
            str(_desktop_shortcut_path()),
            warning=not desktop_ok
        )

        startup_ok = _startup_shortcut_path().exists()
        row(
            True,
            "Start with Windows",
            "Enabled" if startup_ok else "Disabled (optional)"
        )

        body.configure(state="disabled")

    def open_learning_manager(self):
        win = tk.Toplevel(self.root)
        win.title("RAILY Document Learning Manager — v71")
        _fit_learning_window(win, 1320, 650)
        win.transient(self.root)

        manager_footer = ttk.Frame(win)
        manager_footer.pack(side="bottom", fill="x")
        table_frame = ttk.Frame(win)
        table_frame.pack(fill="both", expand=True, padx=10, pady=6)
        tree = ttk.Treeview(
            table_frame,
            columns=(
                "category", "family", "railroad", "location", "visual", "examples",
                "successes", "strength", "confidence", "dateareas", "lastseen"
            ),
            show="headings",
            selectmode="extended"
        )

        for column, heading, width in (
            ("category", "Category", 120),
            ("family", "Learned Type", 165),
            ("railroad", "Railroad", 130),
            ("location", "Location", 150),
            ("visual", "Full Page", 75),
            ("examples", "OCR Examples", 85),
            ("successes", "Auto Files", 75),
            ("strength", "Strength", 80),
            ("confidence", "Last %", 65),
            ("dateareas", "Date Areas", 75),
            ("lastseen", "Last Seen", 165),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")

        tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        row_profiles = {}

        def refresh():
            row_profiles.clear()
            for item in tree.get_children():
                tree.delete(item)

            profiles = list(LEARNED.get("profiles", []))
            profiles.sort(key=lambda p: (
                str(p.get("category", "")).casefold(),
                str(p.get("parent_family", "") or p.get("family", "")).casefold(),
                str(p.get("variant_name", "")).casefold(),
            ))

            for row_number, profile in enumerate(profiles):
                iid = str(row_number)
                row_profiles[iid] = profile
                family_for_dates = clean_optional_name(profile.get("parent_family", "")) or profile.get("family", "")
                areas = len(_date_profile_regions(profile.get("category", ""), family_for_dates))
                examples = max(
                    len(_learned_fingerprint_examples(profile)),
                    len(_learned_structure_examples(profile))
                )
                strength_label, _ = _profile_strength(profile)
                visual_examples = len(profile.get("visual_examples", []) or [])
                if not visual_examples and profile.get("visual_signature"):
                    visual_examples = 1
                tree.insert(
                    "", "end", iid=iid,
                    values=(
                        profile.get("category", ""),
                        profile.get("family", ""),
                        profile.get("railroad", ""),
                        profile.get("location", ""),
                        visual_examples,
                        examples,
                        profile.get("auto_successes", 0),
                        strength_label,
                        profile.get("last_confidence", ""),
                        areas,
                        profile.get("last_seen", profile.get("updated_at", "")),
                    )
                )

        def selected_profiles():
            return [row_profiles[iid] for iid in tree.selection() if iid in row_profiles]

        def selected_one():
            rows = selected_profiles()
            if len(rows) != 1:
                messagebox.showinfo("Learning Manager", "Select exactly one learned type for this action.", parent=win)
                return None
            return rows[0]

        def rename_selected():
            profile = selected_one()
            if not profile:
                return
            old = clean_name(profile.get("family", ""))
            new = simpledialog.askstring(
                "Rename Learned Type",
                "New learned document-type name:",
                initialvalue=old,
                parent=win
            )
            if not new:
                return
            new = clean_name(new)
            category = profile.get("category", "")
            profile["family"] = new
            profile["updated_at"] = datetime.now().isoformat(timespec="seconds")

            if not clean_optional_name(profile.get("parent_family", "")):
                old_key = _date_profile_key(category, old)
                new_key = _date_profile_key(category, new)
                if old_key in DATE_PROFILES.get("families", {}) and new_key not in DATE_PROFILES.get("families", {}):
                    dp = DATE_PROFILES["families"].pop(old_key)
                    dp["family"] = new
                    DATE_PROFILES["families"][new_key] = dp
                    save_date_profiles()

                for entry in DOCUMENT_DATES.get("documents", {}).values():
                    if entry.get("category") == category and clean_name(entry.get("family", "")).casefold() == old.casefold():
                        entry["family"] = new
                save_document_dates()

                for entry in INDEX.get("files", {}).values():
                    if entry.get("category") == category and clean_name(entry.get("family", "")).casefold() == old.casefold():
                        entry["family"] = new
                save_index()

            save_learning()
            self.note(f"Learning Manager: renamed {old} to {new}.")
            refresh()

        def set_variant():
            profile = selected_one()
            if not profile:
                return
            parent = simpledialog.askstring(
                "Parent Family",
                "Parent family used for filing (example: LENTEGRITY):",
                initialvalue=profile.get("parent_family", "") or profile.get("family", ""),
                parent=win
            )
            if parent is None:
                return
            variant = simpledialog.askstring(
                "Variant Name",
                "Variant/layout name (example: PayNearMe or Statement):",
                initialvalue=profile.get("variant_name", ""),
                parent=win
            )
            if variant is None:
                return
            profile["parent_family"] = clean_optional_name(parent)
            profile["variant_name"] = clean_optional_name(variant)
            profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_learning()
            self.note(
                f"Learning Manager: {profile.get('family')} assigned to parent "
                f"{profile.get('parent_family') or profile.get('family')}"
                + (f" / variant {profile.get('variant_name')}" if profile.get("variant_name") else "")
                + "."
            )
            refresh()

        def retrain_selected():
            profile = selected_one()
            if not profile:
                return
            filename = filedialog.askopenfilename(
                title="Choose a sample to reinforce this learned type",
                filetypes=[("Supported documents", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"), ("All files", "*.*")]
            )
            if not filename:
                return
            filing_family = clean_optional_name(profile.get("parent_family", "")) or profile.get("family", "")
            teach_family(filename, profile.get("category", ""), filing_family, railroad=profile.get("railroad", ""), location=profile.get("location", ""))
            self.note(f"Learning Manager: reinforced {filing_family} with another sample.")
            refresh()

        def merge_selected():
            rows = selected_profiles()
            if not rows:
                return
            category = rows[0].get("category", "")
            if any(row.get("category", "") != category for row in rows):
                messagebox.showerror("Merge Learning", "Only learned types in the same category can be merged.", parent=win)
                return
            target_name = simpledialog.askstring(
                "Merge Learning",
                "Merge selected learning into which family name?",
                initialvalue=clean_optional_name(rows[0].get("parent_family", "")) or rows[0].get("family", ""),
                parent=win
            )
            if not target_name:
                return
            target_name = clean_name(target_name)

            target = None
            for profile in LEARNED.get("profiles", []):
                if profile.get("category") == category and clean_name(profile.get("family", "")).casefold() == target_name.casefold():
                    target = profile
                    break
            if target is None:
                target = rows[0]
                target["family"] = target_name
                target["parent_family"] = ""
                target["variant_name"] = ""

            for source in list(rows):
                if source is target:
                    continue
                for key in ("fingerprint_examples", "structure_examples", "visual_examples"):
                    merged = list(target.get(key, []) or []) + list(source.get(key, []) or [])
                    if key == "fingerprint_examples":
                        current_key = "fingerprint"
                    elif key == "structure_examples":
                        current_key = "printed_structure"
                    else:
                        current_key = "visual_signature"
                    if source.get(current_key):
                        merged.append(source.get(current_key))
                    unique = []
                    seen = set()
                    for item in reversed(merged):
                        try:
                            marker = json.dumps(item, sort_keys=True)
                        except Exception:
                            continue
                        if marker in seen:
                            continue
                        seen.add(marker); unique.append(item)
                        if len(unique) >= 12:
                            break
                    unique.reverse()
                    target[key] = unique

                target["date_labels"] = sorted(set(target.get("date_labels", []) or []) | set(source.get("date_labels", []) or []))
                target["corrections"] = int(target.get("corrections", 0)) + int(source.get("corrections", 0))
                target["auto_successes"] = int(target.get("auto_successes", 0)) + int(source.get("auto_successes", 0))
                target["success_streak"] = max(int(target.get("success_streak", 0)), int(source.get("success_streak", 0)))
                if source in LEARNED["profiles"]:
                    LEARNED["profiles"].remove(source)

            target["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_learning()
            self.note(f"Learning Manager: merged selected profiles into {target_name}.")
            refresh()

        def reset_growth():
            profile = selected_one()
            if not profile:
                return
            profile["auto_successes"] = 0
            profile["success_streak"] = 0
            profile["last_confidence"] = 0
            save_learning()
            refresh()

        def explain_selected():
            profile = selected_one()
            if not profile:
                return
            strength, strength_score = _profile_strength(profile)
            family_for_dates = clean_optional_name(profile.get("parent_family", "")) or profile.get("family", "")
            body = (
                f"Category: {profile.get('category', '')}\n"
                f"Learned type: {profile.get('family', '')}\n"
                f"Parent family: {profile.get('parent_family', '') or '—'}\n"
                f"Variant: {profile.get('variant_name', '') or '—'}\n\n"
                f"Training examples: {max(len(_learned_fingerprint_examples(profile)), len(_learned_structure_examples(profile)))}\n"
                f"Manual teachings: {profile.get('corrections', 0)}\n"
                f"Successful automatic filings: {profile.get('auto_successes', 0)}\n"
                f"Success streak: {profile.get('success_streak', 0)}\n"
                f"Strength: {strength} ({strength_score}/100)\n"
                f"Last recognized confidence: {profile.get('last_confidence', 0)}%\n"
                f"Last seen: {profile.get('last_seen', '') or '—'}\n"
                f"Date areas: {len(_date_profile_regions(profile.get('category', ''), family_for_dates))}"
            )
            messagebox.showinfo("Learned Type Details", body, parent=win)

        def delete_selected():
            rows = selected_profiles()
            if not rows:
                return
            names = ", ".join(profile.get("family", "") for profile in rows[:5])
            if len(rows) > 5:
                names += f" and {len(rows) - 5} more"
            if not messagebox.askyesno(
                "Delete Learned Type",
                f"Delete learning for {len(rows)} selected type(s)?\n\n{names}\n\nDocuments will not be deleted.",
                parent=win
            ):
                return
            for profile in rows:
                if profile in LEARNED.get("profiles", []):
                    LEARNED["profiles"].remove(profile)
            save_learning()
            self.note(f"Learning Manager: deleted {len(rows)} learned profile(s).")
            refresh()

        buttons = ttk.Frame(manager_footer)
        buttons.pack(fill="x", padx=10, pady=(4, 10))
        actions = (
            ("Rename", rename_selected, "Action.TButton"),
            ("Set Parent / Variant", set_variant, "Primary.TButton"),
            ("Reinforce with Sample", retrain_selected, "Action.TButton"),
            ("Merge Selected", merge_selected, "Action.TButton"),
            ("Details", explain_selected, "Action.TButton"),
            ("Reset Growth", reset_growth, "Action.TButton"),
            ("Delete", delete_selected, "Action.TButton"),
        )
        for text, command, style in actions:
            ttk.Button(buttons, text=text, style=style, command=command).pack(side="left", padx=3)
        ttk.Button(buttons, text="Refresh", style="Small.TButton", command=refresh).pack(side="right")
        refresh()

        _learning_tree_scrollbars(tree)
        _flow_learning_actions(buttons)

    def open_date_profile_manager(self):
        win = tk.Toplevel(self.root)
        win.title("Date Profile Manager — v68")
        _fit_learning_window(win, 1080, 580)
        win.transient(self.root)

        manager_footer = ttk.Frame(win)
        manager_footer.pack(side="bottom", fill="x")
        table_frame = ttk.Frame(win)
        table_frame.pack(fill="both", expand=True, padx=10, pady=6)
        tree = ttk.Treeview(
            table_frame,
            columns=("category", "family", "areas", "preferred", "required", "updated"),
            show="headings",
            selectmode="browse"
        )

        for column, heading, width in (
            ("category", "Category", 170),
            ("family", "Document Type", 250),
            ("areas", "Date Areas", 90),
            ("preferred", "Filename Date Label", 220),
            ("required", "Required", 85),
            ("updated", "Updated", 180),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")

        tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        row_keys = {}

        def refresh():
            row_keys.clear()
            for item in tree.get_children():
                tree.delete(item)

            for row_number, (key, profile) in enumerate(sorted(
                DATE_PROFILES.get("families", {}).items(),
                key=lambda pair: (
                    str(pair[1].get("category", "")).casefold(),
                    str(pair[1].get("family", "")).casefold()
                )
            )):
                iid = str(row_number)
                row_keys[iid] = key
                tree.insert(
                    "", "end", iid=iid,
                    values=(
                        profile.get("category", ""),
                        profile.get("family", ""),
                        len(profile.get("date_regions", [])),
                        profile.get("preferred_date_label", "") or "Auto",
                        "Yes" if profile.get("date_required") else "No",
                        profile.get("updated_at", profile.get("created_at", "")),
                    )
                )

        def selected_key():
            selection = tree.selection()
            return row_keys.get(selection[0]) if selection else None

        def set_preferred_label():
            key = selected_key()
            if not key:
                return
            profile = DATE_PROFILES["families"].get(key, {})
            current = profile.get("preferred_date_label", "")
            value = simpledialog.askstring(
                "Filename Date Label",
                "Which printed date should control the filename?\n\n"
                "Examples: Due Date, Statement Date, Pay Date, Invoice Date, Service Date, Period Ending\n\n"
                "Leave blank to return to automatic selection.",
                initialvalue=current,
                parent=win
            )
            if value is None:
                return
            profile["preferred_date_label"] = clean_name(value)
            profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_date_profiles()
            self.note(
                f"Date Profile: {profile.get('family')} filename date set to "
                f"{profile.get('preferred_date_label') or 'automatic selection'}."
            )
            refresh()

        def toggle_required():
            key = selected_key()
            if not key:
                return
            profile = DATE_PROFILES["families"].get(key, {})
            profile["date_required"] = not bool(profile.get("date_required"))
            profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_date_profiles()
            refresh()

        def delete_selected():
            key = selected_key()
            if not key:
                return
            profile = DATE_PROFILES["families"].get(key, {})
            if not messagebox.askyesno(
                "Delete Date Profile",
                f"Delete all date locations/preferences for:\n\n"
                f"{profile.get('category')} → {profile.get('family')}?",
                parent=win
            ):
                return
            DATE_PROFILES["families"].pop(key, None)
            save_date_profiles()
            self.note("One isolated date profile was deleted.")
            refresh()

        buttons = ttk.Frame(manager_footer)
        buttons.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(buttons, text="Set Filename Date Label", style="Primary.TButton", command=set_preferred_label).pack(side="left")
        ttk.Button(buttons, text="Toggle Date Required", style="Action.TButton", command=toggle_required).pack(side="left", padx=5)
        ttk.Button(buttons, text="Delete Selected Date Profile", style="Action.TButton", command=delete_selected).pack(side="left", padx=5)
        ttk.Button(buttons, text="Refresh", style="Small.TButton", command=refresh).pack(side="right")
        refresh()

        _learning_tree_scrollbars(tree)
        _flow_learning_actions(buttons)

    def open_manual_date_manager(self):
        win = tk.Toplevel(self.root)
        win.title("Saved Manual Date Manager")
        _fit_learning_window(win, 1050, 560)
        win.transient(self.root)

        manager_footer = ttk.Frame(win)
        manager_footer.pack(side="bottom", fill="x")
        table_frame = ttk.Frame(win)
        table_frame.pack(fill="both", expand=True, padx=10, pady=6)
        tree = ttk.Treeview(
            table_frame,
            columns=("fingerprint", "date", "category", "family", "filename"),
            show="headings",
            selectmode="browse"
        )

        for column, heading, width in (
            ("fingerprint", "Fingerprint", 135),
            ("date", "Saved Date", 120),
            ("category", "Category", 160),
            ("family", "Document Type", 210),
            ("filename", "Last Filename", 300),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")

        tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        row_keys = {}

        def refresh():
            row_keys.clear()

            for item in tree.get_children():
                tree.delete(item)

            docs = DOCUMENT_DATES.get("documents", {})

            for row_number, (key, entry) in enumerate(
                sorted(
                    docs.items(),
                    key=lambda pair: str(pair[1].get("updated_at", "")),
                    reverse=True
                )
            ):
                iid = str(row_number)
                row_keys[iid] = key

                tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(
                        key.replace("sha256:", "")[:12],
                        entry.get("date", ""),
                        entry.get("category", ""),
                        entry.get("family", ""),
                        entry.get("last_filename", ""),
                    )
                )

        def selected_key():
            selection = tree.selection()
            return row_keys.get(selection[0]) if selection else None

        def edit_selected():
            key = selected_key()

            if not key:
                return

            entry = DOCUMENT_DATES["documents"].get(key, {})

            value = simpledialog.askstring(
                "Edit Saved Date",
                f"Enter the corrected date for this exact document:\n\n"
                f"{entry.get('last_filename', '')}",
                initialvalue=entry.get("date", ""),
                parent=win
            )

            if value is None:
                return

            dt = parse_date(value)

            if not dt:
                messagebox.showerror(
                    "Saved Date",
                    "That date could not be read.",
                    parent=win
                )
                return

            entry["date"] = dt.strftime("%Y-%m-%d")
            entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_document_dates()
            self.note("One persistent manual document date was corrected.")
            refresh()

        def delete_selected():
            key = selected_key()

            if not key:
                return

            entry = DOCUMENT_DATES["documents"].get(key, {})

            if not messagebox.askyesno(
                "Delete Saved Date",
                "Delete the remembered manual date for this exact document?\n\n"
                f"{entry.get('last_filename', '')}",
                parent=win
            ):
                return

            DOCUMENT_DATES["documents"].pop(key, None)
            save_document_dates()
            self.note("One persistent manual document date was deleted.")
            refresh()

        buttons = ttk.Frame(manager_footer)
        buttons.pack(fill="x", padx=10, pady=(4, 10))

        ttk.Button(
            buttons,
            text="Edit Selected Date",
            style="Primary.TButton",
            command=edit_selected
        ).pack(side="left")

        ttk.Button(
            buttons,
            text="Delete Selected Date",
            style="Action.TButton",
            command=delete_selected
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Refresh",
            style="Small.TButton",
            command=refresh
        ).pack(side="right")

        refresh()

        _learning_tree_scrollbars(tree)
        _flow_learning_actions(buttons)

    def reset_date_profiles(self):
        if not messagebox.askyesno(
            "Reset Date Profiles",
            (
                "Clear ALL v51 date-location profiles?\n\n"
                "Document-type learning and documents will not be deleted."
            )
        ):
            return

        DATE_PROFILES["families"] = {}
        DATE_PROFILES["version"] = 62
        save_date_profiles()

        self.note(
            "All v51 date profiles reset. Document-type learning was preserved."
        )


    def reset_learning(self):
        if not messagebox.askyesno(
            "Reset v40 Learning",
            "Clear v40 document-family learning?\n\n"
            "This does not delete any documents."
        ):
            return

        LEARNED["profiles"] = []
        # Keep migration marked complete so reset is actually a clean reset.
        LEARNED["migration_done"] = True

        save_learning()

        self.note(
            "v40 learning reset."
        )


def main():
    if not acquire_single_instance():
        root = tk.Tk()
        root.withdraw()

        messagebox.showinfo(
            "SmartScan Sorter",
            "SmartScan is already running.\n\n"
            "Check the Windows taskbar for the existing SmartScan window."
        )

        root.destroy()
        return

    try:
        seed_family_duplicate_history_from_index()
    except Exception as exc:
        log(f"v68 family duplicate history seed warning: {exc}")

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
