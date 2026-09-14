"""Regression checks for delayed Tk health callbacks."""
import ast
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "raily" / "client" / "workstation_gui.py"

def test_health_callback_is_generation_guarded_and_widget_safe():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RailyWorkstation")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    callback = ast.unparse(methods["update_brain_status"])
    assert "_view_generation" in callback
    assert "winfo_exists" in callback
    assert "TclError" in callback
    assert "after(5000" in callback
