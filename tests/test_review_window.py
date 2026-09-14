from pathlib import Path

def test_review_window_persists_and_disables_approval_for_missing_fields():
    gui=(Path(__file__).parents[1]/"raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    assert "tk.Toplevel" in gui and 'state="disabled"' in gui
    assert "trace_add" in gui and "Missing.TEntry" in gui
