#!/usr/bin/env python3
"""Check artwork against the physical limits of puff (raised) screen printing.

Puff ink expands roughly 2-3x in the dryer. Anything thinner than about 3.2 mm
closes into a blob, and any gap narrower than about 3 mm bridges shut. Both
failures only show up on the finished garment, after the money is spent, so the
art gets audited here instead.

Method: rasterise the SVG at its real print size, then run a morphological
opening on the ink (radius = min_stroke/2) and on the background
(radius = min_gap/2). Whatever the opening removes is what the ink will destroy:
a stroke too thin to survive, or a gap too tight to stay open.

One correction matters. An opening also shaves every sharp convex corner - about
0.2*r^2 of area at each right angle - and a rounded corner is what puff ink does
anyway, not a defect. Those shavings are slivers, a pixel or two thick, while a
stroke that actually fails loses its whole width. So a loss only counts once it
survives being eroded by r/3, which discards corner rounding without resorting
to a per-shape area fudge factor.

Requires pillow + numpy:  pip3 install pillow numpy

Usage:
    python3 puff_check.py ../design/emblem-a-arch.svg --width-mm 220
    python3 puff_check.py art.svg --width-mm 90 --min-stroke 3.2 --min-gap 3.0
"""

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from render import render  # noqa: E402

MM_PER_INCH = 25.4


def _shift_min(m, dy, dx):
    out = np.ones_like(m)
    h, w = m.shape
    ys, ye = max(0, dy), min(h, h + dy)
    xs, xe = max(0, dx), min(w, w + dx)
    out[ys:ye, xs:xe] = m[ys - dy : ye - dy, xs - dx : xe - dx]
    return out


def erode(mask, r):
    """Erode by an octagon approximating a disc of radius r pixels."""
    m = mask
    for i in range(r):
        step = [m, _shift_min(m, 1, 0), _shift_min(m, -1, 0), _shift_min(m, 0, 1), _shift_min(m, 0, -1)]
        if i % 2:  # every other pass also takes the diagonals -> octagon, not diamond
            step += [_shift_min(m, 1, 1), _shift_min(m, 1, -1), _shift_min(m, -1, 1), _shift_min(m, -1, -1)]
        m = np.minimum.reduce(step)
    return m


def dilate(mask, r):
    return ~erode(~mask, r)


def opening(mask, r):
    return dilate(erode(mask, r), r)


def real_loss(mask, r, ignore=None):
    """What an opening at radius r destroys, with shaved corners discounted."""
    lost = mask & ~opening(mask, r)
    if ignore is not None:
        lost &= ~ignore
    return erode(lost, max(1, r // 3))


def boxes(mask, px_per_mm, limit=8):
    """Bounding boxes (in mm) of the connected blobs in a sparse boolean mask."""
    todo = set(zip(*np.nonzero(mask)))
    out = []
    while todo and len(out) < limit:
        seed = todo.pop()
        stack, pts = [seed], [seed]
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1), (y + 1, x + 1), (y - 1, x - 1), (y + 1, x - 1), (y - 1, x + 1)):
                if (ny, nx) in todo:
                    todo.discard((ny, nx))
                    stack.append((ny, nx))
                    pts.append((ny, nx))
        ys = [p[0] for p in pts]
        xs = [p[1] for p in pts]
        out.append(
            {
                "x_mm": round(float(min(xs)) / px_per_mm, 1),
                "y_mm": round(float(min(ys)) / px_per_mm, 1),
                "w_mm": round(float(max(xs) - min(xs) + 1) / px_per_mm, 1),
                "h_mm": round(float(max(ys) - min(ys) + 1) / px_per_mm, 1),
            }
        )
    return out, len(todo) > 0


def measure(mask, px_per_mm, cap_mm, ignore=None):
    """Binary-search the opening radius for the thinnest surviving feature, in mm.

    Returns None when the art is thicker than the cap everywhere - there is no
    useful number to print then, only "comfortably over".
    """
    lo, hi = 0, max(1, int(round(cap_mm / 2 * px_per_mm)))
    if not real_loss(mask, hi, ignore).any():
        return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if real_loss(mask, mid, ignore).any():
            hi = mid
        else:
            lo = mid
    return round(2 * lo / px_per_mm, 1)


def check(svg, width_mm, min_stroke=3.2, min_gap=3.0, dpi=150, verbose=True):
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "audit.png"
        w_mm, h_mm = render(svg, out_png=str(png), dpi=dpi, width_mm=width_mm)
        alpha = np.array(Image.open(png).convert("RGBA"))[:, :, 3]

    px_per_mm = dpi / MM_PER_INCH
    ink = alpha > 127
    if not ink.any():
        raise SystemExit("no ink found in the rendered art")

    r_stroke = max(1, int(round(min_stroke / 2 * px_per_mm)))
    r_gap = max(1, int(round(min_gap / 2 * px_per_mm)))

    # The artboard edge is background too, and the opening always shaves it.
    # Losses there are an artefact of the crop, not a gap that can bridge shut.
    bg = ~ink
    edge = np.zeros_like(bg)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    edge = dilate(edge, r_gap + 2)

    thin = real_loss(ink, r_stroke)
    tight = real_loss(bg, r_gap, ignore=edge)

    ys, xs = np.nonzero(ink)
    report = {
        "art": str(svg),
        "print_size_mm": (round(w_mm, 1), round(h_mm, 1)),
        "ink_bbox_mm": tuple(
            round(float(v) / px_per_mm, 1)
            for v in (xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
        ),
        "ink_coverage_pct": round(100 * ink.sum() / ink.size, 1),
        "min_stroke_mm": measure(ink, px_per_mm, cap_mm=4 * min_stroke),
        "min_gap_mm": measure(bg, px_per_mm, cap_mm=4 * min_gap, ignore=edge),
    }
    report["thin_boxes"], _ = boxes(thin, px_per_mm)
    report["tight_boxes"], _ = boxes(tight, px_per_mm)
    report["pass"] = not thin.any() and not tight.any()

    if verbose:
        def fmt(v, limit):
            return f"over {4 * limit:g} mm" if v is None else f"{v} mm"

        print(f"{Path(svg).name} @ {report['print_size_mm'][0]}x{report['print_size_mm'][1]} mm, {dpi}dpi")
        print(f"  ink bbox {report['ink_bbox_mm']} mm, coverage {report['ink_coverage_pct']}%")
        print(
            f"  thinnest ink {fmt(report['min_stroke_mm'], min_stroke):>13}  (limit {min_stroke} mm)"
            + (f"  <-- FAIL at {report['thin_boxes']}" if thin.any() else "")
        )
        print(
            f"  tightest gap {fmt(report['min_gap_mm'], min_gap):>13}  (limit {min_gap} mm)"
            + (f"  <-- FAIL at {report['tight_boxes']}" if tight.any() else "")
        )
        print(f"  {'PASS' if report['pass'] else 'FAIL'}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg", nargs="+")
    ap.add_argument("--width-mm", type=float, required=True, help="printed width of the art")
    ap.add_argument("--min-stroke", type=float, default=3.2)
    ap.add_argument("--min-gap", type=float, default=3.0)
    ap.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args()
    ok = True
    for svg in a.svg:
        ok &= check(svg, a.width_mm, a.min_stroke, a.min_gap, a.dpi)["pass"]
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
