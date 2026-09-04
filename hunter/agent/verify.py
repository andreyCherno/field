#!/usr/bin/env python3
"""Step 3 of a hunt: before offers are published, confirm each link is alive
and the price on the live page matches what we recorded. Verified offers get a
timestamp; broken ones are flagged, never silently dropped.
"""
import json, re, urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

PRICE_RE = re.compile(r'"price"\s*:\s*"?(\d+[.,]?\d*)"?')   # JSON-LD price on most product pages

def verify_offer(offer):
    out = dict(offer)
    out["checked"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    if offer.get("manual"):
        out["status"] = "manual"
        return out
    try:
        req = urllib.request.Request(offer["url"], headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            status, body = r.status, r.read(300_000).decode("utf-8", "replace")
    except Exception as e:
        out["status"] = "dead"
        out["error"] = type(e).__name__
        return out
    if status != 200:
        out["status"] = "dead"
        return out
    prices = [float(m.replace(",", ".")) for m in PRICE_RE.findall(body)[:5]]
    if offer.get("price") and prices:
        # live page still shows (approximately) the recorded price?
        if any(abs(p - offer["price"]) / max(offer["price"], 1) < 0.02 for p in prices):
            out["status"] = "verified"
        else:
            out["status"] = "price-changed"
            out["live_prices_seen"] = prices
    else:
        out["status"] = "alive-unpriced"
    return out

def verify_all(offers):
    return [verify_offer(o) for o in offers]

if __name__ == "__main__":
    import sys
    offers = json.load(open(sys.argv[1], encoding="utf-8"))
    print(json.dumps(verify_all(offers), ensure_ascii=False, indent=1))
