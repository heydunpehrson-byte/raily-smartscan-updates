from pathlib import Path

def test_review_endpoint_uses_stored_metadata_without_ocr_call():
    source=(Path(__file__).parents[1]/"raily/brain/intake.py").read_text(encoding="utf-8-sig")
    section=source[source.index('def review_queue'):source.index('def complete_review')]
    assert "ocr.get" not in section
    assert "json.loads(item.get(\"metadata_json\"" in section
    assert "proposed_destination" in section
