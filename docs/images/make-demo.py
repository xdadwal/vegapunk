"""Assembles demo.gif from the frames that make-demo.mjs recorded.

Usage: python3 make-demo.py <frames dir> [output.gif]   (needs Pillow)

Frames are scaled to 960x540 and reduced to an adaptive palette each, which keeps the GIF small.
A caption frame is added after the first "agent working" frame, because the real wait
for the answer is cut from the walkthrough.
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

SIZE = (960, 540)
COLOURS = 128


def caption(img: Image.Image, text: str) -> Image.Image:
    img = ImageEnhance.Brightness(img).enhance(0.45)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=26)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    w, h = right - left, bottom - top
    x, y = (SIZE[0] - w) // 2, (SIZE[1] - h) // 2
    draw.rounded_rectangle((x - 24, y - 18, x + w + 24, y + h + 22), 12, fill=(16, 18, 24))
    draw.text((x - left, y - top), text, font=font, fill=(240, 242, 246))
    return img


def main() -> None:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).with_name("demo.gif")
    meta = json.loads((src / "frames.json").read_text())

    images, durations = [], []
    working = [i for i, f in enumerate(meta["frames"]) if f["note"].startswith("agent working")]
    for i, f in enumerate(meta["frames"]):
        img = Image.open(src / f["file"]).convert("RGB").resize(SIZE, Image.Resampling.LANCZOS)
        images.append(img)
        durations.append(f["ms"])
        if working and i == working[0]:
            text = f"Wait shortened: the agent answered in {meta['seconds']} s"
            images.append(caption(img, text))
            durations.append(1400)

    # Each frame gets its own adaptive palette: a shared one loses the thin chart lines.
    frames = [
        img.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        for img in images
    ]

    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    print(f"{out}: {len(frames)} frames, {sum(durations) / 1000:.1f} s, {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
