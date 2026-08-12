#!/usr/bin/env python3
"""
Minimum ink-thickness measurement.

An earlier version of this script used a morphological opening and reported the
percentage of ink lost. That metric was wrong: an opening also rounds every
corner and shortens every terminal, so type -- which has an enormous perimeter
relative to its area -- lost 13% of its ink at a 0.1 mm kernel that offset litho
holds without difficulty. The number measured the shape of letters, not the
capability of the process.

This measures the thing that actually matters instead: the STEM WIDTH of every
face and size used, taken from the rasterised outlines. Horizontal scanlines are
run-length encoded and the 5th percentile of run lengths is reported as the stem
-- the percentile rather than the minimum, because curve extremes produce
one-pixel runs that are artefacts of sampling rather than real features.

There is no verdict column here on purpose. I do not have a manufacturer's
tolerance sheet, and inventing a threshold would be worse than having none: you
would plan against it. These are measurements to hand to the supplier with the
question "do you hold this?".
"""
from PIL import Image, ImageDraw
import make_artwork as A

RES = 200            # px per mm for glyph rasterisation


def flatten(cmds, steps=10):
    contours, cur, pos = [], [], (0.0, 0.0)
    for c in cmds:
        if c[0] == "m":
            if len(cur) > 2: contours.append(cur)
            pos = c[1]; cur = [pos]
        elif c[0] == "l":
            pos = c[1]; cur.append(pos)
        elif c[0] == "c":
            p0, (p1, p2, p3) = pos, c[1:]
            for i in range(1, steps + 1):
                t = i / steps; u = 1 - t
                cur.append((u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
                            u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1]))
            pos = p3
        elif c[0] == "h":
            if len(cur) > 2: contours.append(cur)
            cur = []
    if len(cur) > 2: contours.append(cur)
    return contours


def stem_width(face, size, chars):
    """5th-percentile horizontal ink run across the given characters, in mm."""
    runs = []
    for ch in chars:
        if ch in " ·":
            continue
        cmds = A.FACES[face].outline(ch, size, 0.0, 0.0)
        cts = flatten(cmds)
        if not cts:
            continue
        xs = [p[0] for ct in cts for p in ct]; ys = [p[1] for ct in cts for p in ct]
        w = int((max(xs) - min(xs)) * RES) + 4
        h = int((max(ys) - min(ys)) * RES) + 4
        if w < 3 or h < 3:
            continue
        img = Image.new("L", (w, h), 0)
        for ct in cts:                       # XOR = even-odd, correct for counters
            tmp = Image.new("L", (w, h), 0)
            ImageDraw.Draw(tmp).polygon(
                [((p[0] - min(xs)) * RES + 2, (max(ys) - p[1]) * RES + 2) for p in ct], fill=255)
            img = Image.frombytes("L", (w, h), bytes(
                a ^ b for a, b in zip(img.tobytes(), tmp.tobytes())))
        px = img.load()
        for y in range(h):
            run = 0
            for x in range(w):
                if px[x, y] > 127:
                    run += 1
                else:
                    if run: runs.append(run)
                    run = 0
            if run: runs.append(run)
    if not runs:
        return None
    runs.sort()
    return runs[max(0, int(len(runs) * 0.05))] / RES


def report(name, fn):
    A.RUNS.clear()
    _fills, rules, spots = fn()
    print(f"\n{name}")
    seen = {}
    for r in A.RUNS:
        key = (r["face"], round(r["size"], 2))
        seen.setdefault(key, set()).update(r["s"])
    for (face, size), chars in sorted(seen.items(), key=lambda kv: kv[0][1]):
        sw = stem_width(face, size, chars)
        sample = "".join(sorted(c for c in chars if c.isascii() and c.strip()))[:14]
        print(f"   type  {face:10s} {size:4.2f} mm em   stem {sw:.3f} mm   {sample}")
    for _t, x1, y1, x2, y2, w in rules:
        print(f"   rule  {'':10s} {'':4}          width {w:.3f} mm")
    for sp in spots:
        if sp[0] == "ring":
            print(f"   ring  {'':10s} {'':4}          width {sp[4]:.3f} mm")


if __name__ == "__main__":
    print(f"stem = 5th-percentile horizontal ink run, rasterised at {RES} px/mm")
    report("STAMP  (rubber die, one relief height)", A.stamp)
    report("SEAL   (offset / digital, kiss-cut)", A.seal)
    report("TAG    (offset / digital, guillotined)", A.tag_front)
    print("\nHand these to the supplier. The stamp is the one to ask about.")
