#!/usr/bin/env python3
"""Search a shop by its own published index instead of its search box.

Some shops refuse to be searched — an edge firewall answers every request with
403, or search is a JavaScript widget with no url. But a shop that wants to be
found publishes a sitemap: a list of every product url, linked from robots.txt,
written specifically so crawlers can read it. That is a consented route by
construction, and it turns "search this shop" into "look it up in an index we
already hold".

    python3 -m agent.sitemap build giglio.com
    python3 -m agent.sitemap find  giglio.com  salomon xt-6

What is honoured here matters as much as what is fetched. robots.txt is read
first, every Disallow for `*` is respected, and a shop that disallows a path is
simply not crawled there. A Sitemap: line is an invitation; a Disallow is a
refusal, and neither is a puzzle to route around.
"""
import html as _html
import json, os, re, sys, urllib.request, ssl, gzip
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "data", "index")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# Sitemaps that never list products. Shops publish a lot of these, and one
# 10 Corso Como run spent its whole budget re-reading sitemap_blogs_1.xml.
NOT_PRODUCT_SITEMAP = re.compile(
    r"(blog|article|news|magazine|stylemag|journal|editorial|lookbook|"
    r"metaobject|filter[_-]?page|collection|categor|brand|designer|store|"
    r"author|tag|page[s_-]|cms|policy|discovery)", re.I)


def _sitemap_key(url):
    """A sitemap's identity ignoring locale. 10 Corso Como publishes the same
    file under /en-gb/, /en-us/, /en-row/, /it/, /en-ca/, /en-hk/ and /en-sg/;
    they are one catalogue, and reading it seven times is six wasted budgets."""
    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
    return re.sub(r"[-_](?:[a-z]{2}([-_][a-z]{2})?)(?=\.xml)", "", name)


# Deliberately NOT a "is this a product url" filter. Plenty of shops name
# products with plain hierarchical slugs (/sv/accessoarer/balten/...) that
# carry no marker at all — careofcarl publishes 17,602 such urls and a
# product-path regex kept 12 of them. The index is a url corpus; match.py
# does the selecting at query time, and it already demands the model code in
# the slug, which is far more selective than any path heuristic.
#
# So this only drops what is definitely not a product page.
NOT_PRODUCT_URL = re.compile(
    r"/(cart|checkout|account|login|register|wishlist|compare|order[s-]|"
    r"blog|news|magazine|journal|stories|lookbook|press|careers|jobs|"
    r"help|faq|support|about|contact|azienda|info|policy|policies|terms|"
    r"privacy|cookie|shipping|returns|sitemap|admin|api|robots|"
    r"size-guide|store-locator|gift-card)(/|$|\.)", re.I)


def _fetch(url, timeout=20, browser_ok=True):
    """Plain HTTP first — it is 20x cheaper. A real browser only if refused."""
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                   timeout=timeout, context=_ctx)
        b = r.read(8_000_000)
        if b[:2] == b"\x1f\x8b":
            b = gzip.decompress(b)
        return r.status, b.decode("utf-8", "replace")
    except Exception:
        pass
    if not browser_ok:
        return None, ""
    try:
        from agent import browser

        def job(br, u):
            ctx = browser._context(br)
            try:
                resp = ctx.request.get(u, timeout=45000)
                try:
                    b = resp.body()          # bytes: a .xml.gz read as text is garbage
                    if b[:2] == b"\x1f\x8b":
                        b = gzip.decompress(b)
                    return resp.status, b[:8_000_000].decode("utf-8", "replace")
                except Exception:
                    return resp.status, ""
            finally:
                ctx.close()
        return browser._submit(job, url)
    except Exception:
        return None, ""


def robots(domain):
    """{'sitemaps': [...], 'disallow': [...]} — the shop stating its own terms."""
    for host in ("www." + domain.removeprefix("www."), domain):
        status, txt = _fetch(f"https://{host}/robots.txt")
        if status != 200 or not re.search(r"(?im)^\s*(user-agent|disallow|sitemap)", txt or ""):
            continue
        sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", txt)
        disallow = []
        for block in re.split(r"(?im)^user-agent:", txt):
            if block.strip().startswith("*"):
                disallow += [d for d in re.findall(r"(?im)^\s*disallow:\s*(\S*)", block) if d]
        if not sitemaps:                    # not advertised, but conventional
            for guess in ("/sitemap.xml", "/sitemap_index.xml"):
                s, b = _fetch(f"https://{host}{guess}")
                if s == 200 and "<loc>" in (b or ""):
                    sitemaps = [f"https://{host}{guess}"]
                    break
        return {"host": host, "sitemaps": sitemaps, "disallow": disallow}
    return {"host": None, "sitemaps": [], "disallow": []}


def _allowed(url, disallow):
    path = re.sub(r"^https?://[^/]+", "", url) or "/"
    for rule in disallow:
        pat = re.escape(rule).replace(r"\*", ".*")
        if re.match(pat, path):
            return False
    return True


def _locs(xml):
    # &amp; is how XML spells & — Shopify paginates product sitemaps as
    # sitemap_products_1.xml?from=…&amp;to=… and the raw string is not a url
    return [_html.unescape(u) for u in
            re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "")]


def _is_sitemap(url):
    """A sitemap even when it carries a query string. Shopify paginates with
    ?from=&to=, so `.endswith(".xml")` classified every product sitemap as an
    ordinary url — and they were then discarded as if they were pages."""
    return url.split("?")[0].rstrip("/").endswith((".xml", ".xml.gz"))


def build(domain, max_urls=80_000, max_sitemaps=60, verbose=True, headed=False):
    """Walk the shop's sitemaps and write every product url to the index.
    `headed`: fetch through the visible Chrome — Mr Porter and Bloomingdale's
    publish a sitemap and refuse it to anything but a real window."""
    from agent import browser
    browser.FORCE_HEADED = bool(headed)
    info = robots(domain)
    if not info["sitemaps"]:
        return {"domain": domain, "error": "no sitemap published", "urls": 0}
    seen, queue, fetched = set(), list(info["sitemaps"]), 0
    visited, keys = set(), set()
    while queue and len(seen) < max_urls and fetched < max_sitemaps:
        sm = queue.pop(0)
        if sm in visited or not _allowed(sm, info["disallow"]):
            continue
        visited.add(sm)
        if NOT_PRODUCT_SITEMAP.search(sm.split("?")[0].rsplit("/", 1)[-1]):
            continue            # blogs, cms pages, collection listings
        status, xml = _fetch(sm)
        fetched += 1
        if status != 200 or not xml:
            continue
        locs = _locs(xml)
        subs = [l for l in locs if _is_sitemap(l)]
        locs = [l for l in locs if not _is_sitemap(l)]
        # Locale de-duplication applies to LEAF sitemaps only. Index files
        # share a filename across locales (/en-gb/sitemap.xml, /en-us/...),
        # so keying them dropped every branch of the tree and returned zero.
        if not subs:
            key = _sitemap_key(sm)
            if key in keys:
                continue        # same catalogue file, another language
            keys.add(key)
        if subs:
            # prefer the sub-sitemaps that plainly hold products
            fresh = [x for x in subs if x not in visited]
            ranked = sorted(fresh, key=lambda s: 0 if re.search(
                r"produc|item|catalog|shop|clothing|abbigl|prod", s, re.I) else 1)
            queue = ranked + queue
            # a file can be BOTH an index and a list of urls — keep both
        for u in locs:
            if len(seen) >= max_urls:
                break
            if not _allowed(u, info["disallow"]):
                continue
            if NOT_PRODUCT_URL.search(u):
                continue
            # drop only the bare host. Counting slashes in the whole url cut
            # every shop with a flat structure — madeindesign names products
            # /prod-<slug> at the root and lost 18,000 of them that way.
            path = re.sub(r"^https?://[^/]+", "", u)
            if not [seg for seg in path.split("/") if seg]:
                continue
            seen.add(u.split("?")[0])
        if verbose:
            print(f"    {sm[-64:]:64s} +{len(locs):6d} locs  index={len(seen)}", flush=True)

    os.makedirs(INDEX, exist_ok=True)
    path = os.path.join(INDEX, domain.replace("/", "_") + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)))
    meta = {"domain": domain, "urls": len(seen), "built": date.today().isoformat(),
            "sitemaps_fetched": fetched, "disallow": info["disallow"][:12]}
    json.dump(meta, open(path.replace(".txt", ".json"), "w", encoding="utf-8"), indent=1)
    return meta


def has_index(domain):
    return os.path.exists(os.path.join(INDEX, domain.replace("/", "_") + ".txt"))


def age_days(domain):
    p = os.path.join(INDEX, domain.replace("/", "_") + ".json")
    try:
        built = json.load(open(p, encoding="utf-8"))["built"]
        return (date.today() - date.fromisoformat(built)).days
    except Exception:
        return None


def find(domain, identity, limit=6, floor=None):
    """Look the item up in this shop's index. Returns leads in the hunter's
    offer shape — url only, because a sitemap carries no prices; the page
    visit reads those, exactly as it does for a search result."""
    from agent import match, browser
    path = os.path.join(INDEX, domain.replace("/", "_") + ".txt")
    if not os.path.exists(path):
        return []
    if floor is None:
        floor = browser.HARVEST_FLOOR
    out = []
    with open(path, encoding="utf-8") as f:
        for url in f:
            url = url.strip()
            if not url:
                continue
            slug = re.sub(r"[^A-Za-z0-9]+", " ", re.sub(r"^https?://[^/]+", "", url))
            sc, why = match.score(slug, identity, url=url, extra=slug)
            if sc >= floor:
                out.append({"store": domain, "url": url, "title": slug.strip(),
                            "price": None, "match": sc, "match_why": why,
                            "from_index": True})
    # One product under many locale prefixes (/en-ae/, /en-at/, /en-gb/ …) is
    # one lead, not six: Mr Porter's index handed back the same shoe in six
    # locales and nothing else fitted in the limit.
    seen, dedup = set(), []
    for o in sorted(out, key=lambda o: -o["match"]):
        key = re.sub(r"^https?://[^/]+/", "", o["url"])
        key = re.sub(r"^[a-z]{2}(?:-[a-z]{2})?/", "", key).rstrip("/")
        if key in seen:
            continue
        seen.add(key); dedup.append(o)
    return dedup[:limit]


def build_all(domains=None, max_age_days=7, verbose=True):
    """Build or refresh an index for every shop that publishes a sitemap.

    Resumable by design: a shop with a fresh index is skipped, so an
    interrupted run costs nothing to restart. Shops that publish nothing are
    recorded with the reason rather than retried silently forever."""
    import glob as _glob
    if domains is None:
        domains = []
        for f in sorted(_glob.glob(os.path.join(ROOT, "playbooks", "*.json"))):
            if os.path.basename(f).startswith("_"):
                continue
            pb = json.load(open(f, encoding="utf-8"))
            if pb.get("skip"):
                continue          # a shop we have been asked not to crawl
            domains.append(pb["domain"])

    done, skipped, failed = [], [], []
    for i, d in enumerate(domains, 1):
        age = age_days(d)
        if age is not None and age <= max_age_days:
            skipped.append((d, f"fresh ({age}d old)"))
            if verbose:
                print(f"[{i}/{len(domains)}] {d:26s} skip — index is {age}d old", flush=True)
            continue
        if verbose:
            print(f"[{i}/{len(domains)}] {d}", flush=True)
        try:
            pbf = os.path.join(ROOT, "playbooks", d.replace(".", "-") + ".json")
            needs = json.load(open(pbf, encoding="utf-8")).get("needs_headed", False) if os.path.exists(pbf) else False
            meta = build(d, verbose=verbose, headed=needs)
        except Exception as e:
            meta = {"domain": d, "error": f"{type(e).__name__}: {e}", "urls": 0}
        if meta.get("urls"):
            done.append((d, meta["urls"]))
        else:
            failed.append((d, meta.get("error", "no urls")))
        if verbose:
            print(f'    -> {meta.get("urls", 0):,} urls  {meta.get("error", "")}', flush=True)
    return {"built": done, "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        headed = "--headed" in sys.argv
        for d in [a for a in sys.argv[2:] if not a.startswith("--")]:
            print(f"=== {d}")
            print("   ", json.dumps(build(d, headed=headed), ensure_ascii=False))
    elif cmd == "build-all":
        r = build_all(sys.argv[2:] or None)
        print(f'\n=== built {len(r["built"])} · skipped {len(r["skipped"])} · '
              f'no index {len(r["failed"])}')
        for d, n in sorted(r["built"], key=lambda t: -t[1]):
            print(f'   {d:28s} {n:>7,} urls')
        if r["failed"]:
            print("\n   nothing published / unreachable:")
            for d, why in r["failed"]:
                print(f'   {d:28s} {str(why)[:64]}')
    elif cmd == "find":
        from agent.identify import identify
        d, q = sys.argv[2], " ".join(sys.argv[3:])
        for o in find(d, identify(q)):
            print(f'  {o["match"]:.2f}  {o["url"]}')
