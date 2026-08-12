#!/usr/bin/env python3
"""Put an emblem on a flat tee drawing, in both colourways, to judge scale.

The tee outline is drawn at garment scale - one user unit is one millimetre, and
the body is a size L boxy drop-shoulder flat: 580 mm across the chest, 720 mm
long. That is what makes the mockup worth anything: a 200 mm print is drawn 200
mm wide against a real 580 mm chest, so if it looks too big here it is too big.

Puff sits about 1.5 mm proud of the fabric, so the ink is drawn twice - a soft
offset copy underneath for the raised edge, then the ink itself on top.

Usage:
    python3 mockup.py ../design/emblem-a-arch.svg --out tee.png --width-mm 200
    python3 mockup.py ../design/*.svg --sheet ../concepts/contact-sheet.png
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from render import render  # noqa: E402

# A size L boxy drop-shoulder flat, in millimetres.
TEE = (
    "M370,120 L180,120 L60,250 L150,362 L180,300 L180,840 L760,840 L760,300 "
    "L790,362 L880,250 L760,120 L570,120 C560,178 380,178 370,120 Z"
)
COLLAR = "M370,120 C380,178 560,178 570,120"
NECK_CENTRE_Y = 168  # bottom of the neck drop; prints are measured down from here

COLOURWAYS = {
    "black": {"cloth": "#191919", "ink": "#F4F1EA", "shade": "#000000", "label": "black tee / white puff"},
    "cream": {"cloth": "#E9E2D4", "ink": "#1A1A1A", "shade": "#8C8677", "label": "off-white tee / black puff"},
}


def emblem_layer(svg_path, cx, top_y, width_mm):
    """Inline an emblem into the tee's coordinate system, centred on cx."""
    text = Path(svg_path).read_text()
    head = text[: text.index(">") + 1]
    vb = re.search(r'viewBox\s*=\s*"([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)"', head)
    if not vb:
        raise SystemExit(f"{svg_path}: no viewBox")
    vx, vy, vw, vh = (float(g) for g in vb.groups())
    body = text[text.index(">") + 1 : text.rindex("</svg>")]
    body = re.sub(r"<(title|desc)>.*?</\1>", "", body, flags=re.S)
    s = width_mm / vw
    tx = cx - width_mm / 2 - vx * s
    ty = top_y - vy * s
    return f'<g transform="translate({tx:.2f},{ty:.2f}) scale({s:.4f})">{body}</g>', vh * s


def tee_svg(svg_path, colourway, width_mm, drop_mm=110):
    c = COLOURWAYS[colourway]
    art, art_h = emblem_layer(svg_path, cx=470, top_y=NECK_CENTRE_Y + drop_mm, width_mm=width_mm)
    raised = art.replace("var(--ink,#000000)", c["shade"])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="940mm" height="960mm" viewBox="0 0 940 960">
  <rect width="940" height="960" fill="none"/>
  <path d="{TEE}" fill="{c['cloth']}" stroke="{c['shade']}" stroke-width="2" stroke-opacity="0.35"/>
  <path d="{COLLAR}" fill="none" stroke="{c['shade']}" stroke-width="9" stroke-opacity="0.45"/>
  <g opacity="0.45" transform="translate(1.6,1.6)">{raised}</g>  <!-- puff sits proud of the cloth -->
  <g fill="{c['ink']}">{art.replace('var(--ink,#000000)', c['ink'])}</g>
</svg>"""


def render_tee(svg_path, out_png, colourway, width_mm, px_wide=900):
    tmp = Path(out_png).with_suffix(".tee.svg")
    tmp.write_text(tee_svg(svg_path, colourway, width_mm))
    render(tmp, out_png=out_png, dpi=px_wide / (940 / 25.4), background="white")
    tmp.unlink()


def contact_sheet(svgs, out_png, width_mm, px_wide=1500):
    cells = []
    for svg in svgs:
        for cw in ("black", "cream"):
            inner = tee_svg(svg, cw, width_mm)
            inner = inner[inner.index(">") + 1 : inner.rindex("</svg>")]
            cells.append((Path(svg).stem, cw, inner))
    cols, rows = 2, len(svgs)
    cell_w, cell_h = 940, 1020
    parts = []
    for i, (name, cw, inner) in enumerate(cells):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        parts.append(
            f'<g transform="translate({x},{y})">{inner}'
            f'<text x="470" y="1000" text-anchor="middle" font-family="Helvetica,Arial" '
            f'font-size="26" fill="#666">{name} — {COLOURWAYS[cw]["label"]}</text></g>'
        )
    w, h = cols * cell_w, rows * cell_h
    sheet = Path(out_png).with_suffix(".sheet.svg")
    sheet.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
        f'viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="#FBFAF7"/>{"".join(parts)}</svg>'
    )
    render(sheet, out_png=out_png, dpi=px_wide / (w / 25.4), background="white")
    sheet.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg", nargs="+")
    ap.add_argument("--out", help="single-tee PNG to write")
    ap.add_argument("--sheet")
    ap.add_argument("--colourway", default="black", choices=list(COLOURWAYS))
    ap.add_argument("--width-mm", type=float, default=200, help="printed width on the garment")
    a = ap.parse_args()
    if a.sheet:
        contact_sheet(a.svg, a.sheet, a.width_mm)
        print(f"contact sheet -> {a.sheet}")
    else:
        render_tee(a.svg[0], a.out, a.colourway, a.width_mm)
        print(f"{a.svg[0]} -> {a.out} ({a.colourway}, {a.width_mm} mm wide)")


if __name__ == "__main__":
    main()
