"""Read-only page rendering. No OCR or database writes."""
import io
from pathlib import Path
from PIL import Image, ImageOps


def render_page(path, page=0):
    if page < 0:
        raise IndexError('Invalid page')
    if Path(path).suffix.lower() == '.pdf':
        import pymupdf
        with pymupdf.open(path) as pdf:
            count = len(pdf)
            if page >= count:
                raise IndexError('Page not found')
            sheet = pdf[page]
            scale = min(2, 2200 / max(sheet.rect.width, sheet.rect.height))
            pixels = sheet.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return pixels.tobytes('png'), count
    with Image.open(path) as source:
        count = getattr(source, 'n_frames', 1)
        if page >= count:
            raise IndexError('Page not found')
        source.seek(page)
        picture = ImageOps.exif_transpose(source).convert('RGB')
        picture.thumbnail((2200, 2200))
        output = io.BytesIO()
        picture.save(output, format='PNG')
        return output.getvalue(), count
