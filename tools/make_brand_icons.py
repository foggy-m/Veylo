"""Rasterize Veylo's original vector geometry into the extension PNG sizes."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "extension"


def make(size):
    scale = 8
    unit = size * scale / 64
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size * scale - 1, size * scale - 1), radius=18 * unit, fill="#15282C")
    draw.polygon([(x * unit, y * unit) for x, y in [(15.5, 20), (25.6, 20), (35, 44.5), (25.2, 44.5)]], fill="#F6F7F3")
    draw.polygon([(x * unit, y * unit) for x, y in [(38.3, 20), (49, 20), (37.2, 45), (26.7, 45)]], fill="#69DDC3")
    draw.ellipse((45 * unit, 10 * unit, 53 * unit, 18 * unit), fill="#F28C7D")
    return image.resize((size, size), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    for size in [16, 32, 48, 64, 128]:
        make(size).save(ROOT / "icons" / ("icon%d.png" % size))
    print("Generated Veylo icons: 16, 32, 48, 64, 128 px (original vector geometry).")
