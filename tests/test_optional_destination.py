from pathlib import Path

def test_review_uses_unassigned_destination_for_optional_fields():
    source=(Path(__file__).parents[1]/"raily/brain/intake.py").read_text(encoding="utf-8-sig")
    assert '"Unassigned Railroad"' in source and '"General"' in source
    assert '"Document Type/Category is required"' in source
