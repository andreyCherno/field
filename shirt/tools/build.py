#!/usr/bin/env python3
"""Build the whole send-to-factory package from the emblem SVGs.

For each emblem this writes, into shirt/print/<name>/:
  <name>-art-300dpi.png   black art on transparent, at the real print size
  <name>-art.pdf          the same art as vector, for making the screen
  <name>-separation.png   1-bit black on white - what the screen is burned from
  SPEC.txt                sizes, placement, inks, and the audited limits

and into shirt/mockups/: the emblem on a tee in both colourways.

Run:  python3 build.py
"""

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import mockup  # noqa: E402
import puff_check  # noqa: E402
from render import patch_dpi, render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# printed width on the garment, front centre chest
EMBLEMS = {
    "emblem-a-arch": {"width_mm": 190, "note": "Arch"},
    "emblem-b-horizon": {"width_mm": 220, "note": "Horizon"},
    "emblem-c-peak": {"width_mm": 190, "note": "Peak"},
}
CHEST_WIDTH_MM = 90  # the small left-chest option, audited as well
DROP_MM = 110  # top of the print, measured down from the collar seam at centre front

SPEC = """PRINT SPEC — {note} ({name})
================================================================

ARTWORK
  Master (vector)      ../../design/{name}.svg   1 user unit = 1 mm at 100%
  Print file (raster)  {name}-art-300dpi.png     {px_w} x {px_h} px, 300 dpi, transparent
  Vector for screens   {name}-art.pdf
  Screen separation    {name}-separation.png     1-bit, black = ink

SIZE AND PLACEMENT (front, centre chest)
  Printed size         {w} x {h} mm
  Horizontal           centred on the front, left-right
  Vertical             top of the print {drop} mm below the collar seam at centre front
  Small alternative    {chest_w} mm wide, left chest, top 70 mm below the shoulder seam

PRINT METHOD
  Puff (raised / 3D foam) screen print, one colour, one screen.
  No halftones, no gradients, no second colour anywhere in this file.
  Cure to the ink maker's schedule; puff must rise evenly with no flat patches.

COLOURWAYS (two samples, same artwork)
  1. Black garment  PANTONE 19-0303 TCX Jet Black    + white puff  PANTONE 11-0601 TCX Bright White
  2. Off-white garment, approx #E9E2D4 (send two lab dips) + black puff PANTONE 19-0303 TCX Jet Black

AUDITED LIMITS (measured on the art, not estimated)
  Thinnest ink at {w} mm wide   {min_stroke_big}
  Tightest gap at {w} mm wide   {min_gap_big}
  Thinnest ink at {chest_w} mm wide    {min_stroke_small}
  Tightest gap at {chest_w} mm wide    {min_gap_small}
  Limits assumed: ink 3.2 mm minimum, gap 3.0 mm minimum, for puff expansion of 2-3x.

WHAT MUST NOT BE CHANGED
  Do not re-draw, re-trace, thicken or outline the artwork.
  Do not scale the two colourways differently - both are the same size.
  Do not add any brand name, care symbol or website to the print.
"""


def fmt(v, limit):
    return f"over {4 * limit:g} mm" if v is None else f"{v} mm"


def build_emblem(name, width_mm, note):
    svg = ROOT / "design" / f"{name}.svg"
    out = ROOT / "print" / name
    out.mkdir(parents=True, exist_ok=True)

    png = out / f"{name}-art-300dpi.png"
    pdf = out / f"{name}-art.pdf"
    w, h = render(svg, out_png=str(png), out_pdf=str(pdf), dpi=300, width_mm=width_mm)

    art = Image.open(png).convert("RGBA")
    flat = Image.new("RGB", art.size, "white")
    flat.paste(art, mask=art.split()[3])
    sep = out / f"{name}-separation.png"
    flat.convert("L").point(lambda v: 0 if v < 128 else 255, mode="1").save(sep)
    patch_dpi(sep, 300)

    big = puff_check.check(svg, width_mm, verbose=False)
    small = puff_check.check(svg, CHEST_WIDTH_MM, verbose=False)
    (out / "SPEC.txt").write_text(
        SPEC.format(
            name=name,
            note=note,
            w=round(w),
            h=round(h),
            px_w=art.size[0],
            px_h=art.size[1],
            drop=DROP_MM,
            chest_w=CHEST_WIDTH_MM,
            min_stroke_big=fmt(big["min_stroke_mm"], 3.2),
            min_gap_big=fmt(big["min_gap_mm"], 3.0),
            min_stroke_small=fmt(small["min_stroke_mm"], 3.2),
            min_gap_small=fmt(small["min_gap_mm"], 3.0),
        )
    )

    for cw in ("black", "cream"):
        mockup.render_tee(svg, str(ROOT / "mockups" / f"{name}-{cw}.png"), cw, width_mm)

    ok = big["pass"] and small["pass"]
    print(
        f"{name}: {round(w)}x{round(h)} mm, {art.size[0]}x{art.size[1]} px"
        f"  ink>={fmt(big['min_stroke_mm'], 3.2)} @{width_mm}mm, {fmt(small['min_stroke_mm'], 3.2)} @{CHEST_WIDTH_MM}mm"
        f"  {'PASS' if ok else 'FAIL'}"
    )
    return ok


def main():
    (ROOT / "mockups").mkdir(exist_ok=True)
    ok = all(build_emblem(name, **cfg) for name, cfg in EMBLEMS.items())
    mockup.contact_sheet(
        [str(ROOT / "design" / f"{n}.svg") for n in EMBLEMS],
        str(ROOT / "concepts" / "contact-sheet.png"),
        width_mm=200,
    )
    print("contact sheet -> concepts/contact-sheet.png")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
