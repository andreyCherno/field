#!/usr/bin/env python3
"""The hunter's brain: turn the collected data into a short ranked deal feed.

    deal score = discount depth x brand tier x (binary personal gates)

Reads  data/items.json    (latest snapshot, from shopify_adapter.py)
       data/history.jsonl (append-only price points, same source)
       config.json        (sizes, budgets, brand tiers, tax rules)
Writes data/feed.json and prints the top N.

Anchor logic, in order of trust:
  1. median of this item's own observed prices (needs a few runs of history)
  2. shop-declared compare_at, discounted by compare_at_trust and rejected
     when it is implausibly inflated (compare_at_max_ratio)
An item with no anchor at all cannot be scored — no anchor, no deal.
"""
import json, os, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

TIER_WEIGHT = {"A": 1.0, "B": 0.6}

def load_history():
    """id -> list of observed prices (oldest first) and the previous run's price."""
    prices = defaultdict(list)
    path = os.path.join(DATA, "history.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if row.get("price") is not None:
                    prices[row["id"]].append((row["ts"], row["price"]))
    return prices

def landed_usd(price, shop_country):
    """Sticker price -> to-the-door price under Israeli personal-import rules.
    Local shops: sticker already includes VAT. Shipping cost per retailer is a
    later refinement — for now tax steps are the dominant effect."""
    if shop_country == "IL":
        return price
    if price <= CFG["tax_free_under_usd"]:
        return price
    if price <= CFG["customs_over_usd"]:
        return price * (1 + CFG["vat_rate"])
    return price * (1 + CFG["vat_rate"]) * 1.10  # rough customs surcharge; refine per category

def anchor_for(item, history):
    own = [p for _, p in history.get(item["id"], [])]
    candidates = []
    if len(own) >= 3:
        candidates.append(statistics.median(own))
    ca, price = item.get("compare_at"), item.get("price")
    if ca and price and ca > price and ca / price <= CFG["compare_at_max_ratio"]:
        candidates.append(ca * CFG["compare_at_trust"])
    return max(candidates) if candidates else None

def budget_cap(item):
    t = (item.get("product_type") or "").lower() + " " + item.get("title", "").lower()
    for key, cap in CFG["budget_caps_usd"].items():
        if key != "default" and key in t:
            return cap
    return CFG["budget_caps_usd"]["default"]

def size_ok(item):
    size = (item.get("size") or "").strip().lower()
    if not size or size == "default title":   # one-size / non-sized items pass
        return True
    wanted = [s.lower() for s in CFG["sizes"]]
    return any(size == w or size.startswith(w + " ") or f"/ {w}" in size for w in wanted)

def price_dropped(item, history):
    pts = history.get(item["id"], [])
    return len(pts) >= 2 and pts[-1][1] < pts[-2][1]

def main():
    items = json.load(open(os.path.join(DATA, "items.json"), encoding="utf-8"))
    history = load_history()
    registry = {s["url"].split("//")[1].split("/")[0].removeprefix("www."): s
                for s in json.load(open(os.path.join(HERE, "registry.json"), encoding="utf-8"))}
    tiers = {k.lower(): v for k, v in CFG["brand_tiers"].items() if not k.startswith("_")}
    ledger_path = os.path.join(HERE, "ledger.json")
    if os.path.exists(ledger_path):   # ledger is the main tier source; config overrides win
        ledger = json.load(open(ledger_path, encoding="utf-8"))["brands"]
        tiers = {**{k.lower(): v["tier"] for k, v in ledger.items()}, **tiers}

    feed = []
    for it in items:
        tier = tiers.get((it.get("brand") or "").lower())
        if tier not in TIER_WEIGHT:      # gate: known quality brand only
            continue
        if not it.get("available") or not size_ok(it):
            continue
        anchor = anchor_for(it, history)
        if not anchor or not it.get("price"):
            continue
        shop_country = registry.get(it["shop"], {}).get("country", "?")
        landed = landed_usd(it["price"], "IL" if shop_country in ("IL", "Israel") else shop_country)
        if landed > budget_cap(it):      # gate: budget cap per category
            continue
        discount = 1 - landed / landed_usd(anchor, "IL" if shop_country in ("IL", "Israel") else shop_country)
        if discount < CFG["min_discount"]:   # gate: minimum real discount
            continue
        score = discount * TIER_WEIGHT[tier] * (1.25 if price_dropped(it, history) else 1.0)
        feed.append({**it, "tier": tier, "anchor": round(anchor, 2),
                     "landed": round(landed, 2), "discount": round(discount, 3),
                     "dropped_now": price_dropped(it, history), "score": round(score, 3)})

    feed.sort(key=lambda x: -x["score"])
    top = feed[:CFG["top_n"]]
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "candidates": len(feed), "feed": top}
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, "feed.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    if not top:
        print("no deals passed the gates (brand tiers empty? history too young?)")
        return
    for d in top:
        drop = " ↓just dropped" if d["dropped_now"] else ""
        print(f"[{d['score']:.2f}] {d['brand']} — {d['title']} ({d['size']})  "
              f"${d['price']} landed ${d['landed']} vs anchor ${d['anchor']} "
              f"= {d['discount']:.0%} off  @{d['shop']}{drop}")

if __name__ == "__main__":
    main()
