#!/usr/bin/env python3
"""The matcher's decision table. Run it after touching agent/match.py:

    python3 hunter/test_match.py

Every row here is a mistake the hunter actually made or would have made. The
expensive failure mode is the false accept: a wrong shoe with a real price and
a real link looks exactly like a deal, and nothing downstream can catch it.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent.inspect import ACCEPT_MIN
from agent.match import score

NIKE = {"brand": "Nike", "product": "Air Max 90 Mercurial",
        "sku": "IR5903-010", "query": "nike air max 90 mercurial"}
SALOMON = {"brand": "Salomon", "product": "XT-6 Gore-Tex",
           "sku": None, "query": "salomon xt-6 gore-tex"}
XT6 = {"brand": "Salomon", "product": "XT-6", "sku": None,
       "category": "Footwear", "query": "salomon xt-6"}
ASICS = {"brand": "ASICS", "product": "Gel-Kayano 14",
         "sku": "1203A740-101", "query": "asics gel kayano 14"}

CASES = [
    # (identity, page title, page brand, page url, should be accepted)
    (NIKE, "Nike Air Max 90 Mercurial BLACK/WHITE", "Nike", "", True),
    (NIKE, "Air Max 90 SE 'Mercurial'", "", "", True),           # brand absent from title
    (NIKE, 'Air Max 90 SE "Mercurial"', "Xhibition", "", True),  # shop name in JSON-LD brand
    (NIKE, "Air Max 90 Essential", "Nike", "", False),           # right code, wrong shoe
    (NIKE, "Air Max 95 SE 'Mercurial'", "Nike", "", False),      # wrong model number
    (NIKE, "Air Max 900 Mercurial", "Nike", "", False),          # 90 must not match 900
    ({"brand": "New Balance", "product": "990", "sku": None, "query": "new balance 990"},
     "Feizy Katya 8990F Area Rug", "", "/shop/product/feizy-katya-8990f-area-rug", False),  # 990 inside 8990f
    (NIKE, "Air Force 1 Mercurial", "Nike", "", False),
    (NIKE, "Adidas Samba", "Adidas", "", False),
    (NIKE, "Some Other Shoe", "", "/products/ir5903-010", True), # style code in the url wins

    (SALOMON, "Salomon XT-6 Gore-Tex Black", "Salomon", "", True),
    (SALOMON, "SALOMON XT 6 GORE-TEX", "Salomon", "", True),     # separators are noise
    (SALOMON, "Salomon XT-4 Gore-Tex", "Salomon", "", False),    # the whole point
    (SALOMON, "Salomon Speedcross 5", "Salomon", "", False),
    (SALOMON, "XT-6 Expanse", "", "", False),                    # no brand, wrong variant
    (SALOMON, "Salomon XT-6 GTX", "Salomon", "", True),          # stores abbreviate gore-tex

    (ASICS, "ASICS GEL-KAYANO 14 Ivory / Cream", "ASICS", "", True),
    (ASICS, "Gel-Kayano 14", "", "", True),
    (ASICS, "ASICS Gel-Kayano 31", "ASICS", "", False),
    (ASICS, "Gel-Kayano 14 sneakers in mesh", "", "", True),
    # the hunter published these on a live XT-6 hunt, 8 Sep 2026
    (XT6, "XT 6 rygsæk", "", "", False),          # the matching backpack, in Danish
    (XT6, "Salomon XT 6 backpack", "Salomon", "", False),
    (XT6, "Consent", "",
     "https://www.footish.se/searchresults?searchstring=salomon%20xt-6", False),
    (XT6, "Salomon XT-6 Expanse Sneakers Black", "Salomon", "", True),
]


def main():
    bad = 0
    for identity, title, brand, url, want in CASES:
        s, why = score(title, identity, url=url, brand=brand)
        got = s >= ACCEPT_MIN
        if got != want:
            bad += 1
            print(f'  FAIL  {title!r} -> {s:.2f} ({"accept" if got else "reject"}), '
                  f'expected {"accept" if want else "reject"}: {why}')
    print(f'{len(CASES) - bad}/{len(CASES)} matcher cases correct (accept bar {ACCEPT_MIN})')
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
