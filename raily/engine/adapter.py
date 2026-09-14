from __future__ import annotations

import re
import os
from datetime import datetime
from pathlib import Path

try:
    import pytesseract
    from PIL import Image
except Exception:  # OCR is optional in minimal server installs
    pytesseract = None
    Image = None

def configure_tesseract():
    """Select an installed Tesseract explicitly; never rely on PATH."""
    if not pytesseract:
        return None
    configured = os.environ.get("RAILY_TESSERACT_CMD")
    candidates = [configured, r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            pytesseract.pytesseract.tesseract_cmd = str(Path(candidate))
            return str(Path(candidate))
    return None

TESSERACT_CMD = configure_tesseract()

FIELD_LABELS = {
    "railroad": ("railroad", "railway", "company"),
    "location": ("location", "yard", "terminal"),
    "name": ("name", "employee", "conductor"),
    "date": ("start date", "date", "effective date"),
}
STATIC_TEMPLATE_ANCHORS = ("irail services group llc", "start count", "on duty", "time off", "total starts")

def _value(text, labels):
    for label in labels:
        match = re.search(rf"{re.escape(label)}[ \t]*[:#-][ \t]*([^\r\n]+)", text, re.I)
        if match:
            value = match.group(1).strip().splitlines()[0].strip()
            if value:
                return value
    return None

def process_document(path: str | Path) -> dict:
    """Process one file without importing the legacy GUI application."""
    source = Path(path)
    text = ""
    if source.suffix.lower() in {".txt", ".csv"}:
        text = source.read_text(encoding="utf-8", errors="ignore")
    elif pytesseract and Image and source.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        text = pytesseract.image_to_string(Image.open(source))
    railroad = _value(text, FIELD_LABELS["railroad"])
    location = _value(text, FIELD_LABELS["location"])
    name = _value(text, FIELD_LABELS["name"])
    raw_date = _value(text, FIELD_LABELS["date"])
    date = None
    if raw_date:
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y-%m-%d"):
            try:
                date = datetime.strptime(raw_date, fmt).date().isoformat(); break
            except ValueError: pass
    normalized = re.sub(r"\s+", " ", text.casefold())
    anchor_hits = sum(anchor in normalized for anchor in STATIC_TEMPLATE_ANCHORS)
    classification = "Work Log / Start Count Log" if anchor_hits >= 2 else ("Work Document" if text else None)
    # Blank variable fields are expected for a template; static anchors provide
    # the confidence signal and never fabricate a person, railroad, or date.
    confidence = min(1.0, anchor_hits / 3) if anchor_hits else (1 / 3 if classification else 0)
    return {"text": text, "railroad": railroad, "location": location, "name": name,
            "date": date, "category": classification, "confidence": confidence,
            "review_required": not classification}
