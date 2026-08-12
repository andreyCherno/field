#!/usr/bin/env python3
"""Render an SVG at a real-world print size to a 300dpi transparent PNG and a vector PDF.

No imaging libraries are installed in this environment (no PIL, no cairosvg, no
inkscape, no imagemagick), but the Playwright Chromium build is present, so the
renderer drives it directly:

    SVG -> HTML shell -> chrome --headless --screenshot   -> PNG (alpha kept)
                      -> chrome --headless --print-to-pdf -> PDF (vector)

Two details bite anyone repeating this:

* Headless Chromium's --window-size is the *window*, not the viewport - this
  build loses a constant 86x87 px to window furniture and offsets the page 43 px
  from the left. Hard-coding those numbers would rot, so calibrate() measures
  them at runtime and the screenshot is cropped back to the exact pixel size.
* The PNG that Chromium writes carries no pHYs chunk, so every print shop tool
  reads it as 72dpi no matter how many pixels it holds. patch_dpi() inserts it.

Requires pillow (for the crop):  pip3 install pillow

Usage:
    python3 render.py art.svg out.png --width-mm 220 [--dpi 300] [--pdf out.pdf]
"""

import argparse
import binascii
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
MM_PER_INCH = 25.4

BASE_FLAGS = [
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--hide-scrollbars",
    "--disable-dev-shm-usage",
    "--disable-lcd-text",
]


def svg_size_mm(svg_text):
    """Read the artboard size out of the SVG's width/height attributes (mm)."""
    import re

    head = svg_text[: svg_text.index(">") + 1]
    out = []
    for attr in ("width", "height"):
        m = re.search(rf'{attr}\s*=\s*"([0-9.]+)\s*mm"', head)
        if not m:
            raise SystemExit(f"SVG root must carry {attr} in mm, e.g. width=\"220mm\"")
        out.append(float(m.group(1)))
    return out[0], out[1]


def html_shell(svg_text, w_mm, h_mm, background="transparent"):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
  @page {{ size: {w_mm}mm {h_mm}mm; margin: 0; }}
  html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; background: {background}; }}
  /* The SVG carries its size in mm, but a screenshot viewport is sized in
     device pixels at the target dpi. Letting the SVG fill the viewport is what
     makes "220 mm at 300 dpi" come out as 2598 px of art rather than Chromium's
     96-dpi idea of 220 mm. */
  svg {{ display: block; width: 100%; height: 100%; }}
</style></head><body>{svg_text}</body></html>"""


def run_chrome(args):
    proc = subprocess.run([CHROME] + BASE_FLAGS + args, capture_output=True, text=True)
    # Chromium always complains about dbus in this container; only a non-zero
    # exit or a missing output file is a real failure.
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"chromium exited {proc.returncode}")


_CHROME_BOX = None


def calibrate(probe=1000):
    """Measure where the page lands inside a --window-size screenshot.

    Returns (x0, y0, chrome_w, chrome_h): the page's top-left corner in the
    screenshot, and how much bigger the window has to be than the wanted image.
    """
    global _CHROME_BOX
    if _CHROME_BOX:
        return _CHROME_BOX
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "cal.html"
        shot = Path(td) / "cal.png"
        # The marker has to be an element, not a body background: a background on
        # html/body propagates to the whole canvas and would measure the window
        # rather than the viewport.
        page.write_text(
            "<!doctype html><html><head><style>html,body{margin:0;padding:0;width:100%;"
            "height:100%;background:#ffffff}#m{width:100%;height:100%;background:#ff00ff}"
            "</style></head><body><div id=m></div></body></html>"
        )
        run_chrome([f"--screenshot={shot}", f"--window-size={probe},{probe}", f"file://{page}"])
        px = Image.open(shot).convert("RGB").load()
        img = Image.open(shot)
        w, h = img.size
        cols = [x for x in range(w) if px[x, h // 2] == (255, 0, 255)]
        rows = [y for y in range(h) if px[w // 2, y] == (255, 0, 255)]
        if not cols or not rows:
            raise SystemExit("calibration failed: no page area found in the screenshot")
        _CHROME_BOX = (cols[0], rows[0], probe - len(cols), probe - len(rows))
    return _CHROME_BOX


def patch_dpi(png_path, dpi):
    """Insert a pHYs chunk so the PNG declares its real physical resolution."""
    data = Path(png_path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{png_path} is not a PNG")
    ihdr_end = 8 + 8 + struct.unpack(">I", data[8:12])[0] + 4
    ppm = int(round(dpi / MM_PER_INCH * 1000))
    body = b"pHYs" + struct.pack(">IIB", ppm, ppm, 1)
    chunk = struct.pack(">I", len(body) - 4) + body + struct.pack(">I", binascii.crc32(body))
    rest = data[ihdr_end:]
    if rest[4:8] == b"pHYs":  # replace an existing one
        rest = rest[8 + struct.unpack(">I", rest[:4])[0] + 4 :]
    Path(png_path).write_bytes(data[:ihdr_end] + chunk + rest)


def png_info(png_path):
    data = Path(png_path).read_bytes()
    w, h = struct.unpack(">II", data[16:24])
    colour = data[25]
    dpi = None
    i = 8
    while i < len(data) - 8:
        ln = struct.unpack(">I", data[i : i + 4])[0]
        typ = data[i + 4 : i + 8]
        if typ == b"pHYs":
            ppm = struct.unpack(">I", data[i + 8 : i + 12])[0]
            dpi = round(ppm * MM_PER_INCH / 1000)
            break
        i += 12 + ln
    return {"px": (w, h), "colour_type": colour, "has_alpha": colour in (4, 6), "dpi": dpi}


def render(svg_path, out_png=None, out_pdf=None, dpi=300, width_mm=None, background="transparent"):
    svg_text = Path(svg_path).read_text()
    w_mm, h_mm = svg_size_mm(svg_text)
    if width_mm:  # scale the artboard, keeping aspect
        h_mm = h_mm * (width_mm / w_mm)
        w_mm = width_mm
        svg_text = svg_text.replace(
            svg_text[: svg_text.index(">") + 1],
            _resize_root(svg_text[: svg_text.index(">") + 1], w_mm, h_mm),
        )

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "page.html"
        page.write_text(html_shell(svg_text, w_mm, h_mm, background))
        if out_png:
            from PIL import Image

            px_w = int(round(w_mm / MM_PER_INCH * dpi))
            px_h = int(round(h_mm / MM_PER_INCH * dpi))
            x0, y0, chrome_w, chrome_h = calibrate()
            shot = Path(td) / "shot.png"
            flags = [f"--screenshot={shot}", f"--window-size={px_w + chrome_w},{px_h + chrome_h}"]
            if background == "transparent":
                flags.insert(0, "--default-background-color=00000000")
            run_chrome(flags + [f"file://{page}"])
            Image.open(shot).crop((x0, y0, x0 + px_w, y0 + px_h)).save(out_png)
            patch_dpi(out_png, dpi)
        if out_pdf:
            run_chrome(
                [
                    f"--print-to-pdf={Path(out_pdf).resolve()}",
                    "--no-pdf-header-footer",
                    f"file://{page}",
                ]
            )
    return w_mm, h_mm


def _resize_root(root_tag, w_mm, h_mm):
    import re

    root_tag = re.sub(r'width\s*=\s*"[0-9.]+\s*mm"', f'width="{w_mm:g}mm"', root_tag)
    return re.sub(r'height\s*=\s*"[0-9.]+\s*mm"', f'height="{h_mm:g}mm"', root_tag)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("png", nargs="?")
    ap.add_argument("--pdf")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--width-mm", type=float)
    ap.add_argument("--background", default="transparent")
    a = ap.parse_args()
    w, h = render(a.svg, a.png, a.pdf, a.dpi, a.width_mm, a.background)
    print(f"{a.svg}: {w:g}x{h:g} mm @ {a.dpi}dpi")
    if a.png:
        print(f"  {a.png}: {png_info(a.png)}")
    if a.pdf:
        print(f"  {a.pdf}: {Path(a.pdf).stat().st_size} bytes")


if __name__ == "__main__":
    main()
