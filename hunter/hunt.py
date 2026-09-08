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
    "google-index": ("google index", "price as Google indexed it — the shop refuses direct reads"),
    "archived": ("archived", "price from the public web archive, weeks old — the shop refuses direct reads"),
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
.st.link{{background:#5b5bd6;color:#fff;border-color:#5b5bd6}}
.st.archived{{background:#6b5b95;color:#fff;border-color:#6b5b95}}
.st.google-index{{background:#3f6ad8;color:#fff;border-color:#3f6ad8}}
details.unread{{border-bottom:1px solid #111}}
details.unread summary{{padding:12px 18px;cursor:pointer;font:600 12px ui-monospace,monospace;
  color:#555;list-style:none}}
details.unread summary::-webkit-details-marker{{display:none}}
details.unread summary::before{{content:"\25b8  "}}
details.unread[open] summary::before{{content:"\25be  "}}
details.unread summary:hover{{background:#e8e5dc}}
.row.sub{{padding-inline-start:36px;background:#f6f4ef}}
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
        d_ship = landed_detail(o)
        no_ship = d_ship.get("ships_to_il") is False
        gone = " gone" if status in ("sold-out", "dead", "unpriced") or no_ship else ""
        if no_ship:
            why = "does not ship to Israel — " + why
        name = o.get("page_name") or o.get("title", "")
        why_match = o.get("match_why", "")
        rows += (f'<a class="row{gone}" href="{o["url"]}" target="_blank" rel="noopener" title="{why}">'
                 f'<span class="price">${landed(o["usd"], o.get("currency"), o.get("url"))}</span>{thumb}'
                 f'<span class="store"><b>{o["store"]}</b> · {o["price"]} {o.get("currency","USD")}'
                 f'<div class="pname">{name}</div>'
                 f'<small>{why_match}</small></span>'
                 f'<span class="st {status}">{label}</span></a>')
    # A direct product link is a real answer and belongs in the list. "This
    # shop refused to be read" is not — 23 rows of HTTP 403 buried 22 genuine
    # offers on the last hunt. Those fold into one line you can open.
    links = [o for o in manual if o.get("from_index")]
    unread = [o for o in manual if not o.get("from_index")]
    for o in links:
        name = o.get("title") or ""
        rows += (f'<a class="row" href="{o["url"]}" target="_blank" rel="noopener">'
                 f'<span class="price">see shop</span><span class="store"><b>{o["store"]}</b>'
                 + (f'<div class="pname">{name[:90]}</div>' if name else "")
                 + f'<small>{o.get("why", "")}</small></span>'
                 f'<span class="st link">direct link</span></a>')
    if unread:
        inner = "".join(
            f'<a class="row sub" href="{o["url"]}" target="_blank" rel="noopener">'
            f'<span class="store"><b>{o["store"]}</b> <small>{o.get("why") or "not readable"}'
            f'</small></span><span class="st manual">search it yourself</span></a>'
            for o in unread)
        rows += (f'<details class="unread"><summary>{len(unread)} shops could not be read '
                 f'&mdash; open one to search it yourself</summary>{inner}</details>')
    ts = datetime.now(timezone.utc).isoformat(timespec="minutes")
    path = os.path.join(items_dir, slug + ".html")
    open(path, "w", encoding="utf-8").write(PAGE.format(
        title=title, sku=identity.get("sku") or "", rows=rows or
        '<div class="row">nothing confirmed — try --deep, or widen the item</div>', ts=ts))
    idx_path = os.path.join(items_dir, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else []
    idx = [e for e in idx if e["slug"] != slug]
    from agent.landed import to_door
    buyable = [o for o in priced if o.get("status") not in ("sold-out", "unpriced")
               and to_door(o["usd"], store_domain(o.get("url")), o.get("currency")).get("ships_to_il") is not False]
    idx.insert(0, {"slug": slug, "title": title, "sku": identity.get("sku"),
                   "hunted": ts, "offers": len(priced), "manual": len(manual),
                   "cheapest_landed": landed(buyable[0]["usd"], buyable[0].get("currency"),
                                             buyable[0].get("url")) if buyable else None})
    json.dump(idx, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_overlay(identity, priced, slug, ts)
    return path


OVERLAY = os.path.join(os.path.dirname(HERE), "shelf", "hunter-overlay.json")


def write_overlay(identity, priced, slug, ts, shelf_item=None):
    """Hand the hunt's answer back to the shelf, without touching items.json.

    items.json is the seam between the backend pipeline and the shelf page, and
    it is written by that pipeline on its own schedule. The hunter never edits
    it. It writes a sidecar keyed by shelf item id, and the shelf page merges
    it: a fresh verified price, where, when, with the verification status —
    next to the snapshot price, never silently replacing it."""
    from agent import catalog
    from agent.landed import to_door
    buyable = [o for o in priced if o.get("status") not in ("sold-out", "unpriced", "dead")
               and to_door(o["usd"], store_domain(o.get("url")), o.get("currency")).get("ships_to_il") is not False]
    if not buyable:
        return
    best = min(buyable, key=lambda o: landed(o["usd"], o.get("currency"), o.get("url")) or 9e9)
    entry = {"hunted": ts, "page": f"hunter/items/{slug}.html",
             "cheapest_landed_usd": landed(best["usd"], best.get("currency"), best.get("url")),
             "price": best.get("price"), "currency": best.get("currency"),
             "store": store_domain(best.get("url")) or best.get("store"),
             "url": best.get("url"), "status": best.get("status"),
             "offers": len(priced), "sku": identity.get("sku")}
    ids = set()
    if shelf_item or identity.get("shelf_item"):
        ids.add(shelf_item or identity.get("shelf_item"))
    code = (identity.get("sku") or "").replace("-", "").lower()
    if code:                                    # every shelf piece with this style code
        for it in catalog.items():
            if (it.get("styleCode") or "").replace("-", "").lower() == code:
                ids.add(it.get("id"))
    if not ids:
        return
    try:
        ov = json.load(open(OVERLAY, encoding="utf-8"))
    except (OSError, ValueError):
        ov = {}
    for i in ids:
        if i:
            ov[i] = entry
    json.dump(ov, open(OVERLAY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


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
