#!/usr/bin/env python3
"""Read a Shopify shop's WHOLE catalogue, not its ten-result suggestion box.

Every Shopify shop publishes /products.json: title, vendor, every variant
with its size, price and availability. That is proposal 02 of the plan:
suggest.json caps at ten hits and carries no variants, so sizes came back
empty for six of thirteen producing shops. The catalogue is fetched once a
day per shop into data/index/<domain>.shopify.jsonl and searched locally.

    python3 -m agent.shopify build kith.com
    python3 -m agent.shopify find  kith.com salomon xt-6
"""
import json, os, sys, time, urllib.request, ssl
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "data", "index")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126"}
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _path(domain):
    return os.path.join(INDEX, domain + ".shopify.jsonl")


def build(domain, host=None, max_pages=60, verbose=True):
    host = host or domain
    rows, page = [], 1
    while page <= max_pages:
        u = f"https://{host}/products.json?limit=250&page={page}"
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30, context=_ctx)
            prods = json.load(r).get("products") or []
        except Exception as e:
            if verbose:
                print(f"    page {page}: {type(e).__name__} — stopping", flush=True)
            break
        if not prods:
            break
        for p in prods:
            # compare_at_price is the sale signal — drop it and no Shopify shop can
            # ever show a sale; the first sweep of a 15,000-product shop found 0
            vs = [{"id": v.get("id"), "title": v.get("title"), "price": v.get("price"),
                   "compare_at_price": v.get("compare_at_price"),
                   "available": v.get("available"), "sku": v.get("sku"),
                   "opts": [x for x in (v.get("option1"), v.get("option2"), v.get("option3")) if x]}
                  for v in p.get("variants") or []]
            rows.append({"handle": p.get("handle"), "title": p.get("title"), "vendor": p.get("vendor"),
                         "type": p.get("product_type"), "url": f"https://{host}/products/{p.get('handle')}",
                         "img": ((p.get("images") or [{}])[0]).get("src", ""), "variants": vs,
                         "updated": p.get("updated_at")})
        if verbose:
            print(f"    page {page}: +{len(prods)}  total {len(rows)}", flush=True)
        page += 1
        time.sleep(0.6)
    os.makedirs(INDEX, exist_ok=True)
    with open(_path(domain), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {"domain": domain, "products": len(rows), "built": date.today().isoformat()}
    json.dump(meta, open(_path(domain).replace(".jsonl", ".json"), "w"), indent=1)
    return meta


def has_index(domain):
    return os.path.exists(_path(domain))


def age_days(domain):
    try:
        b = json.load(open(_path(domain).replace(".jsonl", ".json")))["built"]
        return (date.today() - date.fromisoformat(b)).days
    except Exception:
        return None


def find(domain, identity, limit=6, currency=None):
    """Leads with price, sizes and stock straight from the catalogue — the
    page visit still verifies, but the sizes are already known."""
    from agent import match, fx
    out = []
    with open(_path(domain), encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            sc, why = match.score(p["title"], identity, url=p["url"], brand=p.get("vendor") or "",
                                  extra=" ".join(v.get("sku") or "" for v in p["variants"]))
            if sc < 0.6:
                continue
            live = [v for v in p["variants"] if v.get("available")]
            prices = [fx.parse_amount(v.get("price")) for v in (live or p["variants"])]
            prices = [x for x in prices if x is not None]
            out.append({"store": domain, "url": p["url"], "title": p["title"], "brand": p.get("vendor") or "",
                        "img": p.get("img") or "", "price": min(prices) if prices else None,
                        "currency": currency, "match": sc, "match_why": why,
                        "page_sizes": [{"size": v.get("title"), "available": bool(v.get("available"))}
                                       for v in p["variants"]],
                        "from_catalog": True})
    out.sort(key=lambda o: -o["match"])
    return out[:limit]


if __name__ == "__main__":
    cmd, d = sys.argv[1], sys.argv[2]
    if cmd == "build":
        print(json.dumps(build(d)))
    else:
        from agent.identify import identify
        for o in find(d, identify(" ".join(sys.argv[3:]))):
            sz = [s["size"] for s in o["page_sizes"] if s["available"]]
            print(f'  {o["match"]:.2f} {str(o["price"]):>8}  sizes={sz[:8]}  {o["title"][:50]}')
