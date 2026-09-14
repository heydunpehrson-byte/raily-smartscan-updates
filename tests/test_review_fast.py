from pathlib import Path

def test_review_queue_does_not_run_synchronous_ocr():
    source=(Path(__file__).parents[1]/"raily/brain/intake.py").read_text(encoding="utf-8-sig")
    section=source[source.index('def review_queue'):source.index('def complete_review')]
    assert "process_document" not in section
    assert "persisted job metadata" in section
