import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
import pymupdf
from raily.brain.preview import render_page


class PreviewTests(unittest.TestCase):
    def test_image_exif_orientation_and_source_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder)/'scan.jpg'
            picture = Image.new('RGB', (120, 60), 'white')
            exif = picture.getexif(); exif[274] = 6
            picture.save(path, exif=exif)
            original = path.read_bytes()
            data, count = render_page(path)
            self.assertEqual(count, 1)
            self.assertEqual(Image.open(io.BytesIO(data)).size, (60, 120))
            self.assertEqual(path.read_bytes(), original)

    def test_pdf_pages_and_bounds(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder)/'pages.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page(width=100, height=200)
                pdf.new_page(width=300, height=100).set_rotation(90)
                pdf.save(path)
            first, count = render_page(path, 0)
            second, _ = render_page(path, 1)
            self.assertEqual(count, 2)
            self.assertNotEqual(first, second)
            self.assertEqual(Image.open(io.BytesIO(second)).size, (200, 600))
            with self.assertRaises(IndexError): render_page(path, 2)

if __name__ == '__main__':
    unittest.main()
