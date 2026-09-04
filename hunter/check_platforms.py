#!/usr/bin/env python3
"""Verify each shop in registry.json: is it alive, is it Shopify, does it expose
structured product data (JSON-LD)? Run locally (the repo's cloud environment
blocks outbound requests to arbitrary shops).

Usage:  python3 check_platforms.py            # updates registry.json in place
        python3 check_platforms.py --dry-run  # print results only
"""
import json, re, sys, time, urllib.request

REGISTRY = __file__.rsplit("/", 1)[0] + "/registry.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(200_000).decode("utf-8", "replace")

def domain(url):
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/")[0])

def check(shop):
    d = domain(shop["url"])
    result = {"alive": False, "platform": "unknown", "structured_data": False}
    try:  # Shopify: public products.json is definitive
        st, body = fetch(f"https://{d}/products.json?limit=1")
        if st == 200 and body.lstrip().startswith('{"products"'):
            result.update(alive=True, platform="shopify")
            return result
    except Exception:
        pass
    try:  # otherwise fetch homepage, look for platform fingerprints + JSON-LD
        st, body = fetch(shop["url"])
        result["alive"] = st == 200
        low = body.lower()
        if "cdn.shopify.com" in low: result["platform"] = "shopify-locked"  # Shopify but products.json disabled
        elif "woocommerce" in low or "wp-content" in low: result["platform"] = "woocommerce"
        elif "mage" in low and "magento" in low: result["platform"] = "magento"
        else: result["platform"] = "custom"
        result["structured_data"] = 'application/ld+json' in low
    except Exception as e:
        result["error"] = type(e).__name__
    return result

def main():
    dry = "--dry-run" in sys.argv
    shops = json.load(open(REGISTRY, encoding="utf-8"))
    for i, s in enumerate(shops):
        r = check(s)
        s["verified"] = r
        tier = ("1-shopify" if r["platform"] == "shopify"
                else "2-jsonld" if r["structured_data"]
                else "3-custom" if r["alive"] else "dead?")
        print(f"[{i+1}/{len(shops)}] {domain(s['url']):40s} {r['platform']:15s} tier {tier}")
        time.sleep(1)  # be polite
    if not dry:
        json.dump(shops, open(REGISTRY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\nupdated {REGISTRY}")

if __name__ == "__main__":
    main()
