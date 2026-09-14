from unittest.mock import Mock, patch
from raily.engine import adapter

def test_tesseract_configuration_prefers_explicit_executable(tmp_path):
    exe = tmp_path / "tesseract.exe"; exe.write_bytes(b"")
    fake = Mock()
    with patch.object(adapter, "pytesseract", fake):
        with patch.dict("os.environ", {"RAILY_TESSERACT_CMD": str(exe)}):
            assert adapter.configure_tesseract() == str(exe)
            assert fake.pytesseract.tesseract_cmd == str(exe)
