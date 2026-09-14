from pathlib import Path

def test_review_requires_required_fields_and_exposes_ocr_context():
    intake = (Path(__file__).parents[1] / "raily/brain/intake.py").read_text(encoding="utf-8-sig")
    gui = (Path(__file__).parents[1] / "raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    assert 'item["ocr"]' in intake and 'proposed_destination' in intake
    assert '"document_type", "date"' in intake
    assert "initialvalue=ocr.get" in gui and "required" in gui
