"""Render RAILY's original signal/rail shield icons at Windows sizes."""
from pathlib import Path
from PIL import Image, ImageDraw


def build(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, accent, diagnostic in [('raily', '#E8993C', False), ('raily-diagnostics', '#58C4C5', True)]:
        image = Image.new('RGBA', (512, 512))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((18, 18, 494, 494), radius=105, fill='#101D29', outline=accent, width=13)
        draw.polygon([(110,390),(183,92),(223,92),(161,390)], fill=accent)
        draw.polygon([(351,390),(289,92),(329,92),(402,390)], fill=accent)
        for y, half in [(208,68),(268,86),(331,106),(389,124)]:
            draw.rounded_rectangle((256-half,y,256+half,y+17), radius=6, fill='#D7E2E9')
        draw.rounded_rectangle((221,67,291,173), radius=30, fill='#101D29', outline='#D7E2E9', width=8)
        draw.ellipse((240,88,272,120), fill=accent)
        if diagnostic:
            draw.ellipse((316,302,452,438), fill='#101D29', outline=accent, width=9)
            draw.line((345,370,370,395,421,337), fill=accent, width=15)
        image.save(root/f'{name}.png')
        image.save(root/f'{name}.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])

if __name__ == '__main__': build(Path(__file__).resolve().parents[1]/'assets')
