import ast
from pathlib import Path

def test_review_workflow_exposes_queue_and_approval():
    intake = (Path(__file__).parents[1] / "raily/brain/intake.py").read_text(encoding="utf-8-sig")
    gui = (Path(__file__).parents[1] / "raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    assert '"/review"' in intake and '"/review/{job_id}"' in intake
    assert "show_review_queue" in gui and "JOB_REVIEW_APPROVED" in intake
