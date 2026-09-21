"""Original FramePulse mark; rebuild PNG/ICO from geometric primitives."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent / "assets"
ROOT.mkdir(parents=True, exist_ok=True)
size, scale = 256, 4
image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)


def xy(values):
    return tuple(round(value * scale) for value in values)


draw.rounded_rectangle(xy((8, 8, 248, 248)), radius=54 * scale, fill="#141a24")
draw.rounded_rectangle(xy((14, 14, 242, 242)), radius=48 * scale, outline="#374452", width=2 * scale)
for points in [((79, 63), (54, 63), (54, 97)), ((177, 63), (202, 63), (202, 97)),
               ((54, 162), (54, 193), (79, 193)), ((202, 162), (202, 193), (177, 193))]:
    draw.line([xy(point) for point in points], fill="#ced8e5", width=9 * scale, joint="curve")
draw.line([xy(point) for point in [(49, 132), (89, 132), (112, 99), (143, 163), (167, 132), (207, 132)]],
          fill="#f3bd54", width=12 * scale, joint="curve")
image = image.resize((size, size), Image.Resampling.LANCZOS)
image.save(ROOT / "icon.png")
image.save(ROOT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
(ROOT / "icon.svg").write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
<rect x="8" y="8" width="240" height="240" rx="54" fill="#141a24"/>
<rect x="14" y="14" width="228" height="228" rx="48" fill="none" stroke="#374452" stroke-width="2"/>
<path d="M79 63H54V97M177 63H202V97M54 162V193H79M202 162V193H177" fill="none" stroke="#ced8e5" stroke-width="9"/>
<path d="M49 132H89L112 99L143 163L167 132H207" fill="none" stroke="#f3bd54" stroke-width="12" stroke-linejoin="round"/>
</svg>''', encoding="utf-8")
