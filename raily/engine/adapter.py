from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

try:
    import pytesseract
    from PIL import Image
except Exception:  # OCR is optional in minimal server installs
    pytesseract = None
    Image = None

FIELD_LABELS = {
    "railroad": ("railroad", "railway", "company"),
    "location": ("location", "yard", "terminal"),
    "name": ("name", "employee", "conductor"),
    "date": ("start date", "date", "effective date"),
}

def _value(text, labels):
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:#-]\s*(.+)", text, re.I)
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
    classification = "Work Document" if text else None
    confidence = sum(bool(x) for x in (railroad, location, classification)) / 3
    return {"text": text, "railroad": railroad, "location": location, "name": name,
            "date": date, "category": classification, "confidence": confidence,
            "review_required": confidence < 1.0}
