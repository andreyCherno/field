#!/usr/bin/env python3
"""Step 2 of a hunt: run the store playbooks and collect offers.

    python3 -m agent.search "salomon xt-6"            # level 1-2
    python3 -m agent.search --deep "salomon xt-6"     # level 3: LLM-parse hard stores too

Order and relevance come from agent/stores.py: shops that have actually
produced hits are asked first, and shops whose whole trade is a different
world than the item (a chandelier shop asked for sneakers) are not asked at
all. Both facts were already in the repo and unused.

Levels:
  1  stores whose playbook already proved productive (data/attempts.jsonl)
  2  every store with a structured method (shopify-suggest / search-url)
  3  --deep: also llm-parse stores — fetch the search page and let the parse
     model extract offers (costs cents; capped by the daily LLM budget)

Every attempt is logged to data/attempts.jsonl — the raw material learn.py
uses to rewrite playbooks. Offers go to verify.py before publication.
"""
import glob, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(400_000).decode("utf-8", "replace")

def log_attempt(row):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "attempts.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def playbooks():
    for path in sorted(glob.glob(os.path.join(ROOT, "playbooks", "*.json"))):
        if os.path.basename(path).startswith("_"):
            continue
        pb = json.load(open(path, encoding="utf-8"))
        pb["_path"] = path          # so a lesson can be written back to it
        yield path, pb


def remember_search_url(pb, landed_url, q):
    """Write a search url we *observed* back into the playbook, replacing the
    one that was guessed. Playbooks are files in git, so every lesson this
    learns arrives as a reviewable diff rather than as hidden state."""
    path = pb.get("_path")
    if not path or not landed_url:
        return
    tpl = landed_url
    for form in (urllib.parse.quote(q), urllib.parse.quote_plus(q), q):
        tpl = tpl.replace(form, "{q}")
    if "{q}" not in tpl or tpl == pb.get("search_url"):
        return
    try:
        saved = json.load(open(path, encoding="utf-8"))
        saved["search_url"] = tpl
        note = "observed: typed into the shop's own search box"
        was = str(saved.get("search_url_source") or "")
        # Curated notes carry knowledge nothing can rediscover — that a shop
        # encodes Hebrew as cp1255, or why it was given up on. Keep them.
        if was and not was.startswith(("observed", "typed", "differentially")):
            note += f" | previously: {was}"
        saved["search_url_source"] = note
        saved.pop("skip", None)          # it works now
        json.dump(saved, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        pb["search_url"] = tpl
    except OSError:
        pass

def queries_for(identity, pb):
    """The phrases this store gets asked — the same list the confirmation card
    showed you, minus the style code at shops whose search chokes on one."""
    from agent.identify import search_terms
    qs = search_terms(identity)
    if identity.get("sku") and not pb.get("accepts_sku", True):
        qs = [q for q in qs if q != identity["sku"]] or [identity["query"]]
    return qs[:3]

def run_shopify_suggest(pb, q):
    # `host` is the hostname that actually answers (some shops only serve the
    # www twin); `domain` stays the identity used for display and skip lists
    url = (f'https://{pb.get("host") or pb["domain"]}/search/suggest.json'
           f'?q={urllib.parse.quote(q)}'
           "&resources[type]=product&resources[limit]=10")
    data = json.loads(fetch(url))
    out = []
    for p in data.get("resources", {}).get("results", {}).get("products", []):
        try:
            price = float(p.get("price"))
        except (TypeError, ValueError):
            continue
        out.append({"store": pb["domain"], "title": p.get("title", ""),
                    "brand": p.get("vendor", ""), "price": price,
                    "img": p.get("image", "") or p.get("featured_image", "") or "",
                    "url": f'https://{pb.get("host") or pb["domain"]}'
                           f'{p.get("url","")}'.split("?")[0]})
    return out

def from_index(pb, identity):
    """Look the item up in this shop's own published product index.

    A sitemap is a list of product urls the shop publishes for crawlers, so we
    look the item up rather than asking. Leads carry no price; the page visit
    reads that, exactly as for a search result.

    Unless the shop also refuses its product pages. Three shops here publish a
    product index and then answer 403 to any automated request for the pages
    in it, so no price is obtainable without defeating that — which is not
    something this builds. What the index still buys is worth keeping: an
    *exact product link* instead of "go and search this shop yourself"."""
    from agent import sitemap
    if not sitemap.has_index(pb["domain"]):
        return []
    leads = sitemap.find(pb["domain"], identity)
    if pb.get("pages_blocked"):
        for l in leads:
            l["manual"] = True
            l["why"] = ("found in the shop's own product index — its pages refuse "
                        "automated requests, so open it yourself for the price")
    return leads


_INDEX_CACHE = {}
READ = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8")).get("read", {})


def from_catalog(pb, identity):
    """A Shopify shop's whole catalogue, read locally: price, sizes, stock —
    no page load, no ten-result cap. Off when config.read.shopify_catalog is."""
    if not READ.get("shopify_catalog", True):
        return None
    from agent import shopify
    d = pb["domain"]
    if not shopify.has_index(d):
        return None
    age = shopify.age_days(d)
    if age is None or age > 3:
        return None
    return shopify.find(d, identity, currency=pb.get("currency")) or None


def index_first(pb, identity, max_age_days=14):
    """Leads from a fresh local index, or None to mean 'ask the shop'."""
    from agent import sitemap
    d = pb["domain"]
    if d not in _INDEX_CACHE:
        age = sitemap.age_days(d)
        _INDEX_CACHE[d] = sitemap.has_index(d) and age is not None and age <= max_age_days
    if not _INDEX_CACHE[d]:
        return None
    leads = from_index(pb, identity)
    return leads or None


def url_variants(pb, q):
    """The playbook's search url, plus its www / no-www twin.

    80 of the 100 search-url playbooks were seeded with a guessed
    https://{domain}/search?q={q} template and no www, and a large share of
    those 404 on the missing prefix alone — which is most of why nearly every
    store used to come back as a manual link."""
    base = pb["search_url"].format(q=urllib.parse.quote(q))
    twin = (base.replace("://www.", "://", 1) if "://www." in base
            else base.replace("://", "://www.", 1))
    return [base, twin] if twin != base else [base]


def browser_search(pb, identity, q, deep):
    """Open this store's results page for real. Returns (leads, error).
    error is None when the page was genuinely read — even if nothing on it
    matched, which is a real answer and must not be reported as 'manual'."""
    from agent import browser
    err = None
    for u in url_variants(pb, q):
        try:
            offers, v = browser.search(pb, u, identity, deep=deep, headed=bool(pb.get("needs_headed")))
        except Exception as e:
            err = type(e).__name__
            continue
        if offers:
            return offers, None
        if v.get("status") and v["status"] < 400:
            return [], None          # page read fine, this shop has nothing
        err = f'HTTP {v.get("status")}'

    # The url template was a guess and it is wrong, or the shop refuses direct
    # search links. Walk in the front door and use its own search box — which
    # is what a person would have done in the first place.
    try:
        v = browser.type_search(pb["domain"], q, headed=bool(pb.get("needs_headed")))
    except Exception as e:
        return [], err or type(e).__name__
    offers = browser.harvest(v.get("collected"), pb["domain"], identity)
    if offers:
        remember_search_url(pb, v.get("url"), q)
    return offers, None


def run_llm_parse(pb, q):
    from agent import llm
    url = pb["search_url"].format(q=urllib.parse.quote(q))
    html = fetch(url)
    html = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"\s+", " ", html)[:30_000]
    hint = pb.get("price_selector", "")
    return llm.complete_json("parse",
        "Extract product offers from this store search-results page. JSON only:"
        ' [{"title": str, "brand": str, "price": number, "currency": str, "url": str}].'
        " Absolute URLs. Empty array if no real product results."
        + (f" Store hint: {hint}" if hint else ""),
        f"Query: {q}\nPage from {pb['domain']}:\n{html}", max_tokens=3000)

def hunt(identity, deep=False, report=None, skip=None, on_store=None,
         should_stop=None):
    """Collect offers; if `report` is a list, append one row per store:
    {store, method, hits, error} — the coverage view the UI shows.
    `skip`: domains to leave out. `on_store(row, store_offers)` fires the
    moment each store finishes — the live-progress hook. `should_stop()` is
    checked before every store and every query, so the stop button lands within
    one page load instead of at the end of the sweep."""
    from agent import stores
    offers = []
    skip = set(skip or [])
    queue, off_world = stores.order([pb for _, pb in playbooks()], identity)

    # One chip, not thirty-three: say how many shops were ruled out and why,
    # without burying the shops that were actually asked.
    if off_world:
        # one chip, but an honest one: "47 not asked — 36 sell home goods,
        # 11 gated storefronts", not the first reason stamped on all of them
        from collections import Counter
        reasons = Counter(why for _, why in off_world)
        short = {r: (r if len(r) < 40 else r[:37] + "…") for r in reasons}
        row = {"store": f"{len(off_world)} shops not asked",
               "method": "relevance", "hits": 0,
               "error": "skipped — " + "; ".join(f"{n} {short[r]}" for r, n in reasons.most_common(3)),
               "domains": [pb["domain"] for pb, _ in off_world]}
        if report is not None:
            report.append(row)
        if on_store:
            on_store(row, [])

    for pb in queue:
        if should_stop and should_stop():
            break
        method = pb.get("method", "search-url")
        if pb["domain"] in skip:
            row = {"store": pb["domain"], "method": method, "hits": 0, "error": "skipped (by you)"}
            if report is not None:
                report.append(row)
            if on_store:
                on_store(row, [])
            continue
        if method == "llm-parse" and not deep:
            row = {"store": pb["domain"], "method": method,
                   "hits": 0, "error": "skipped (needs deep)"}
            if report is not None:
                report.append(row)
            if on_store:
                on_store(row, [])
            continue
        store_hits, store_err, store_offers = 0, None, []
        # every browser query is a page load; two phrasings is the honest
        # ceiling per shop, and the style code goes first
        for q in queries_for(identity, pb)[:2 if method == "search-url" else 3]:
            if should_stop and should_stop():
                break
            t0, hits, err = time.time(), [], None
            try:
                if method == "shopify-suggest":
                    hits = from_catalog(pb, identity) or run_shopify_suggest(pb, q)
                elif method == "llm-parse":
                    hits = [h for h in run_llm_parse(pb, q) if h.get("price")]
                    for h in hits:
                        h["store"] = pb["domain"]
                elif method == "sitemap-index":
                    # this shop refuses to be searched; read its own index
                    hits = from_index(pb, identity)
                elif READ.get("sitemap_index", True) and index_first(pb, identity) is not None:
                    # A fresh index answers in ~0.2s against 5-8s for a live
                    # search, and costs the shop nothing. It is a lead list,
                    # not an answer — the page visit still reads the price, so
                    # a stale entry shows up as dead or price-changed rather
                    # than as a wrong number. When it finds nothing we fall
                    # through and search the shop for real.
                    hits = index_first(pb, identity)
                else:  # search-url: open it in a real browser like a human would
                    from agent import browser
                    if browser.available():
                        if pb.get("needs_headed") and not READ.get("headed_when_blocked", True):
                            pb = dict(pb, needs_headed=False)
                        hits, err = browser_search(pb, identity, q, deep)
                        if err:
                            # the shop would not be read. Before giving up and
                            # handing back a link, look in its own index, then
                            # ask Google's index for what it already holds.
                            hits = from_index(pb, identity)
                            if not hits:
                                try:
                                    from agent import google
                                    if READ.get("google") and google.available():
                                        hits = google.offers_for(pb, identity)
                                except Exception:
                                    hits = []
                            if not hits and pb.get("archive", True) and READ.get("archive", True):
                                # last resort: the public web archive already
                                # holds this shop's pages; read the price there
                                try:
                                    from agent import archive
                                    hits = archive.offers_for(pb, identity)
                                except Exception:
                                    hits = []
                            if not hits:
                                hits = [{"store": pb["domain"], "title": None,
                                         "price": None, "manual": True, "why": err,
                                         "url": url_variants(pb, q)[0]}]
                    else:
                        hits = [{"store": pb["domain"], "title": None, "price": None,
                                 "url": url_variants(pb, q)[0], "manual": True,
                                 "why": "no browser installed"}]
            except Exception as e:
                err = type(e).__name__
            log_attempt({"store": pb["domain"], "method": method, "query": q,
                         "hits": len([h for h in hits if not h.get("manual")]),
                         "error": err, "ms": int((time.time() - t0) * 1000),
                         "item": identity.get("sku") or identity["query"]})
            for h in hits:   # every offer knows its currency (store's, unless page said)
                h.setdefault("currency", pb.get("currency", "USD"))
                if pb.get("needs_headed"):
                    h["needs_headed"] = True     # its product page needs the visible browser too
            offers += hits
            store_offers += hits
            store_hits += len([h for h in hits if not h.get("manual")])
            store_err = store_err or err
            if hits:
                break   # this store answered (a manual link counts) — next store
            time.sleep(pb.get("rate_limit_s", 1))
        row = {"store": pb["domain"], "method": method,
               "hits": store_hits, "error": store_err}
        if report is not None:
            report.append(row)
        if on_store:
            on_store(row, store_offers)
    return offers

if __name__ == "__main__":
    deep = "--deep" in sys.argv
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    from agent.identify import identify
    identity = identify(q)
    print(json.dumps({"identity": identity, "offers": hunt(identity, deep)},
                     ensure_ascii=False, indent=1))
