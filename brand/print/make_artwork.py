#!/usr/bin/env python3
"""
Print-ready artwork for the MERIDIAN INTAKE identity.

Artefacts
    tag-front, tag-back   40 x 70 mm hangtag        1/1 black, 3 mm bleed, crop marks
    stamp                 30 mm round rubber die    solid black only, no tints
    seal                  35 mm round kiss-cut      1/0 black, 2 mm bleed, CutContour path

All type is emitted as OUTLINED VECTOR PATHS. No font is referenced by any output
file, so nothing can substitute or reflow at the RIP.

Three production languages, deliberately not the same:

  The TAG is printed on a sheet and guillotined. It may carry tints of K, it gets
  bleed and crop marks, and the punch position rides on a named `Die` separation.

  The STAMP is not printed at all -- it is a die, cut from a single relief
  height. There is no such thing as 55% grey on a rubber stamp, so the artwork is
  100% K throughout. Tints here would not print lighter; they would print solid,
  or fill in, or drop out, and which of the three you get is the manufacturer's
  guess rather than your decision.

  The stamp's thinnest feature measures 0.28 mm (verify_thickness.py). That is a
  MEASUREMENT, not a certified pass: I have no tolerance sheet from a stamp
  maker, so there is no threshold in this file to check it against. Give the
  supplier the number and ask.

  The SEAL is printed and kiss-cut on a roll. A round cut wanders more than a
  straight guillotine, so its safe area is pulled further in than the tag's, and
  the cut is delivered as a `CutContour` spot path rather than as marks.
"""
import os
from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen

MM = 2.834645669291339
K100, K55, K30 = 1.0, 0.55, 0.30

FONTS = {
    "cond":      "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
    "cond_bold": "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
    "mono":      "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "mono_bold": "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
    "heb":       "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
}


class RecPen(BasePen):
    def __init__(self, glyphSet):
        super().__init__(glyphSet); self.cmds = []
    def _moveTo(self, p):              self.cmds.append(("m", p))
    def _lineTo(self, p):              self.cmds.append(("l", p))
    def _curveToOne(self, p1, p2, p3): self.cmds.append(("c", p1, p2, p3))
    def _closePath(self):              self.cmds.append(("h",))


class Face:
    def __init__(self, path):
        self.font = TTFont(path, fontNumber=0)
        self.upem = self.font["head"].unitsPerEm
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        self.hmtx = self.font["hmtx"]
        self.name = os.path.basename(path)

    def gname(self, ch):
        gn = self.cmap.get(ord(ch))
        if gn is None:
            raise SystemExit(f"FATAL: {self.name} lacks U+{ord(ch):04X} {ch!r}")
        return gn

    def advance(self, ch, size):
        return self.hmtx[self.gname(ch)][0] * size / self.upem

    def outline(self, ch, size, x, y):
        pen = RecPen(self.glyphs)
        self.glyphs[self.gname(ch)].draw(pen)
        s = size / self.upem
        return [(c[0],) if c[0] == "h" else
                (c[0],) + tuple((px * s + x, py * s + y) for px, py in c[1:])
                for c in pen.cmds]


FACES = {k: Face(v) for k, v in FONTS.items()}


def visual_order(s):
    """Logical -> visual for an RTL run; digit runs stay internally LTR."""
    toks, i = [], 0
    while i < len(s):
        if s[i].isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            toks.append(s[i:j]); i = j
        else:
            toks.append(s[i]); i += 1
    return "".join(reversed(toks))


RUNS = []   # every text run drawn, so verify_thickness.py can measure real stems


def text(s, face, size, x, y, ls=0.0, align="left", rtl=False, box_h=None):
    """(x, y) in mm, y measured DOWN from the artefact's trim top-left."""
    RUNS.append({"s": s, "face": face, "size": size})
    seq = visual_order(s) if rtl else s
    f = FACES[face]
    w = sum(f.advance(c, size) for c in seq) + ls * (len(seq) - 1) if seq else 0.0
    if align == "center": x -= w / 2.0
    elif align == "right": x -= w
    base = (box_h if box_h is not None else 0) - y
    cmds, cx = [], x
    for ch in seq:
        if ch != " ":
            cmds += f.outline(ch, size, cx, base)
        cx += f.advance(ch, size) + ls
    return cmds


# ---------------------------------------------------------------------------
# artefacts
# ---------------------------------------------------------------------------
TAG_W, TAG_H = 40.0, 70.0
HOLE = (20.0, 6.0, 4.0)

def tag_front():
    T = lambda *a, **k: text(*a, box_h=TAG_H, **k)
    fills, rules = [], []
    fills.append((K100, T("MERIDIAN", "cond_bold", 3.4, 20.0, 15.2, ls=0.75, align="center")))
    rules.append((K100, 4.0, 17.2, 36.0, 17.2, 0.28))
    fills.append((K55, T("מרידיאן · תל אביב", "heb", 2.1, 20.0, 20.3, align="center", rtl=True)))
    for label, y in (("INTAKE", 27.0), ("GRADE", 32.5), ("SIZE", 38.0), ("CHECKED", 43.5)):
        fills.append((K55, T(label, "mono", 2.0, 4.0, y, ls=0.10)))
        rules.append((K30, 4.0, y + 1.1, 36.0, y + 1.1, 0.20))
    fills.append((K55, T("NOTE", "mono", 2.0, 4.0, 49.5, ls=0.10)))
    rules.append((K30, 4.0, 53.0, 36.0, 53.0, 0.20))
    rules.append((K30, 4.0, 57.5, 36.0, 57.5, 0.20))
    fills.append((K55, T("ONE OF ONE", "mono", 1.8, 4.0, 61.3, ls=0.22)))
    fills.append((K55, T("ORIGIN", "mono", 1.8, 36.0, 61.3, ls=0.22, align="right")))
    hx, hy, hd = HOLE
    return fills, rules, [("circle", hx, hy, hd / 2, 0.25, True)]


GRADES = [
    ("A",  "כמו חדש, ללא סימני שימוש"),
    ("A−", "שימוש קל, ללא פגמים"),
    ("B+", "סימני שימוש נראים"),
    ("B",  "פגם אחד מתועד"),
    ("C",  "פגם משמעותי, מתומחר בהתאם"),
]

def tag_back():
    T = lambda *a, **k: text(*a, box_h=TAG_H, **k)
    fills, rules = [], []
    fills.append((K55, T("GRADE KEY", "mono", 1.8, 20.0, 12.5, ls=0.25, align="center")))
    fills.append((K100, T("מפתח דירוגים", "heb", 2.4, 20.0, 16.3, align="center", rtl=True)))
    rules.append((K100, 4.0, 18.3, 36.0, 18.3, 0.28))
    top, row = 18.3, (59.0 - 18.3) / 5.0
    for i, (letter, desc) in enumerate(GRADES):
        base = top + row * i + 5.2
        fills.append((K100, T(letter, "cond_bold", 2.9, 36.0, base, align="right")))
        fills.append((K100, T(desc, "heb", 2.15, 31.5, base, align="right", rtl=True)))
        if i < len(GRADES) - 1:
            rules.append((K30, 4.0, top + row * (i + 1), 36.0, top + row * (i + 1), 0.20))
    rules.append((K100, 4.0, 60.5, 36.0, 60.5, 0.28))
    fills.append((K100, T("החלפה תוך 14 יום עם התג", "heb", 2.0, 36.0, 63.5, align="right", rtl=True)))
    fills.append((K55, T("@meridian.tlv", "mono", 1.8, 36.0, 66.6, ls=0.12, align="right")))
    hx, hy, hd = HOLE
    return fills, rules, [("circle", hx, hy, hd / 2, 0.25, True)]


STAMP_D = 30.0

def stamp():
    """
    Rubber die. 100% K only -- there is no tint on a stamp.
    Bold faces throughout: the regular weights measured 0.17 mm at this size,
    against 0.28 mm for the bolds -- and rubber is the one process where that
    difference decides whether a letter survives a heavy press.
    """
    T = lambda *a, **k: text(*a, box_h=STAMP_D, **k)
    c = STAMP_D / 2.0
    fills, rules = [], []
    # Sizes set by measurement, not by eye. At 2.3 mm the mono stems came out at
    # 0.23 mm -- the thinnest thing on the die by a wide margin. Raised to 2.8 mm,
    # which lifts the stems to ~0.28 and still fits the chord at that height.
    # MERIDIAN grew with it so the hierarchy survives the change.
    fills.append((K100, T("CHECKED BY", "mono_bold", 2.8, c, 11.0, ls=0.16, align="center")))
    fills.append((K100, T("MERIDIAN", "cond_bold", 4.2, c, 17.0, ls=0.72, align="center")))
    rules.append((K100, 9.0, 19.2, 21.0, 19.2, 0.55))
    fills.append((K100, T("TEL AVIV", "mono_bold", 2.8, c, 23.0, ls=0.30, align="center")))
    # The ring is artwork, not a die line: it is inked and it prints.
    ring = [("ring", c, c, c - 0.9, 0.55)]
    # The plate edge, for the manufacturer only.
    die = [("circle", c, c, c, 0.25, True)]
    return fills, rules, ring + die


SEAL_D = 35.0

def seal():
    T = lambda *a, **k: text(*a, box_h=SEAL_D, **k)
    c = SEAL_D / 2.0
    fills, rules = [], []
    # Content sized to leave ~5 mm inside the cut, not the 7.4 mm the first pass
    # left. A kiss-cut wanders by a fraction of a millimetre; 7 mm of white was
    # not caution, it was a seal that looked lost inside its own sticker.
    fills.append((K100, T("MERIDIAN", "cond_bold", 4.0, c, 16.2, ls=0.85, align="center")))
    rules.append((K100, 9.5, 18.6, 25.5, 18.6, 0.32))
    # K100, not a tint: at 1.9 mm a 55% screen breaks into visible dots on
    # uncoated stock and reads as a printing fault rather than as hierarchy.
    # Size carries the hierarchy here instead.
    fills.append((K100, T("ONE OF ONE", "mono", 2.2, c, 22.3, ls=0.30, align="center")))
    return fills, rules, [("circle", c, c, SEAL_D / 2.0, 0.25, True)]


# ---------------------------------------------------------------------------
# emitters
# ---------------------------------------------------------------------------
def P(v): return f"{v:.4f}"

def bezier_circle(cx, cy, r):
    """Four-arc approximation; returns (start, [(c1,c2,end) x4])."""
    k = 0.5522847498 * r
    return (cx + r, cy), [
        ((cx + r, cy + k), (cx + k, cy + r), (cx, cy + r)),
        ((cx - k, cy + r), (cx - r, cy + k), (cx - r, cy)),
        ((cx - r, cy - k), (cx - k, cy - r), (cx, cy - r)),
        ((cx + k, cy - r), (cx + r, cy - k), (cx + r, cy)),
    ]


class Sheet:
    def __init__(self, trim_w, trim_h, bleed, marks, spot_name):
        self.tw, self.th = trim_w, trim_h
        self.bleed = bleed
        self.marks = marks
        self.mark_off, self.mark_len = (3.0, 5.0) if marks else (0.0, 0.0)
        self.margin = bleed + (self.mark_len if marks else 5.0)
        self.mw = trim_w + 2 * self.margin
        self.mh = trim_h + 2 * self.margin
        self.spot = spot_name

    def pt(self, x, y_up):
        return (self.margin + x) * MM, (self.margin + y_up) * MM


def emit_pdf(sh, fills, rules, spots, path, spot_label):
    s = ["q"]
    if sh.marks:
        s.append("0 0 0 1 K 0.25 w")
        for (cx, cy) in ((0, 0), (sh.tw, 0), (0, sh.th), (sh.tw, sh.th)):
            hx = -1 if cx == 0 else 1
            vy = -1 if cy == 0 else 1
            for a, b in (((cx + hx * sh.mark_off, cy), (cx + hx * (sh.mark_off + sh.mark_len), cy)),
                         ((cx, cy + vy * sh.mark_off), (cx, cy + vy * (sh.mark_off + sh.mark_len)))):
                x0, y0 = sh.pt(*a); x1, y1 = sh.pt(*b)
                s.append(f"{P(x0)} {P(y0)} m {P(x1)} {P(y1)} l S")

    for tint, x1, y1, x2, y2, w in rules:
        ax, ay = sh.pt(x1, sh.th - y1); bx, by = sh.pt(x2, sh.th - y2)
        s.append(f"0 0 0 {P(tint)} K {P(w * MM)} w {P(ax)} {P(ay)} m {P(bx)} {P(by)} l S")

    for tint, cmds in fills:
        if not cmds: continue
        s.append(f"0 0 0 {P(tint)} k")
        for c in cmds:
            if c[0] == "h": s.append("h")
            elif c[0] == "m":
                x, y = sh.pt(*c[1]); s.append(f"{P(x)} {P(y)} m")
            elif c[0] == "l":
                x, y = sh.pt(*c[1]); s.append(f"{P(x)} {P(y)} l")
            else:
                s.append(" ".join(P(v) for p in c[1:] for v in sh.pt(*p)) + " c")
        s.append("f")

    def circle_ops(cx, cy_top, r, w, dash, spot):
        o = []
        o.append(("/CS0 CS 1 SCN" if spot else "0 0 0 1 K") + f" {P(w * MM)} w")
        o.append("[2.5 2.5] 0 d" if dash else "[] 0 d")
        st, arcs = bezier_circle(cx, sh.th - cy_top, r)
        x, y = sh.pt(*st); o.append(f"{P(x)} {P(y)} m")
        for a in arcs:
            o.append(" ".join(P(v) for p in a for v in sh.pt(*p)) + " c")
        o.append("S [] 0 d")
        return o

    for sp in spots:
        if sp[0] == "ring":
            _, cx, cy, r, w = sp
            s += circle_ops(cx, cy, r, w, False, False)
        else:
            _, cx, cy, r, w, dash = sp
            s += circle_ops(cx, cy, r, w, dash, True)

    if spot_label:
        lbl = text(spot_label, "mono", 1.4, 1.5, sh.th + (sh.margin - 2.4), ls=0.02, box_h=sh.th)
        s.append("/CS0 CS 1 scn")
        for c in lbl:
            if c[0] == "h": s.append("h")
            elif c[0] == "m":
                x, y = sh.pt(*c[1]); s.append(f"{P(x)} {P(y)} m")
            elif c[0] == "l":
                x, y = sh.pt(*c[1]); s.append(f"{P(x)} {P(y)} l")
            else:
                s.append(" ".join(P(v) for p in c[1:] for v in sh.pt(*p)) + " c")
        s.append("f")
    s.append("Q")

    content = "\n".join(s).encode("latin-1")
    W, H = sh.mw * MM, sh.mh * MM
    bx, by = sh.margin * MM, sh.margin * MM
    res = ("<</ColorSpace<</CS0[/Separation/%s /DeviceCMYK 5 0 R]>>>>" % sh.spot) if sh.spot else "<<>>"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        ("<</Type/Page/Parent 2 0 R/MediaBox[0 0 %s %s]/BleedBox[%s %s %s %s]"
         "/TrimBox[%s %s %s %s]/Contents 4 0 R/Resources%s>>" % (
            P(W), P(H),
            P(bx - sh.bleed * MM), P(by - sh.bleed * MM),
            P(bx + (sh.tw + sh.bleed) * MM), P(by + (sh.th + sh.bleed) * MM),
            P(bx), P(by), P(bx + sh.tw * MM), P(by + sh.th * MM), res)).encode(),
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"\nendstream",
    ]
    if sh.spot:
        # Tint transform: 0 -> paper, 1 -> magenta, so the separation still
        # previews as magenta in any reader that flattens it.
        objs.append(b"<</FunctionType 2/Domain[0 1]/C0[0 0 0 0]/C1[0 1 0 0]/N 1>>")
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs)+1}\n".encode() + b"0000000000 65535 f \n"
    for off in offs:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += (f"trailer\n<</Size {len(objs)+1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n").encode()
    open(path, "wb").write(bytes(buf))
    return len(buf)


def emit_svg(sh, fills, rules, spots, path, spot_label):
    def sv(x, y_up): return (sh.margin + x), (sh.mh - (sh.margin + y_up))
    def grey(t):
        v = round(255 * (1 - t)); return f"#{v:02x}{v:02x}{v:02x}"
    MAG = "#e5007e"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{sh.mw}mm" height="{sh.mh}mm" '
           f'viewBox="0 0 {sh.mw} {sh.mh}">',
           f'<rect x="0" y="0" width="{sh.mw}" height="{sh.mh}" fill="#ffffff"/>']
    if sh.marks:
        for (cx, cy) in ((0, 0), (sh.tw, 0), (0, sh.th), (sh.tw, sh.th)):
            hx = -1 if cx == 0 else 1
            vy = -1 if cy == 0 else 1
            for a, b in (((cx + hx * sh.mark_off, sh.th - cy), (cx + hx * (sh.mark_off + sh.mark_len), sh.th - cy)),
                         ((cx, sh.th - cy - vy * sh.mark_off), (cx, sh.th - cy - vy * (sh.mark_off + sh.mark_len)))):
                ax, ay = sv(*a); bx, by = sv(*b)
                out.append(f'<line x1="{ax:.3f}" y1="{ay:.3f}" x2="{bx:.3f}" y2="{by:.3f}" '
                           f'stroke="#000" stroke-width="0.09"/>')
    for tint, x1, y1, x2, y2, w in rules:
        ax, ay = sv(x1, sh.th - y1); bx, by = sv(x2, sh.th - y2)
        out.append(f'<line x1="{ax:.3f}" y1="{ay:.3f}" x2="{bx:.3f}" y2="{by:.3f}" '
                   f'stroke="{grey(tint)}" stroke-width="{w}"/>')

    def path_d(cmds):
        d = []
        for c in cmds:
            if c[0] == "h": d.append("Z")
            elif c[0] == "m":
                x, y = sv(*c[1]); d.append(f"M{x:.3f} {y:.3f}")
            elif c[0] == "l":
                x, y = sv(*c[1]); d.append(f"L{x:.3f} {y:.3f}")
            else:
                d.append("C" + " ".join(f"{x:.3f} {y:.3f}" for x, y in (sv(*p) for p in c[1:])))
        return " ".join(d)

    for tint, cmds in fills:
        if cmds:
            out.append(f'<path d="{path_d(cmds)}" fill="{grey(tint)}" fill-rule="nonzero"/>')
    for sp in spots:
        if sp[0] == "ring":
            _, cx, cy, r, w = sp
            x, y = sv(cx, sh.th - cy)
            out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{r}" fill="none" stroke="#000" '
                       f'stroke-width="{w}"/>')
        else:
            _, cx, cy, r, w, dash = sp
            x, y = sv(cx, sh.th - cy)
            da = ' stroke-dasharray="0.9 0.9"' if dash else ""
            out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{r}" fill="none" stroke="{MAG}" '
                       f'stroke-width="{w}"{da}/>')
    if spot_label:
        lbl = text(spot_label, "mono", 1.4, 1.5, sh.th + (sh.margin - 2.4), ls=0.02, box_h=sh.th)
        out.append(f'<path d="{path_d(lbl)}" fill="{MAG}" fill-rule="nonzero"/>')
    out.append("</svg>")
    open(path, "w").write("\n".join(out))


ARTEFACTS = [
    # name,       content,     trim w/h,            bleed, marks, separation,  margin label
    ("tag-front", tag_front, (TAG_W, TAG_H),        3.0, True,  "Die",
     "MAGENTA = DIE / PUNCH GUIDE, DO NOT PRINT"),
    ("tag-back",  tag_back,  (TAG_W, TAG_H),        3.0, True,  "Die",
     "MAGENTA = DIE / PUNCH GUIDE, DO NOT PRINT"),
    ("stamp",     stamp,     (STAMP_D, STAMP_D),    0.0, False, "Die",
     "MAGENTA = PLATE EDGE, NOT INKED"),
    ("seal",      seal,      (SEAL_D, SEAL_D),      2.0, True,  "CutContour",
     "MAGENTA = CUTCONTOUR, DO NOT PRINT"),
]

if __name__ == "__main__":
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "print")
    os.makedirs(d, exist_ok=True)
    for name, fn, (tw, th), bleed, marks, spot, label in ARTEFACTS:
        fills, rules, spots = fn()
        sh = Sheet(tw, th, bleed, marks, spot)
        n = emit_pdf(sh, fills, rules, spots, os.path.join(d, f"meridian-{name}.pdf"), label)
        emit_svg(sh, fills, rules, spots, os.path.join(d, f"meridian-{name}.svg"), label)
        tints = sorted({t for t, _ in fills} | {r[0] for r in rules})
        print(f"{name:10s} media {sh.mw:g}x{sh.mh:g}mm  {n:>7,}B  tints {tints}  spots {len(spots)}")
