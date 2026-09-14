import ast
from pathlib import Path

def test_retry_gui_requires_confirmation_and_uses_session_headers():
    source = (Path(__file__).parents[1] / "raily/client/workstation_gui.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RailyWorkstation")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "retry_job")
    code = ast.unparse(method)
    assert "askyesno" in code
    assert "self.auth_headers()" in code
    assert "/jobs/{job_id}/retry" in code
