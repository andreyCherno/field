#!/usr/bin/env python3
"""Prices for shops that refuse to be read, from the public web archive.

Some shops answer HTTP 403 to everything. Common Crawl has already fetched
their product pages, and publishes both the index (CDX, a keyless public API)
and the pages themselves (WARC records on data.commoncrawl.org). The archived
HTML carries the same schema.org Product block the live page does — so the
price can be read with the exact parser that reads live pages.

What this buys and what it costs, stated plainly:
  * needs no key, no account, no card, and touches the blocked shop never
  * the price is as old as the crawl — weeks, not seconds — so it is labelled
    `archived` with its crawl date and can never be called `verified`
  * the CDX server rate-limits; queries go one at a time with backoff

    python3 -m agent.archive revolve.com adidas adizero evo sl
"""
import gzip, io, json, os, re, ssl, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "data", "cc_crawls.json")
UA = {"User-Agent": "field-hunter/1.0 (personal price research)"}
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
_last_call = 0.0


def crawls(n=2):
    """Most recent crawl ids, cached for a day."""
    try:
        c = json.load(open(CACHE, encoding="utf-8"))
        if time.time() - c.get("at", 0) < 86400:
            return c["ids"][:n]
    except (OSError, ValueError):
        pass
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://index.commoncrawl.org/collinfo.json", headers=UA), timeout=30, context=_ctx)
        ids = [x["id"] for x in json.load(r)]
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({"at": time.time(), "ids": ids[:6]}, open(CACHE, "w"))
        return ids[:n]
    except Exception:
        return ["CC-MAIN-2026-34", "CC-MAIN-2026-30"]


def _cdx(crawl, pattern, url_regex=None, limit=60, tries=3):
    """One index query, politely: spaced out and backed off on 503."""
    global _last_call
    q = (f"https://index.commoncrawl.org/{crawl}-index?url={urllib.parse.quote(pattern)}"
         f"&output=json&filter=status:200&filter=mime:text/html&limit={limit}")
    if url_regex:
        q += "&filter=~url:" + urllib.parse.quote(url_regex)
    for t in range(tries):
        wait = 4 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request(q, headers=UA),
                                       timeout=120, context=_ctx)
            body = r.read(6_000_000).decode("utf-8", "replace")
            return [json.loads(l) for l in body.splitlines() if l.strip()], None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return [], "absent"
            time.sleep(10 * (t + 1))
        except Exception:
            time.sleep(8 * (t + 1))
    return [], "unanswered"


def _slug_regex(identity):
    """The strongest url-shaped clue: the model code if there is one, else the
    product words, matched loosely against hyphen/underscore slugs."""
    from agent import match
    name = " ".join(filter(None, [identity.get("brand"), identity.get("product")])) \
        or identity.get("query", "")
    toks = match.model_tokens(name)
    if not toks:
        toks = [w for w in match.words(identity.get("product") or name)
                if len(w) > 2 and w not in match.STOP][:2]
    if not toks:
        return None
    return "(?i).*" + ".*".join(re.escape(t) for t in toks[:2]) + ".*"


INDEX_DIR = os.path.join(ROOT, "data", "index")
# server-side narrowing for shops whose product urls carry a marker; the rest
# are pulled whole up to a cap and sifted locally
PRODUCT_HINT = r".*(/prd/|/product/|/products/|/dp/|/p/|/item/).*"


def _cdx_pages(crawl, pattern, url_regex):
    q = (f"https://index.commoncrawl.org/{crawl}-index?url={urllib.parse.quote(pattern)}"
         f"&output=json&showNumPages=true")
    if url_regex:
        q += "&filter=~url:" + urllib.parse.quote(url_regex)
    try:
        r = urllib.request.urlopen(urllib.request.Request(q, headers=UA), timeout=60, context=_ctx)
        return int(json.load(r).get("pages", 0))
    except Exception:
        return 0


def build(domain, max_pages=40, use_hint=True, verbose=True):
    """Pull the archive's whole listing for a shop ONCE into a local index, so
    a hunt never waits on the rate-limited CDX server again.

    The live path costs 15-60s per shop per hunt: several index queries, each
    spaced four seconds apart, before the first page can even be fetched.
    With the listing on disk a lookup is a scan of a local file, and only the
    one WARC range request per candidate page stays live (~1s)."""
    global _last_call
    rx = PRODUCT_HINT if use_hint else None
    rows_out, crawl_used = {}, None
    for crawl in crawls(2):
        for host in (f"www.{domain}", domain):
            pattern = f"{host}/*"
            pages = _cdx_pages(crawl, pattern, rx)
            if verbose:
                print(f"    {crawl} {pattern}: {pages} index pages", flush=True)
            if not pages:
                continue
            for pg in range(min(pages, max_pages)):
                q = (f"https://index.commoncrawl.org/{crawl}-index?url={urllib.parse.quote(pattern)}"
                     f"&output=json&filter=status:200&filter=mime:text/html&page={pg}")
                if rx:
                    q += "&filter=~url:" + urllib.parse.quote(rx)
                got = None
                for t in range(4):
                    wait = 4 - (time.time() - _last_call)
                    if wait > 0:
                        time.sleep(wait)
                    _last_call = time.time()
                    try:
                        r = urllib.request.urlopen(urllib.request.Request(q, headers=UA),
                                                   timeout=180, context=_ctx)
                        got = r.read(60_000_000).decode("utf-8", "replace")
                        break
                    except urllib.error.HTTPError as e:
                        if e.code == 404:
                            got = ""; break
                        time.sleep(12 * (t + 1))
                    except Exception:
                        time.sleep(10 * (t + 1))
                if got is None:
                    if verbose:
                        print(f"      page {pg}: unanswered, stopping this crawl", flush=True)
                    break
                n = 0
                for line in got.splitlines():
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    u = rec.get("url", "").split("?")[0]
                    if not u or u in rows_out:
                        continue
                    rows_out[u] = {"u": u, "t": rec["timestamp"], "f": rec["filename"],
                                   "o": int(rec["offset"]), "l": int(rec["length"])}
                    n += 1
                if verbose:
                    print(f"      page {pg}: +{n}  total {len(rows_out):,}", flush=True)
            if rows_out:
                crawl_used = crawl
                break
        if rows_out:
            break
    os.makedirs(INDEX_DIR, exist_ok=True)
    path = os.path.join(INDEX_DIR, domain + ".cc.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for rec in rows_out.values():
            f.write(json.dumps(rec) + "\n")
    meta = {"domain": domain, "urls": len(rows_out), "crawl": crawl_used,
            "built": datetime.now(timezone.utc).date().isoformat()}
    json.dump(meta, open(path.replace(".jsonl", ".json"), "w"), indent=1)
    return meta


def has_local(domain):
    return os.path.exists(os.path.join(INDEX_DIR, domain + ".cc.jsonl"))


def find_local(domain, identity, limit=6):
    """Scan the local archive listing — no network at all."""
    from agent import match, browser
    path = os.path.join(INDEX_DIR, domain + ".cc.jsonl")
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            u = rec["u"]
            slug = re.sub(r"[^A-Za-z0-9]+", " ", re.sub(r"^https?://[^/]+", "", u))
            sc, why = match.score(slug, identity, url=u, extra=slug)
            if sc >= browser.HARVEST_FLOOR:
                out.append({"url": u, "match": sc, "match_why": why, "crawl": "local",
                            "timestamp": rec["t"], "filename": rec["f"],
                            "offset": rec["o"], "length": rec["l"]})
    out.sort(key=lambda o: -o["match"])
    return out[:limit]


def find(domain, identity, limit=6):
    """Archived product pages at this shop that plausibly are the item.
    A local listing is scanned first (instant); the live index only when there
    is none."""
    if has_local(domain):
        hits = find_local(domain, identity, limit)
        if hits:
            return hits
    from agent import match
    rx = _slug_regex(identity)
    out = []
    for crawl in crawls(2):
        for host in (f"www.{domain}", domain):
            rows, err = _cdx(crawl, f"{host}/*", rx, limit=80)
            if err == "absent":
                continue
            if not rows and rx:
                # the slug may not carry the words in the shape the regex
                # expected; take a plain sample and let match.score decide
                rows, err = _cdx(crawl, f"{host}/*", None, limit=200)
            if not rows:
                continue
            for r in rows:
                u = r.get("url", "").split("?")[0]
                slug = re.sub(r"[^A-Za-z0-9]+", " ", re.sub(r"^https?://[^/]+", "", u))
                sc, why = match.score(slug, identity, url=u, extra=slug)
                if sc >= 0.6:
                    out.append({"url": u, "match": sc, "match_why": why, "crawl": crawl,
                                "timestamp": r["timestamp"], "filename": r["filename"],
                                "offset": int(r["offset"]), "length": int(r["length"])})
            if out:
                break
        if out:
            break
    seen, dedup = set(), []
    for o in sorted(out, key=lambda o: -o["match"]):
        if o["url"] not in seen:
            seen.add(o["url"]); dedup.append(o)
    return dedup[:limit]


def read(rec):
    """Pull one archived page and read it like a live one."""
    from agent import browser, fx
    req = urllib.request.Request("https://data.commoncrawl.org/" + rec["filename"],
                                 headers={**UA, "Range": f'bytes={rec["offset"]}-{rec["offset"] + rec["length"] - 1}'})
    raw = urllib.request.urlopen(req, timeout=90, context=_ctx).read()
    data = gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode("utf-8", "replace")
    html = data.split("\r\n\r\n", 2)[-1]
    out = {"name": None, "brand": "", "price": None, "currency": None, "in_stock": None,
           "img": "", "sku": None, "read_by": None,
           "crawled": datetime.strptime(rec["timestamp"][:8], "%Y%m%d").date().isoformat()}
    nodes = browser.jsonld_products(html)
    if nodes:
        p = browser.read_product(nodes[0])
        out.update({k: p[k] for k in ("name", "brand", "in_stock", "img", "sku")})
        if p["price"] is not None:
            out.update(price=p["price"], currency=p["currency"], read_by="jsonld")
    if out["price"] is None:
        m = re.search(r'(?:property|name)="(?:product|og):price:amount"[^>]+content="([^"]+)"', html)
        if m:
            c = re.search(r'(?:product|og):price:currency"[^>]+content="([^"]+)"', html)
            out.update(price=fx.parse_amount(m.group(1)), read_by="meta",
                       currency=(c.group(1).upper() if c else None))
    if not out["name"]:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        if h1:
            out["name"] = re.sub(r"<[^>]+>", " ", h1.group(1)).strip()
    return out


def offers_for(pb, identity, limit=4):
    """Leads in the hunter's offer shape, priced from the archive.
    Status `archived`: a real number, a stated date, never `verified`."""
    out = []
    for rec in find(pb["domain"], identity, limit=limit):
        try:
            page = read(rec)
        except Exception:
            continue
        if page["price"] is None:
            continue
        out.append({"store": pb["domain"], "url": rec["url"], "title": page["name"] or rec["url"],
                    "page_name": page["name"], "price": page["price"], "currency": page["currency"],
                    "in_stock": page["in_stock"], "img": page["img"],
                    "match": rec["match"], "match_why": rec["match_why"],
                    "from_archive": True, "crawled": page["crawled"], "read_by": page["read_by"],
                    "why": f'price as archived by Common Crawl on {page["crawled"]} — '
                           f'the shop refuses direct reads'})
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        for d in sys.argv[2:]:
            print(f"=== {d}", flush=True)
            print("   ", json.dumps(build(d)), flush=True)
        print("ARCHIVE BUILD DONE", flush=True)
        raise SystemExit(0)
    from agent.identify import identify
    d, q = sys.argv[1], " ".join(sys.argv[2:])
    idy = identify(q, prefer_catalog=True)
    print(f'hunting {idy.get("brand")} / {idy.get("product")} at {d} via {crawls(2)}')
    for o in offers_for({"domain": d}, idy):
        print(f'  {o["price"]:>8} {o["currency"] or "":4s} crawled {o["crawled"]}  {o["page_name"][:48]!r}')
        print(f'           {o["url"][:100]}')
