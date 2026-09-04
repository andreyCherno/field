#!/usr/bin/env python3
"""Targeted hunt: search one particular product across every shop in the
registry and rank the hits by landed (to-the-door) price.

    python3 find.py salomon xt-6
    python3 find.py --all dynafish xiaonian     # include non-Shopify shops via their search page URL

How it searches, per shop:
  - Shopify shops (verified or guessed): the public predictive-search endpoint
    /search/suggest.json — structured results, price included, no scraping.
  - Everything else: we cannot parse arbitrary search pages reliably, so with
    --all the script prints each shop's search URL for the query, ready to
    open in the browser (middle-click down the list).

Landed price = sticker + Israeli import steps (free under $75, VAT to $500).
Currency caveat: Shopify returns shop-currency amounts; until the currency
layer lands, cross-currency comparisons are approximate — the shop's currency
is printed so you can judge.
"""
import json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

def domain(url):
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/")[0])

def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)

def landed(price, is_il):
    if is_il or price <= CFG["tax_free_under_usd"]:
        return price
    if price <= CFG["customs_over_usd"]:
        return price * (1 + CFG["vat_rate"])
    return price * (1 + CFG["vat_rate"]) * 1.10

def search_shopify(dom, query):
    q = urllib.parse.quote(query)
    url = (f"https://{dom}/search/suggest.json?q={q}"
           "&resources[type]=product&resources[limit]=10"
           "&resources[options][unavailable_products]=hide")
    try:
        data = fetch_json(url)
    except Exception:
        return []
    hits = []
    for p in data.get("resources", {}).get("results", {}).get("products", []):
        price = p.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        hits.append({
            "shop": dom,
            "title": p.get("title", ""),
            "brand": p.get("vendor", ""),
            "price": price,
            "url": f"https://{dom}{p.get('url','')}".split("?")[0],
            "img": p.get("image", "") or "",
        })
    return hits

SEARCH_PATHS = {  # best-effort search URLs for non-Shopify shops, for --all
    "default": "/search?q={q}",
    "woocommerce": "/?s={q}&post_type=product",
    "magento": "/catalogsearch/result/?q={q}",
}

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    include_manual = "--all" in sys.argv
    if not args:
        sys.exit("usage: python3 find.py [--all] <product name>")
    query = " ".join(args)
    shops = json.load(open(os.path.join(HERE, "registry.json"), encoding="utf-8"))

    words = [w for w in query.lower().split() if len(w) > 1]
    results, manual = [], []
    for s in shops:
        dom = domain(s["url"])
        is_il = s.get("country") in ("IL", "Israel")
        platform = str(s.get("verified", {}).get("platform") or s.get("platform", "")).lower()
        if "shopify" in platform:
            for h in search_shopify(dom, query):
                text = (h["title"] + " " + h["brand"]).lower()
                if all(w in text for w in words):   # every query word must appear
                    h["country"] = s.get("country", "?")
                    h["landed"] = round(landed(h["price"], is_il), 2)
                    results.append(h)
            time.sleep(0.4)
        elif include_manual:
            path = SEARCH_PATHS.get(platform.split()[0] if platform else "", SEARCH_PATHS["default"])
            manual.append(f"https://{dom}" + path.format(q=urllib.parse.quote(query)))

    results.sort(key=lambda h: h["landed"])
    if results:
        print(f'\n"{query}" — {len(results)} hits, cheapest to the door first:\n')
        for h in results[:20]:
            print(f'  ${h["landed"]:>8.2f} landed  (${h["price"]:.2f} sticker, {h["country"]})'
                  f'  {h["brand"]} — {h["title"]}\n             {h["url"]}')
    else:
        print(f'\nno structured hits for "{query}" in the Shopify shops of the registry')
    if manual:
        print(f"\n{len(manual)} non-Shopify shops — search them in the browser:")
        for u in manual:
            print("  " + u)
    json.dump({"query": query, "results": results},
              open(os.path.join(HERE, "find-results.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
