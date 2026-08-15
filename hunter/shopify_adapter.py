#!/usr/bin/env python3
"""Pull the full catalogue of every tier-1 (Shopify) shop in registry.json and
normalize it into one flat item format. Appends a price snapshot per run to
history.jsonl — never overwrites, so price anchors accumulate over time.

Run locally after check_platforms.py has stamped the registry:
    python3 shopify_adapter.py              # all verified shopify shops
    python3 shopify_adapter.py kith.com     # one shop only

Outputs (in hunter/data/):
    items.json     latest normalized snapshot of every item seen this run
    history.jsonl  one line per (item, run): url, price, compare_at, available, ts
"""
import json, os, re, sys, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "registry.json")
DATA = os.path.join(HERE, "data")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
PAGE_LIMIT = 250          # Shopify max per page
PAGE_PAUSE = 1.0          # seconds between requests — stay polite, stay unblocked

def domain(url):
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/")[0])

def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def pull_shop(dom):
    """Yield normalized items from one Shopify shop, paging until empty."""
    page = 1
    while True:
        try:
            data = fetch_json(f"https://{dom}/products.json?limit={PAGE_LIMIT}&page={page}")
        except Exception as e:
            print(f"  ! {dom} page {page}: {type(e).__name__}", file=sys.stderr)
            return
        products = data.get("products", [])
        if not products:
            return
        for p in products:
            for v in p.get("variants", []):
                img = (p.get("images") or [{}])[0].get("src", "")
                yield {
                    "id": f"{dom}/{p['id']}/{v['id']}",
                    "shop": dom,
                    "brand": (p.get("vendor") or "").strip(),
                    "title": p.get("title", ""),
                    "product_type": p.get("product_type", ""),
                    "tags": p.get("tags", []),
                    "size": v.get("title", ""),
                    "price": float(v["price"]) if v.get("price") else None,
                    "compare_at": float(v["compare_at_price"]) if v.get("compare_at_price") else None,
                    "available": bool(v.get("available")),
                    "url": f"https://{dom}/products/{p.get('handle','')}",
                    "img": img,
                }
        page += 1
        time.sleep(PAGE_PAUSE)

def main():
    os.makedirs(DATA, exist_ok=True)
    shops = json.load(open(REGISTRY, encoding="utf-8"))
    only = sys.argv[1] if len(sys.argv) > 1 else None
    targets = [domain(s["url"]) for s in shops
               if (s.get("verified", {}).get("platform") == "shopify"
                   or "shopify" in str(s.get("platform", "")).lower())
               and (not only or domain(s["url"]) == only)]
    if not targets:
        sys.exit("no shopify shops in registry — run check_platforms.py first")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    all_items, snapshots = {}, 0
    with open(os.path.join(DATA, "history.jsonl"), "a", encoding="utf-8") as hist:
        for dom in targets:
            n = 0
            for item in pull_shop(dom):
                all_items[item["id"]] = item
                hist.write(json.dumps({
                    "id": item["id"], "ts": ts, "price": item["price"],
                    "compare_at": item["compare_at"], "available": item["available"],
                }, ensure_ascii=False) + "\n")
                n += 1
            snapshots += n
            print(f"  {dom}: {n} variants")
    json.dump(list(all_items.values()),
              open(os.path.join(DATA, "items.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    print(f"\n{len(targets)} shops, {len(all_items)} items, {snapshots} price points appended @ {ts}")

if __name__ == "__main__":
    main()
