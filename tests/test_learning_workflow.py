from pathlib import Path

def test_learning_requires_explicit_confirmation_and_is_manageable():
    gui=(Path(__file__).parents[1]/"raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    intake=(Path(__file__).parents[1]/"raily/brain/intake.py").read_text(encoding="utf-8-sig")
    assert "Teach RAILY" in gui and "Apply to this document only" in gui and "Confirm learning" in gui
    assert "learned_rules" in intake and "LEARNED_RULE" in intake
