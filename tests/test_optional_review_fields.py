from pathlib import Path

def test_only_document_type_is_required_for_review_approval():
    intake=(Path(__file__).parents[1]/"raily/brain/intake.py").read_text(encoding="utf-8-sig")
    gui=(Path(__file__).parents[1]/"raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    assert '"Document Type/Category is required"' in intake
    assert 'missing = ["document_type"]' in gui
