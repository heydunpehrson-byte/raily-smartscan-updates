from pathlib import Path

def test_login_layout_is_scaling_aware_and_centered():
    source=(Path(__file__).parents[1]/"raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    assert "tk.call(\"tk\", \"scaling\")" in source
    assert "winfo_screenwidth" in source and "winfo_screenheight" in source
    assert "_set_login_geometry" in source
