#!/usr/bin/env python3
"""One command, one hunt: identify -> you confirm -> search the stores ->
walk into every candidate's page -> publish.

    python3 hunt.py salomon xt-6 gore-tex
    python3 hunt.py --deep DV1748-101
    python3 hunt.py --yes salomon xt-6          # skip the confirmation prompt

The confirmation step is not a formality. A hunt asks ~120 shops up to three
questions each, and every wrong lead costs a page load. Getting the item right
first is the cheapest thing this program does.

Writes hunter/items/<slug>.html — the item's page: every store that has it,
with the name and price read off the store's own product page, landed cost,
cheapest first — and updates hunter/items/index.json so the UI can list past
hunts. Needs ANTHROPIC_API_KEY only when the shelf catalogue cannot identify
the query by itself.
"""
import json, os, re, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agent.identify import identify, search_terms, save_alias
from agent.search import hunt as run_search
from agent import catalog, inspect

CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

# how a status reads on the item page, and whether it is a thing you can buy
STATUS_LABEL = {
    "verified": ("verified", "the page confirms this name and price"),
    "price-changed": ("price moved", "the page charges something else than the listing did"),
    "price-unconfirmed": ("price?", "only a price scraped from visible text, and it disagrees"),
    "sold-out": ("sold out", "right item, not buyable"),
    "unpriced": ("no price", "right item, the page shows no price"),
    "live": ("live", "structured store feed, seconds old"),
    "manual": ("manual", "open the store search yourself"),
    "dead": ("dead", "the page would not open"),
}


def store_domain(url):
    from urllib.parse import urlparse
    return re.sub(r"^www\.", "", urlparse(url or "").netloc)


def landed(usd, currency=None, url=None):
    """USD sticker -> to-the-door price. See agent/landed.py for why the
    country, not the currency, decides whether anything is imported."""
    from agent.landed import total
    return total(usd, store_domain(url), currency)


def landed_detail(o):
    """The itemisation behind one offer's landed number."""
    from agent.landed import to_door
    return to_door(o.get("usd"), store_domain(o.get("url")), o.get("currency"))


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "item"


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
body{{margin:0;background:#f0eee9;color:#111;font-family:ui-sans-serif,system-ui,sans-serif}}
.wrap{{max-width:940px;margin:0 auto;padding:14px}}
.card{{border:1px solid #111;box-shadow:0 2px 8px rgba(30,25,15,.06)}}
.head{{padding:14px 18px;border-bottom:1px solid #111}}
.head b{{font-size:17px}} .head .sku{{font:600 11px ui-monospace,monospace;color:#555;letter-spacing:.08em}}
.row{{display:flex;gap:14px;align-items:center;padding:12px 18px;border-bottom:1px solid #111;text-decoration:none;color:#111}}
.row:hover{{background:#e8e5dc}} .row.gone{{opacity:.55}}
.price{{font-weight:800;font-size:18px;min-width:96px}} .store{{flex:1;min-width:0}}
.store small{{color:#555}} .pname{{font-size:12px;color:#333}}
.st{{font:600 9px ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;padding:2px 6px;border:1px solid #111;white-space:nowrap}}
.st.verified{{background:#156b3b;color:#fff;border-color:#156b3b}}
.st.dead,.st.price-unconfirmed{{background:#d12a6a;color:#fff;border-color:#d12a6a}}
.st.sold-out,.st.price-changed,.st.unpriced{{background:#b8862b;color:#fff;border-color:#b8862b}}
footer{{padding:10px 18px;font:11px ui-monospace,monospace;color:#888;line-height:1.7}}
</style></head><body><div class="wrap"><div class="card">
<div class="head"><b>{title}</b><br><span class="sku">{sku}</span></div>
{rows}
<footer>hunted {ts} · every price below was read off the store's own product page, not off a search result<br>
landed = price + israeli import steps · <a href="../index.html">hunter home</a></footer>
</div></div></body></html>"""


def publish(identity, offers):
    items_dir = os.path.join(HERE, "items")
    os.makedirs(items_dir, exist_ok=True)
    title = " ".join(filter(None, [identity.get("brand"), identity.get("product")])) \
        or identity.get("query") or "item"
    slug = slugify(identity.get("sku") or title)
    from agent.fx import to_usd
    seen = set()
    priced, manual = [], []
    for o in offers:   # dedupe by product URL across query variants
        if o["url"] in seen:
            continue
        seen.add(o["url"])
        if o.get("price"):
            o["usd"] = o.get("usd") or to_usd(o["price"], o.get("currency"))
            priced.append(o)
        elif o.get("manual"):
            manual.append(o)
    priced.sort(key=lambda o: (o.get("status") in ("sold-out", "unpriced"), o["usd"]))
    rows = ""
    for o in priced:
        thumb = (f'<img src="{o["img"]}" alt="" loading="lazy" '
                 'style="width:56px;height:56px;object-fit:cover;border:1px solid #111">'
                 if o.get("img") else "")
        status = o.get("status", "?")
        label, why = STATUS_LABEL.get(status, (status, ""))
        gone = " gone" if status in ("sold-out", "dead", "unpriced") else ""
        name = o.get("page_name") or o.get("title", "")
        why_match = o.get("match_why", "")
        rows += (f'<a class="row{gone}" href="{o["url"]}" target="_blank" rel="noopener" title="{why}">'
                 f'<span class="price">${landed(o["usd"], o.get("currency"), o.get("url"))}</span>{thumb}'
                 f'<span class="store"><b>{o["store"]}</b> · {o["price"]} {o.get("currency","USD")}'
                 f'<div class="pname">{name}</div>'
                 f'<small>{why_match}</small></span>'
                 f'<span class="st {status}">{label}</span></a>')
    for o in manual:
        rows += (f'<a class="row" href="{o["url"]}" target="_blank" rel="noopener">'
                 f'<span class="price">?</span><span class="store"><b>{o["store"]}</b><br>'
                 f'<small>open the store search in the browser</small></span>'
                 f'<span class="st manual">manual</span></a>')
    ts = datetime.now(timezone.utc).isoformat(timespec="minutes")
    path = os.path.join(items_dir, slug + ".html")
    open(path, "w", encoding="utf-8").write(PAGE.format(
        title=title, sku=identity.get("sku") or "", rows=rows or
        '<div class="row">nothing confirmed — try --deep, or widen the item</div>', ts=ts))
    idx_path = os.path.join(items_dir, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else []
    idx = [e for e in idx if e["slug"] != slug]
    buyable = [o for o in priced if o.get("status") not in ("sold-out", "unpriced")]
    idx.insert(0, {"slug": slug, "title": title, "sku": identity.get("sku"),
                   "hunted": ts, "offers": len(priced), "manual": len(manual),
                   "cheapest_landed": landed(buyable[0]["usd"], buyable[0].get("currency"),
                                             buyable[0].get("url")) if buyable else None})
    json.dump(idx, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path


def confirm(query, assume_yes=False):
    """Show what I think you meant and let you fix it before 120 shops are asked.
    Returns the identity to hunt, or None if you backed out."""
    idy = identify(query)
    cands = catalog.lookup(query, limit=6)
    print(f'\n  what I think you mean  [{idy.get("source")}]')
    print(f'    brand    {idy.get("brand") or "—"}')
    print(f'    product  {idy.get("product") or "—"}')
    print(f'    code     {idy.get("sku") or "— (no style code: matching will be by name only)"}')
    if idy.get("note"):
        print(f'    note     {idy["note"]}')
    print(f'    stores will be asked for: {" | ".join(search_terms(idy))}')
    if cands:
        print(f'\n  or one of these off your shelf ({len(catalog.items())} pieces):')
        for i, c in enumerate(cands, 1):
            print(f'    {i}. {c["brand"]} — {c["name"][:52]}  '
                  f'{c["style_code"] or "no code"}  {c["price"]} @{c["retailer"]}')
    if assume_yes:
        return idy
    ans = input("\n  hunt this? [enter=yes / 1-6=pick from the shelf / n=cancel] ").strip().lower()
    if ans in ("n", "no", "q"):
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(cands):
        idy = {**idy, **catalog.identity_from(cands[int(ans) - 1]), "query": query}
    save_alias(query, idy)
    return idy


def main():
    deep = "--deep" in sys.argv
    assume_yes = "--yes" in sys.argv or "-y" in sys.argv
    query = " ".join(a for a in sys.argv[1:] if not a.startswith("-"))
    if not query:
        sys.exit("usage: python3 hunt.py [--deep] [--yes] <product name or style code>")
    identity = confirm(query, assume_yes)
    if identity is None:
        sys.exit("  cancelled — nothing was searched")

    all_offers, off_target = [], 0

    def on_store(row, store_offers):
        nonlocal off_target
        kept, off = inspect.confirm_store(store_offers, identity)
        off_target += off
        all_offers.extend(kept)
        for o in kept:
            if o.get("price"):
                print(f'    {o["store"]:28s} {o["price"]:>9} {o.get("currency","USD")}  '
                      f'{o.get("status"):18s} {(o.get("page_name") or "")[:44]}')

    print()
    run_search(identity, deep=deep, on_store=on_store)
    page = publish(identity, all_offers)
    priced = [o for o in all_offers if o.get("price")]
    print(f'\n  {len(priced)} confirmed offers · {off_target} leads rejected as the wrong item · '
          f'{sum(1 for o in all_offers if o.get("manual"))} manual links')
    print("  page:", page)


if __name__ == "__main__":
    main()
