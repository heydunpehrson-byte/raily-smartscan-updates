from raily.engine.adapter import process_document

def test_blank_irail_template_classifies_from_static_anchors(tmp_path):
    form = tmp_path / "blank-work-log.txt"
    form.write_text("IRAIL SERVICES GROUP LLC\nName:\nStart Date:\nLocation:\nStart Count\nDate On Duty Time Off Job Hours Total Starts\n")
    result = process_document(form)
    assert result["category"] == "Work Log / Start Count Log"
    assert result["review_required"] is False
    assert result["name"] is None and result["railroad"] is None and result["location"] is None and result["date"] is None
