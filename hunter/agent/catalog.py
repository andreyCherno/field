#!/usr/bin/env python3
"""The hunter already owns a catalogue — ask it before asking anyone else.

`shelf/items.json` holds 23,466 real pieces from 46 retailers, priced and
re-checked on a schedule. 1,641 of them carry a manufacturer `styleCode` that
somebody actually read off a product page, and `facets.style_family` already
stores it squashed ("IH8647-010" -> "ih8647") — the same rule agent/match.py
applies to titles, arrived at twice independently.

That makes the shelf the right first answer to "what is this item?":
grounded, free, offline, and — unlike a language model — incapable of
inventing a style code. An invented code poisons every store query and then
scores a wrong page 1.0, which is the worst failure this system has.

Items that carry `sellers` / `alt_offers` have already been resolved across
retailers, so for those the hunt is partly answered before it starts.

    python3 -m agent.catalog salomon xt-6
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHELF = os.path.join(os.path.dirname(os.path.dirname(HERE)), "shelf", "items.json")

_cache = None


def squash(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def words(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()


def items():
    """The shelf catalogue, loaded once per process (0.3s, ~23k rows).
    Returns [] when the shelf is absent — the hunter simply falls back to the
    model, it never breaks."""
    global _cache
    if _cache is None:
        try:
            _cache = json.load(open(SHELF, encoding="utf-8"))
        except (OSError, ValueError):
            _cache = []
    return _cache


def available():
    return bool(items())


def _row(it):
    """A catalogue row reduced to what a confirmation card needs to show."""
    return {
        "id": it.get("id"),
        "brand": it.get("brand"),
        "name": it.get("name"),
        "style_code": it.get("styleCode"),
        "price": it.get("price"),
        "currency": it.get("currency"),
        "img": it.get("img"),
        "url": it.get("url"),
        "retailer": it.get("retailer"),
        "cat": it.get("cat"),
        "sizes": it.get("sizes") or [],
        "sold_out": it.get("soldOut"),
        "price_age": it.get("priceAge"),
        "sellers": len(it.get("sellers") or []) or None,
        "material": it.get("material"),
    }


def lookup(query, limit=6, min_score=0.5):
    """Grounded candidates for a free-text query, best first.

    Same discipline as match.py: a token carrying a digit is a model code and
    must be present — "xt-6" never returns an XT-4. Descriptive words only
    move the score inside a band."""
    q_words = [w for w in words(query) if len(w) > 1]
    if not q_words:
        return []
    q_sq = squash(query)
    models = [squash(t) for t in query.split()
              if squash(t) and any(c.isdigit() for c in squash(t))]

    scored = []
    for it in items():
        hay_sq = squash(f'{it.get("brand","")} {it.get("name","")} {it.get("styleCode") or ""}')
        fam = (it.get("facets") or {}).get("style_family") or []
        bag = set(words(f'{it.get("brand","")} {it.get("name","")}')) | {squash(f) for f in fam}
        # a model code the query names must be on the item, or it is a different shoe
        if any(not (m in bag or (not (m.isdigit() and len(m) < 3) and m in hay_sq))
               for m in models):
            continue
        hit = sum(1 for w in q_words if w in bag or (len(w) > 2 and w in hay_sq))
        s = hit / len(q_words)
        if s < min_score:
            continue
        # a real style code and a live price are what make a row worth trusting
        s += 0.05 * bool(it.get("styleCode")) + 0.03 * bool(it.get("sellers"))
        if q_sq and q_sq in hay_sq:
            s += 0.1
        scored.append((s, it))

    scored.sort(key=lambda p: -p[0])
    out, seen = [], set()
    for s, it in scored:
        key = squash(it.get("styleCode") or "") or squash(it.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(_row(it), score=round(min(s, 1.0), 2)))
        if len(out) >= limit:
            break
    return out


_DASH = re.compile(r"\s+[-\u2013\u2014]\s+")
# "Putty / Collegiate Navy" is a colourway and so is "Ivory / Cream", but a
# pattern of capitalised words alone would eat "Adizero EVO SL EXO" too. The
# slash is the reliable marker: the colourway starts at the word before it.
_SLASH_COLOR = re.compile(r"\s(\S+\s*/\s*.+)$")
# two or more trailing SHOUTED words are a colourway ("QS LT BASE GREY");
# one alone is usually part of the model ("XT 6 GTX"), so it stays
_CAPS_COLOR = re.compile(r"\s((?:\b[A-Z]{3,}\b\s+)+\b[A-Z]{3,}\b)\s*$")


def split_name(name, brand, code):
    """A retailer's title -> (model, colourway).

    This split is load-bearing, not cosmetic. Matching demands that every
    descriptive word of the model appear on the page, so leaving "BLACK/WHITE
    IR5903-010" inside the model name makes the hunter reject the same shoe at
    every shop that words its colourway differently. The colourway is real
    information — it just belongs in its own field."""
    n = name or ""
    if code:
        n = re.sub(re.escape(code), " ", n, flags=re.I)
    if brand:
        n = re.sub(rf"^{re.escape(brand)}\s+", "", n, flags=re.I)
    colours = [g for g in re.findall(r"\(([^)]*)\)", n) if g.strip()]
    n = re.sub(r"\([^)]*\)", " ", n)              # (TD) (PS) (GS) (Carrier Grey/Black)
    parts = _DASH.split(n)
    if len(parts) > 1:
        n, tail = parts[0], parts[-1]
        colours.append(tail)
    for rx in (_SLASH_COLOR, _CAPS_COLOR):
        m = rx.search(n)
        if m:
            colours.insert(0, m.group(1))
            n = n[:m.start()]
    model = re.sub(r"\s+", " ", n).strip(" -/,\"'")
    colourway = re.sub(r"\s+", " ", " ".join(colours)).strip(" -/,") or None
    return model or (name or "").strip(), colourway


def identity_from(row):
    """A catalogue row -> the identity a hunt runs on. `brand` is trusted as-is;
    the model name is the item name with the brand, the style code and the
    colourway taken out, because stores word all three differently and every
    extra word is one more way to reject the right shoe."""
    brand = (row.get("brand") or "").strip()
    name = (row.get("name") or "").strip()
    product, colourway = split_name(name, brand, row.get("style_code"))
    aliases = [name] if name and name.lower() != f"{brand} {product}".lower().strip() else []
    return {
        "brand": brand or None,
        "product": product or None,
        "sku": row.get("style_code"),
        "aliases": aliases,
        "category": row.get("cat"),
        "colorway": colourway,
        "year": None,
        "retail_usd": None,
        "note": (f'From your shelf: seen at {row.get("retailer")} for '
                 f'{row.get("price")} · {row.get("price_age") or "price age unknown"}'),
        "img": row.get("img"),
        "source": "shelf",
    }


def seed_offers(identity):
    """Leads the shelf already knows, in the hunter's own offer shape.

    1,334 shelf items carry a `sellers` list — the cross-retailer resolution
    that the MoneyMachine matcher already did, with url, price, sizes and
    stock per seller. There is no reason to re-discover them by asking 120
    shops. They still go through the page visit like every other lead: the
    shelf says where to look, the product page still has to say the price."""
    code = squash(identity.get("sku"))
    if not code:
        return []
    out, seen = [], set()
    for it in items():
        fam = {squash(f) for f in (it.get("facets") or {}).get("style_family") or []}
        if squash(it.get("styleCode")) != code and code not in fam:
            continue
        for sel in (it.get("sellers") or [{"retailer": it.get("retailer"),
                                           "url": it.get("url"),
                                           "price": None,
                                           "currency": it.get("currency"),
                                           "sizes": it.get("sizes"),
                                           "soldOut": it.get("soldOut")}]):
            url = sel.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"store": sel.get("retailer") or "shelf", "url": url,
                        "title": it.get("name"), "brand": it.get("brand"),
                        "price": sel.get("price"), "currency": sel.get("currency"),
                        "img": it.get("img"), "sizes": sel.get("sizes") or [],
                        "from_shelf": True})
    return out


if __name__ == "__main__":
    q = " ".join(sys.argv[1:])
    hits = lookup(q)
    print(json.dumps({"candidates": hits,
                      "seed_offers": seed_offers(identity_from(hits[0])) if hits else []},
                     ensure_ascii=False, indent=1))
