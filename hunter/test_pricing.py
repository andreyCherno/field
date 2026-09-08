#!/usr/bin/env python3
"""Every way a price has been wrong this week, pinned. Run after touching
fx.py, landed.py or inspect.py:   python3 hunter/test_pricing.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent.fx import parse_amount
from agent.landed import to_door, country_of
from agent.inspect import _looks_like_listing, norm_size, size_system

fails = []
def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, wanted {want!r}")

# European decimals: 154,34 is 154.34, not 15434
check("eu decimal", parse_amount("154,34"), 154.34)
check("eu thousands", parse_amount("1.299,00"), 1299.0)
check("us thousands", parse_amount("1,299.00"), 1299.0)
check("trailing comma", parse_amount("11200,"), 11200.0)
# a listing page is never a product
check("collection page", _looks_like_listing("https://x.com/collections/fashion/adidas-evo-sl", "adidas"), True)
check("search page", _looks_like_listing("https://x.com/search.html?q=adidas", "Search for adidas"), True)
check("product page", _looks_like_listing("https://x.com/collections/a/products/xt-6", "Salomon XT-6"), False)
# country decides import, never currency; a foreign shop quoting ILS is uncertain, not free of duty
r = to_door(136.62, "xhibition.co", "ILS"); check("foreign ILS uncertain", r["confidence"], "uncertain")
r = to_door(100.0, "kicks.co.il", "ILS");   check("israeli shop high", r["confidence"], "high")
r = to_door(129.71, "sneakersnstuff.com", "EUR")
check("origin VAT removed", any("VAT" in l[0] and l[1] < 0 for l in r["lines"]), True)
check("card fx on EUR", any("Card FX" in l[0] for l in r["lines"]), True)
check("no card fx on USD", any("Card FX" in l[0] for l in to_door(100.0, "kith.com", "USD")["lines"]), False)
# a shop that will not deliver is not a deal
check("END no delivery", to_door(98.0, "endclothing.com", "GBP").get("ships_to_il"), False)
check("kith delivers", to_door(185.0, "kith.com", "USD").get("ships_to_il"), True)
# sizes: alpha vs numeric are different systems
check("size systems", (size_system("M"), size_system("9.5")), ("alpha", "numeric"))
check("size norm", norm_size("US 9,5"), "9.5")

print(f"{22 - len(fails)}/22 pricing guards hold" if not fails else "\n".join("FAIL " + f for f in fails))
sys.exit(1 if fails else 0)
