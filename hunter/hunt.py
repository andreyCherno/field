#!/usr/bin/env python3
"""One command, one hunt: identify -> search the stores -> verify -> publish.

    python3 hunt.py salomon xt-6 gore-tex
    python3 hunt.py --deep DV1748-101

Writes hunter/items/<slug>.html — the item's page: every store that has it,
verified link and price, landed cost, cheapest first — and updates
hunter/items/index.json so the hunter UI can list past hunts.
Needs ANTHROPIC_API_KEY for identification and deep parsing; without it,
SKU-shaped queries and structured stores still work.
"""
import json, os, re, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agent.identify import identify
from agent.search import hunt as run_search
from agent.verify import verify_all

CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

def landed(price):
    if price is None:
        return None
    if price <= CFG["tax_free_under_usd"]:
        return round(price, 2)
    if price <= CFG["customs_over_usd"]:
        return round(price * (1 + CFG["vat_rate"]), 2)
    return round(price * (1 + CFG["vat_rate"]) * 1.10, 2)

def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "item"

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
body{{margin:0;background:#f0eee9;color:#111;font-family:ui-sans-serif,system-ui,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:14px}}
.card{{border:1px solid #111;box-shadow:0 2px 8px rgba(30,25,15,.06)}}
.head{{padding:14px 18px;border-bottom:1px solid #111}}
.head b{{font-size:17px}} .head .sku{{font:600 11px ui-monospace,monospace;color:#555;letter-spacing:.08em}}
.row{{display:flex;gap:14px;align-items:center;padding:12px 18px;border-bottom:1px solid #111;text-decoration:none;color:#111}}
.row:hover{{background:#e8e5dc}}
.price{{font-weight:800;font-size:18px;min-width:90px}} .store{{flex:1}}
.st{{font:600 9px ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;padding:2px 6px;border:1px solid #111}}
.st.verified{{background:#156b3b;color:#fff;border-color:#156b3b}}
.st.dead{{background:#d12a6a;color:#fff;border-color:#d12a6a}}
footer{{padding:10px 18px;font:11px ui-monospace,monospace;color:#888}}
</style></head><body><div class="wrap"><div class="card">
<div class="head"><b>{title}</b><br><span class="sku">{sku}</span></div>
{rows}
<footer>hunted {ts} · landed = price + israeli import steps · <a href="../index.html">hunter home</a></footer>
</div></div></body></html>"""

def publish(identity, offers):
    items_dir = os.path.join(HERE, "items")
    os.makedirs(items_dir, exist_ok=True)
    title = " ".join(filter(None, [identity.get("brand"), identity.get("product")])) or identity["query"]
    slug = slugify(identity.get("sku") or title)
    priced = sorted([o for o in offers if o.get("price")], key=lambda o: o["price"])
    manual = [o for o in offers if o.get("manual")]
    rows = ""
    for o in priced:
        thumb = (f'<img src="{o["img"]}" alt="" loading="lazy" '
                 'style="width:56px;height:56px;object-fit:cover;border:1px solid #111">'
                 if o.get("img") else "")
        rows += (f'<a class="row" href="{o["url"]}" target="_blank" rel="noopener">'
                 f'<span class="price">${landed(o["price"])}</span>{thumb}'
                 f'<span class="store">{o["store"]}<br><small>{o.get("title","")} · sticker ${o["price"]}</small></span>'
                 f'<span class="st {o.get("status","")}">{o.get("status","?")}</span></a>')
    for o in manual:
        rows += (f'<a class="row" href="{o["url"]}" target="_blank" rel="noopener">'
                 f'<span class="price">?</span><span class="store">{o["store"]}<br>'
                 f'<small>open the store search in the browser</small></span>'
                 f'<span class="st manual">manual</span></a>')
    ts = datetime.now(timezone.utc).isoformat(timespec="minutes")
    path = os.path.join(items_dir, slug + ".html")
    open(path, "w", encoding="utf-8").write(PAGE.format(
        title=title, sku=identity.get("sku") or "", rows=rows or
        '<div class="row">nothing found — try --deep or add stores</div>', ts=ts))
    idx_path = os.path.join(items_dir, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else []
    idx = [e for e in idx if e["slug"] != slug]
    idx.insert(0, {"slug": slug, "title": title, "sku": identity.get("sku"),
                   "hunted": ts, "offers": len(priced), "manual": len(manual),
                   "cheapest_landed": landed(priced[0]["price"]) if priced else None})
    json.dump(idx, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path

def main():
    deep = "--deep" in sys.argv
    query = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    if not query:
        sys.exit("usage: python3 hunt.py [--deep] <product name or style code>")
    identity = identify(query)
    print(f'identity: {identity.get("brand")} / {identity.get("product")} / sku {identity.get("sku")} [{identity["source"]}]')
    offers = verify_all(run_search(identity, deep=deep))
    page = publish(identity, offers)
    priced = [o for o in offers if o.get("price")]
    print(f'{len(priced)} priced offers, {sum(1 for o in offers if o.get("manual"))} manual store links')
    print("page:", page)

if __name__ == "__main__":
    main()
