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


def find(domain, identity, limit=6):
    """Archived product pages at this shop that plausibly are the item."""
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
    from agent.identify import identify
    d, q = sys.argv[1], " ".join(sys.argv[2:])
    idy = identify(q, prefer_catalog=True)
    print(f'hunting {idy.get("brand")} / {idy.get("product")} at {d} via {crawls(2)}')
    for o in offers_for({"domain": d}, idy):
        print(f'  {o["price"]:>8} {o["currency"] or "":4s} crawled {o["crawled"]}  {o["page_name"][:48]!r}')
        print(f'           {o["url"][:100]}')
