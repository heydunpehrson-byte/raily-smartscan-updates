from __future__ import annotations

import re
import os
from datetime import datetime
from pathlib import Path

try:
    import pytesseract
    from PIL import Image, ImageOps, ImageFilter, ImageEnhance
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
        image = Image.open(source).convert("RGB")
        image = ImageOps.exif_transpose(image)
        # Remove small scan borders, normalize contrast, upscale small scans,
        # and compare native/grayscale/threshold OCR candidates.
        margin = max(2, int(min(image.size) * .02)); image = image.crop((margin, margin, image.width-margin, image.height-margin))
        if min(image.size) < 1200: image = image.resize((image.width*2, image.height*2), Image.Resampling.LANCZOS)
        gray = ImageOps.autocontrast(ImageOps.grayscale(image)).filter(ImageFilter.SHARPEN)
        contrast = ImageEnhance.Contrast(gray).enhance(1.35)
        threshold = contrast.point(lambda p: 255 if p > 165 else 0)
        candidates = []
        for candidate_image in (image, contrast, threshold):
            for config in ("--psm 6", "--psm 11", "--psm 3"):
                data = pytesseract.image_to_data(candidate_image, config=config, output_type=pytesseract.Output.DICT)
                parts = [t.strip() for t, c in zip(data.get("text", []), data.get("conf", [])) if t.strip() and float(c) >= 0]
                confidence = sum(max(0.0, float(c)) for c in data.get("conf", []) if float(c) >= 0) / max(1, sum(float(c) >= 0 for c in data.get("conf", [])))
                candidates.append((confidence, " ".join(parts)))
        confidence, text = max(candidates, key=lambda item: item[0], default=(0.0, ""))
    else:
        confidence = 0.0
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
    cleaned = re.sub(r"\s+", " ", text).strip()
    return {"text": text, "raw_text": text, "cleaned_text": cleaned, "ocr_confidence": round(float(locals().get("confidence", 0.0)), 1), "railroad": railroad, "location": location, "name": name,
            "date": date, "category": classification, "confidence": confidence,
            "review_required": not classification}
