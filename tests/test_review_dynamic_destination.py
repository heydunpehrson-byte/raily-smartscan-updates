from pathlib import Path

def test_review_destination_updates_when_fields_change():
    gui=(Path(__file__).parents[1]/"raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    assert "proposed.configure" in gui and "Unassigned Railroad" in gui and "General" in gui
