#!/usr/bin/env python3
"""Imitate the way you hunt for sales, and do it every day.

Two halves. `record` opens the visible Chrome and simply watches where you go:
every page you open on a registered shop is remembered, and the ones that
look like sale/outlet pages are proposed as that shop's `sale_url`s. That is
the imitation — the hunter learns your route through a shop from you walking
it, not from guessing. `sweep` then walks those routes on its own: opens each
sale page (visible browser where the shop needs it), takes every item with a
discount signal, reads the page for the real price and sizes, keeps what is
in your size, from a brand you rate, under your budget, at or past your
minimum discount, and writes data/feed.json — the "Today's catch" the hunter
page already shows. Shopify shops need no page at all: their catalogue says
compare_at_price, so their sale is read straight from the index.

    python3 -m agent.sales record            # browse; Ctrl-C when done
    python3 -m agent.sales sweep             # build today's feed
"""
import json, os, re, sys, time, glob
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
SALE_PATH = re.compile(r"(sale|outlet|clearance|promo|discount|final|reduced|last[-_ ]chance|deals?)", re.I)
DISCOUNT_TXT = re.compile(r"(-\s?\d{1,2}\s?%|\d{1,2}\s?%\s?off|sale|reduced|was\s)", re.I)


def _playbooks():
    for f in sorted(glob.glob(os.path.join(ROOT, "playbooks", "*.json"))):
        if os.path.basename(f).startswith("_"):
            continue
        yield f, json.load(open(f, encoding="utf-8"))


def _domain_of(url):
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url or "").split("/")[0].lower())


def record():
    """Watch you browse in the visible Chrome; propose sale pages per shop."""
    from agent import browser
    browser.MODE = "headed"
    seen, shops = {}, {pb["domain"]: f for f, pb in _playbooks()}

    def job(br):
        ctx = browser._headed_ctx()
        def on_nav(frame):
            try:
                if frame.parent_frame:
                    return
                u = frame.url
                d = _domain_of(u)
                hit = next((s for s in shops if d == s or d.endswith("." + s)), None)
                if hit and u not in seen:
                    seen[u] = hit
                    print(f"   {hit:26s} {u[:90]}", flush=True)
            except Exception:
                pass
        ctx.on("page", lambda p: p.on("framenavigated", on_nav))
        p = ctx.new_page(); p.on("framenavigated", on_nav)
        p.goto("about:blank")
        print("Browse your shops. Every page you open on a registered shop is noted. Ctrl-C to finish.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return dict(seen)
    try:
        visited = browser._submit(job)
    except KeyboardInterrupt:
        visited = dict(seen)
    by_shop = {}
    for u, d in visited.items():
        if SALE_PATH.search(u.split("?")[0]) or SALE_PATH.search(u):
            by_shop.setdefault(d, []).append(u.split("#")[0])
    for d, urls in by_shop.items():
        f = shops[d]; pb = json.load(open(f, encoding="utf-8"))
        cur = pb.get("sale_urls") or []
        pb["sale_urls"] = list(dict.fromkeys(cur + urls))[:6]
        pb["sale_urls_source"] = f"recorded from your own browsing {datetime.now().date().isoformat()}"
        json.dump(pb, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  {d:26s} learned {len(urls)} sale page(s)", flush=True)
    print(f"recorded {len(visited)} pages across {len(set(visited.values()))} shops; "
          f"{len(by_shop)} shops gained sale routes", flush=True)


def _my_sizes():
    from agent.inspect import norm_size
    return {norm_size(s) for s in CFG.get("sizes") or [] if str(s).strip()}


_LEDGER = None
def _tier(brand):
    """config.brand_tiers first (your hand-set overrides), then ledger.json —
    the 196 brands already rated, 74 of them tier A. Unknown brand: None."""
    global _LEDGER
    t = CFG.get("brand_tiers", {}).get(brand or "")
    if t in ("A", "B"):
        return t
    if _LEDGER is None:
        try:
            _LEDGER = {k.lower(): v.get("tier") for k, v in
                       json.load(open(os.path.join(ROOT, "ledger.json"), encoding="utf-8")).get("brands", {}).items()}
        except Exception:
            _LEDGER = {}
    return _LEDGER.get((brand or "").lower())


def _keep(o, landed_usd):
    """Your gates, exactly as config.json states them."""
    tier = _tier(o.get("brand"))
    if tier not in ("A", "B"):
        return False, "brand not rated A/B"
    cap = CFG.get("budget_caps_usd", {}).get("default", 60)
    if landed_usd is None or landed_usd > cap:
        return False, f"over ${cap}"
    disc = o.get("discount") or 0
    if disc < CFG.get("min_discount", 0.3):
        return False, "discount under the bar"
    mine = _my_sizes()
    if mine and o.get("page_sizes"):
        from agent.inspect import norm_size
        if not any(norm_size(s["size"]) in mine for s in o["page_sizes"] if s.get("available") is not False):
            return False, "not in your size"
    return True, tier


def sweep(limit_per_shop=40, only=None):
    from agent import browser, inspect, fx, shopify
    from hunt import landed_detail, store_domain
    found = []
    for f, pb in _playbooks():
        if pb.get("skip"):
            continue
        d = pb["domain"]
        if only and d not in only:
            continue
        # 1. Shopify: the catalogue knows the sale outright
        if shopify.has_index(d):
            with open(shopify._path(d), encoding="utf-8") as fh:
                for line in fh:
                    p = json.loads(line)
                    for v in p.get("variants") or []:
                        pr, was = fx.parse_amount(v.get("price")), fx.parse_amount(v.get("compare_at_price"))
                        if pr and was and was > pr and v.get("available"):
                            found.append({"shop": d, "brand": p.get("vendor") or "", "title": p["title"],
                                          "url": p["url"], "img": p.get("img") or "", "price": pr,
                                          "was": was, "currency": pb.get("currency"),
                                          "size": v.get("title"), "discount": round(1 - pr / was, 3),
                                          "page_sizes": [{"size": v.get("title"), "available": True}
                                                         for v in p["variants"] if v.get("available")]})
                            break          # one row per product: its cheapest on-sale variant
            continue
        # 2. everyone else: the sale routes you recorded (or the obvious /sale)
        urls = pb.get("sale_urls") or []
        if not urls and pb.get("search_url"):
            base = re.sub(r"/[^/]*\?.*$", "", pb["search_url"].split("{q}")[0].rstrip("?=&"))
            urls = [base.rsplit("/", 1)[0] + "/sale"]
        headed = bool(pb.get("needs_headed"))
        for u in urls[:3]:
            try:
                v = browser.visit(u, wait_ms=4000, collect=browser.LINKS_JS, headed=headed)
            except Exception:
                continue
            if not v.get("status") or v["status"] >= 400:
                continue
            n = 0
            for l in v.get("collected") or []:
                if n >= limit_per_shop:
                    break
                if not DISCOUNT_TXT.search(l.get("text") or ""):
                    continue
                if not browser.PRODUCT_PATH.search(l["href"]):
                    continue
                found.append({"shop": d, "url": l["href"].split("?")[0], "title": (l.get("text") or "")[:120],
                              "img": l.get("img") or "", "currency": pb.get("currency"), "needs_headed": headed})
                n += 1
    # 3. verify what came from a page, price it, gate it
    feed, considered, why_not = [], 0, {}
    for o in found:
        considered += 1
        if "price" not in o:   # harvested link: read the page for the truth
            page = inspect.read_page(o["url"], headed=bool(o.get("needs_headed")))
            if not page or page.get("error") or page.get("price") is None:
                continue
            o.update(price=page["price"], currency=page.get("currency") or o.get("currency"),
                     title=page.get("name") or o["title"], page_sizes=page.get("page_sizes") or [],
                     brand=page.get("brand") or o.get("brand", ""), img=page.get("img") or o["img"])
            m = re.search(r"-\s?(\d{1,2})\s?%", o["title"] or "")
            o["discount"] = int(m.group(1)) / 100 if m else 0
        o["usd"] = fx.to_usd(o["price"], o.get("currency"))
        d = landed_detail(o)
        ok, why = _keep(o, d["total"])
        if not ok:
            why_not[why] = why_not.get(why, 0) + 1
            continue
        feed.append({"brand": o.get("brand", ""), "tier": why, "title": o["title"], "size": o.get("size") or "",
                     "url": o["url"], "img": o.get("img", ""), "landed": d["total"],
                     "anchor": round(fx.to_usd(o.get("was") or o["price"] / max(1 - o["discount"], 0.01),
                                               o.get("currency")), 2),
                     "discount": o["discount"], "shop": o["shop"], "dropped_now": False,
                     "score": round(o["discount"] * (1.0 if why == "A" else 0.6), 3)})
    feed.sort(key=lambda x: -x["score"])
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "candidates": considered, "feed": feed[:CFG.get("top_n", 10)]}
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "data", "feed.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"considered {considered} sale items, kept {len(feed)}, published top {len(out['feed'])}")
    for k, n in sorted(why_not.items(), key=lambda kv: -kv[1]):
        print(f"   rejected {n:>6,}  {k}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    if cmd == "record":
        record()
    else:
        only = set(a for a in sys.argv[2:] if not a.startswith("-")) or None
        sweep(only=only)
