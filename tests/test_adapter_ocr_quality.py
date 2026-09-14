from pathlib import Path
from raily.engine import adapter

def test_ocr_adapter_reports_raw_cleaned_text_and_confidence(tmp_path, monkeypatch):
    p=tmp_path/'scan.txt'; p.write_text('IRAIL SERVICES GROUP LLC\nStart Count On Duty Time Off Total Starts')
    result=adapter.process_document(p)
    assert result['raw_text'] == result['text'] and 'cleaned_text' in result and 'ocr_confidence' in result
    assert result['category'] == 'Work Log / Start Count Log'
