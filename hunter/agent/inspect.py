#!/usr/bin/env python3
"""Step 3 of a hunt: open the candidate's own product page in a real browser,
read the name and the price off *that page*, and decide whether it is the item
we hunted.

Why this file exists: a search-results row is a lead, not a fact. The store's
search engine returns what it feels like, and the number in a results grid is
often a "from" price, a pre-discount price, or the cheapest variant. So the
hunter walks in the front door of every candidate — one page, one read — and
publishes only what the page itself says.

Price provenance is ranked and recorded, never blended:
    jsonld > meta > text
A price scraped out of visible text is corroboration only: it is accepted when
it agrees with the lead, and otherwise leaves the offer `price-unconfirmed`
rather than inventing a number. Unknown stays unknown.

Without Playwright installed this falls back to verify.py's plain HTTP check,
so a machine with no browser still works — it just learns less.
"""
import html as _html
import json, os, re
from datetime import datetime, timezone
from urllib.parse import urlparse

from agent import browser, fx, match

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(os.path.dirname(HERE), "config.json"), encoding="utf-8"))
M = CFG.get("match", {})
CANDIDATE_MIN = M.get("candidate_min", 0.45)     # worth opening the page for
ACCEPT_MIN = M.get("accept_min", 0.60)           # worth showing you
PER_STORE = M.get("inspect_per_store", 3)
TOL = M.get("price_tolerance", 0.02)
# A lead whose currency differs from the page's was a *converted* number (the
# shelf stores everything in ILS), so it can never agree to 2% — comparing it
# raw reports "price-changed" on a page that never changed. Compare in USD and
# allow for the fx spread between whenever the lead was priced and now.
FX_TOL = M.get("fx_price_tolerance", 0.10)

MY_SIZES = CFG.get("sizes") or []
_SIZE_ALIAS = {"small": "s", "medium": "m", "large": "l", "xlarge": "xl",
               "x-large": "xl", "xxlarge": "xxl", "xsmall": "xs", "x-small": "xs"}


# What a size actually looks like. Without this, JSON-LD variant *skus*
# (126184, 250760) get read as sizes, because a variant offer often carries no
# name and the sku is the only label on it.
SIZE_SHAPE = re.compile(
    r"^(?:x{0,3}(?:s|l)|m|os|one ?size|u|"
    r"(?:us|eu|uk|it|fr|jp)? ?\d{1,2}(?:[.,]5)?|\d{2}(?:[.,]5)?[-/]\d{2})$", re.I)


def looks_like_size(s):
    t = str(s or "").strip()
    return bool(t) and len(t) <= 8 and bool(SIZE_SHAPE.match(t))


def size_system(s):
    """'alpha' (S/M/L) or 'numeric' (9.5, 43) — you cannot compare across the
    two, and pretending you can is how every sneaker gets told it is not your
    size because your profile says 'M'."""
    t = norm_size(s)
    if re.fullmatch(r"[a-z]{1,4}", t or ""):
        return "alpha"
    if re.fullmatch(r"[\d.]+", t or ""):
        return "numeric"
    return None


def norm_size(s):
    """'US 9.5' / 'us9.5' / '9,5' all mean one size; 'Medium' and 'M' too."""
    t = re.sub(r"\s+", "", str(s or "").strip().lower()).replace(",", ".")
    t = re.sub(r"^(us|eu|uk|fr|it|jp|size)[-_.]?", "", t)
    t = re.sub(r"\.0$", "", t)
    return _SIZE_ALIAS.get(t, t)


SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "₪": "ILS", "¥": "JPY", "₩": "KRW"}
META_RE = ('<meta[^>]+(?:property|name)=["\']{key}["\'][^>]+content=["\']([^"\']+)["\']',
           '<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{key}["\']')
PRICE_TEXT = re.compile(r'([$€£₪¥])\s?(\d[\d.,]*)|(\d[\d.,]*)\s?(USD|EUR|GBP|ILS|NIS|SEK|DKK|CHF)\b')


def _meta(html, key):
    for pat in META_RE:
        m = re.search(pat.format(key=re.escape(key)), html, re.I)
        if m:
            return m.group(1).strip()
    return None


def _num(s):
    return fx.parse_amount(s)


def read_page(url, timeout_ms=20000, headed=False):
    """Open one product page and report what it actually says.
    Returns None when the page could not be opened at all."""
    try:
        v = browser.visit(url, wait_ms=4000, timeout_ms=timeout_ms,
                          ready_js=browser.PRODUCT_READY, collect=browser.SIZES_JS, headed=headed)
    except Exception as e:
        return {"error": type(e).__name__}
    html, text = v["html"], v["text"]

    # did we land where we were sent? a delisted product 302s to the homepage
    want, got = urlparse(url), urlparse(v["url"])
    tail = want.path.rstrip("/").rsplit("/", 1)[-1]
    moved = bool(tail) and tail not in got.path
    out = {"name": None, "brand": "", "price": None, "currency": None,
           "in_stock": None, "img": "", "sku": None, "read_by": None, "page_sizes": [],
           "status_code": v["status"], "landed_on": v["url"] if moved else None,
           "text": text[:4000]}
    if v["status"] and v["status"] >= 400:
        return {"error": f'HTTP {v["status"]}'}

    out["page_sizes"] = _sizes_from(v.get("collected"), html)
    nodes = browser.jsonld_products(html)
    if nodes:
        # the page's own product is the first Product in document order; if one
        # node's url is this page, trust that over position
        best = next((n for n in nodes
                     if str((browser.read_product(n) or {}).get("url", "")).rstrip("/")
                     .endswith(url.split("?")[0].rstrip("/").rsplit("/", 1)[-1])), nodes[0])
        p = browser.read_product(best)
        out.update({k: p[k] for k in ("name", "brand", "in_stock", "img", "sku")})
        if p["price"] is not None:
            out.update(price=p["price"], currency=p["currency"], read_by="jsonld")

    if out["price"] is None:                       # meta tags: the next-best source
        amount = _num(_meta(html, "product:price:amount") or _meta(html, "og:price:amount"))
        if amount is not None:
            out.update(price=amount, read_by="meta",
                       currency=(_meta(html, "product:price:currency")
                                 or _meta(html, "og:price:currency") or out["currency"]))
    if not out["name"]:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        out["name"] = (re.sub(r"<[^>]+>", " ", h1.group(1)) if h1
                       else _meta(html, "og:title") or "").strip() or None
    for k in ("name", "brand"):               # stores emit &quot; and &amp; raw
        if out.get(k):
            out[k] = _html.unescape(re.sub(r"\s+", " ", out[k])).strip()
    if not out["img"]:
        out["img"] = _meta(html, "og:image") or ""
    if out["in_stock"] is None:
        low = text.lower()
        if any(w in low for w in ("out of stock", "sold out", "אזל מהמלאי", "אזל במלאי")):
            out["in_stock"] = False

    if out["price"] is None:                       # visible text: corroboration only
        m = PRICE_TEXT.search(text)
        if m:
            sym, a, b, code = m.groups()
            out.update(price=_num(a or b), read_by="text",
                       currency=SYMBOL.get(sym) if sym else ("ILS" if code == "NIS" else code))
    # redirected away AND nothing product-shaped here: the listing is gone, not wrong
    if moved and out["read_by"] in (None, "text"):
        return {"error": f'redirected to {got.netloc}{got.path or "/"} — listing is gone'}
    return out


def _sizes_from(dom_sizes, html):
    """[{size, available}] for this product. JSON-LD variant offers first —
    they carry real stock state — then what the page renders."""
    found = {}
    for m in re.finditer(r'"offers"\s*:\s*(\[.*?\])', html, re.S):
        try:
            arr = json.loads(m.group(1))
        except ValueError:
            continue
        for o in arr if isinstance(arr, list) else []:
            if not isinstance(o, dict):
                continue
            label = o.get("name") or o.get("sku") or ""
            label = re.sub(r"^.*?\b(?:size)\b[\s:]*", "", str(label), flags=re.I).strip()
            if not looks_like_size(label):
                continue
            avail = str(o.get("availability") or "").lower()
            found[norm_size(label)] = {
                "size": label,
                "available": True if "instock" in avail else
                             False if ("outofstock" in avail or "soldout" in avail) else None}
    for d in dom_sizes or []:
        if not looks_like_size(d.get("size")):
            continue
        k = norm_size(d.get("size"))
        if k and k not in found:
            found[k] = {"size": d.get("size"), "available": d.get("available")}
    return list(found.values())


def size_verdict(sizes):
    """Do you take one of the sizes this page can actually sell you?
    Returns (verdict, matched) — 'yes' / 'no' / None when unknowable."""
    if not sizes or not MY_SIZES:
        return None, []
    mine = {norm_size(s) for s in MY_SIZES if str(s).strip()}
    if not mine:
        return None, []
    buyable = [s for s in sizes if s.get("available") is not False]
    matched = [s["size"] for s in buyable if norm_size(s["size"]) in mine]
    if matched:
        return "yes", matched
    # "no" is only sayable when we are comparing like with like. Your profile
    # says M and L; a shoe page says 8.5 and 9. That is not a mismatch, it is
    # a question this configuration cannot answer — so it stays unknown.
    my_systems = {size_system(s) for s in mine} - {None}
    page_systems = {size_system(s["size"]) for s in buyable} - {None}
    if not (my_systems & page_systems):
        return None, []
    if buyable:
        return "no", []
    return None, []


def _colourway(sku, page, url):
    """'same' / 'different' / None. None means the page never said which."""
    m = re.match(r"^([A-Za-z0-9]{4,})[-_ ]?(\d{2,3})$", str(sku or "").strip())
    if not m:
        return None
    family, want = m.group(1).lower(), m.group(2)
    hay = " ".join(filter(None, [page.get("name"), page.get("sku"), url,
                                 (page.get("text") or "")[:4000]]))
    found = set(re.findall(rf"{re.escape(family)}[-_ ]?(\d{{2,3}})", hay, re.I))
    if not found:
        return None
    return "same" if want in found else "different"


def inspect_offer(offer, identity, timeout_ms=20000):
    """One lead -> one verdict. The page wins every disagreement.

    status is one of:
      verified          page opened, name matches, page's own price recorded
      price-changed     confirmed item, but the page charges something else
      price-unconfirmed only a text-scraped price, and it disagrees with the lead
      sold-out          confirmed item, page says it is not buyable
      wrong-item        the page is a different product than the one hunted
      unpriced          confirmed item, no price anywhere on the page
      dead              the page would not open
    """
    out = dict(offer)
    out["checked"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    out["listed_price"] = offer.get("price")
    out["listed_currency"] = offer.get("currency")

    if not browser.available():
        from agent.verify import verify_offer      # no browser on this machine
        out.update(verify_offer(offer))
        out["read_by"] = "http"
        return out

    page = read_page(offer["url"], timeout_ms, headed=bool(offer.get("needs_headed")))
    if page is None or page.get("error"):
        # The exact page refused us. Mr Porter opens its sitemap and its
        # search to a real window and still 403s every product page. Before
        # calling it dead, read the ARCHIVED copy of this same url — a real
        # number with a crawl date beats a shrug.
        err = (page or {}).get("error", "unreachable")
        if str(err).startswith("HTTP 40") and CFG.get("read", {}).get("archive", True):
            try:
                from agent import archive
                for crawl in archive.crawls(2):
                    rows, e = archive._cdx(crawl, offer["url"].split("?")[0], None, limit=3)
                    rows = [r for r in rows or [] if str(r.get("status")) == "200"]
                    if rows:
                        rec = {"url": rows[0]["url"], "timestamp": rows[0]["timestamp"],
                               "filename": rows[0]["filename"], "offset": int(rows[0]["offset"]),
                               "length": int(rows[0]["length"])}
                        ap = archive.read(rec)
                        if ap.get("price") is not None:
                            sc, why = match.score(ap["name"] or offer.get("title"), identity,
                                                  url=offer["url"], brand=ap.get("brand") or "")
                            if sc >= ACCEPT_MIN:
                                out.update(status="archived", price=ap["price"],
                                           currency=ap["currency"] or offer.get("currency"),
                                           page_name=ap["name"], in_stock=ap["in_stock"],
                                           img=ap.get("img") or out.get("img", ""),
                                           read_by=ap["read_by"], crawled=ap["crawled"],
                                           match=sc, match_why=why, from_archive=True,
                                           why=f"live page refused ({err}); price as archived "
                                               f"by Common Crawl on {ap['crawled']}")
                                return out
                        break
            except Exception:
                pass
        out["status"] = "dead"
        out["error"] = err
        return out

    out["page_name"] = page["name"]
    out["read_by"] = page["read_by"]
    # `sizes` on the lead is the shelf's list of plain strings; `page_sizes` is
    # what this page itself offers, with stock state. Two shapes, two keys.
    out["page_sizes"] = page.get("page_sizes") or offer.get("page_sizes") or []
    out["your_size"], out["your_sizes"] = size_verdict(out["page_sizes"])
    out["read_locale"] = browser.LOCALE   # which market's price this is

    # A style code names ONE colourway: 1203A740-101 is the ivory, -751 the
    # cream. Comparing it to the page's own `sku` does not work — shops put
    # their internal variant id there (126184, "00048184|WHT|7|NA"), which
    # would mark every offer as a different colour. Look for the
    # *manufacturer's* code family on the page instead, and stay silent when
    # it is not there.
    out["colourway"] = _colourway(identity.get("sku"), page, offer.get("url", ""))
    if page.get("sku"):
        out["page_sku"] = page["sku"]
    if page.get("img") and not out.get("img"):
        out["img"] = page["img"]

    # the name on the page is the authority — the search-result title was a lead
    # Evidence about the page must come FROM the page. The lead's own title
    # cannot vouch for it — that is circular, and it is how a search result
    # gets to certify itself as the thing it claimed to be.
    sc, why = match.score(page["name"], identity, url=offer.get("url", ""),
                          brand=page.get("brand") or "", extra=page.get("sku") or "")
    out["match"], out["match_why"] = sc, why
    if sc < ACCEPT_MIN:
        out["status"] = "wrong-item"
        return out

    listed = offer.get("price")
    if page["price"] is None:
        # The page opened and said nothing readable — a JS-rendered price the
        # readers cannot see. The archived copy of the same url may carry a
        # server-rendered one; try it before settling for "unpriced".
        if CFG.get("read", {}).get("archive", True):
            try:
                from agent import archive
                for crawl in archive.crawls(2):
                    rows, _ = archive._cdx(crawl, offer["url"].split("?")[0], None, limit=3)
                    rows = [r for r in rows or [] if str(r.get("status")) == "200"]
                    if rows:
                        rec = {"url": rows[0]["url"], "timestamp": rows[0]["timestamp"],
                               "filename": rows[0]["filename"], "offset": int(rows[0]["offset"]),
                               "length": int(rows[0]["length"])}
                        ap = archive.read(rec)
                        if ap.get("price") is not None:
                            out.update(status="archived", price=ap["price"], read_by=ap["read_by"],
                                       currency=ap["currency"] or offer.get("currency"),
                                       crawled=ap["crawled"], from_archive=True,
                                       why=f"live page shows no readable price; archived copy of {ap['crawled']}")
                            return out
                        break
            except Exception:
                pass
        out["status"] = "unpriced"
        return out
    page_ccy = page["currency"] or offer.get("currency")
    same_ccy = (offer.get("currency") or "").upper() == (page_ccy or "").upper()
    if same_ccy:
        agrees = bool(listed) and abs(page["price"] - listed) / max(listed, 1) < TOL
    else:                                  # cross-currency: compare in USD, wider band
        a, b = fx.to_usd(listed, offer.get("currency")), fx.to_usd(page["price"], page_ccy)
        agrees = bool(a and b) and abs(b - a) / max(a, 1) < FX_TOL
        out["listed_usd"] = a
    if page["read_by"] == "text" and not agrees and listed:
        out["status"] = "price-unconfirmed"       # a lone text scrape that contradicts the listing
        out["page_price_seen"] = page["price"]
        return out
    if page["read_by"] == "text" and not listed:
        # An index lead carries no listed price, so there is nothing for the
        # text scrape to disagree with. It is the only number we have; keep it,
        # say where it came from, and let landed confidence stay "medium".
        out["price_note"] = "price read from page text — no structured price on this page"

    out["price"] = page["price"]                  # the page is the price
    out["currency"] = page["currency"] or offer.get("currency")
    out["in_stock"] = page["in_stock"]
    if page["in_stock"] is False:
        out["status"] = "sold-out"
    elif listed and not agrees:
        out["status"] = "price-changed"
    else:
        out["status"] = "verified"
    return out


def shortlist(offers, identity, limit=PER_STORE):
    """Cheap gate before any page is opened: score what the results page said
    and keep only the leads worth the walk. Returns (leads, off_target_count).

    A lead with no price is normal and expected — a harvested product link
    carries only a url and some anchor text, because price and name are what
    the page visit is FOR. Requiring a price here is what made link harvesting
    silently produce nothing."""
    leads, off, seen = [], 0, set()
    for o in offers:
        if o.get("manual"):
            continue
        if o.get("match") is None:      # browser.harvest already scored its own
            o["match"], o["match_why"] = match.score(
                o.get("title"), identity, url=o.get("url", ""), brand=o.get("brand", ""))
        if o["match"] < CANDIDATE_MIN:
            o["status"] = "off-target"
            off += 1
            continue
        key = o.get("url", "").split("?")[0].rstrip("/")   # ?_pos=&_sid= is one page
        if key in seen:
            continue
        seen.add(key)
        leads.append(o)
    leads.sort(key=lambda o: -o["match"])
    off += max(0, len(leads) - limit)
    return leads[:limit], off


def confirm_store(store_offers, identity, should_stop=None, limit=PER_STORE):
    """Search rows from one store -> offers that survived the page visit.
    Shared by the CLI and the server so both surfaces agree on the truth.
    Returns (kept, off_target) — kept includes manual links untouched."""
    kept = [o for o in store_offers if o.get("manual")]
    for o in kept:
        o["status"] = "manual"
    # A price Google read off a page we are forbidden to open is a real number
    # with a stated source. It never gets visited (the visit is what 403s) and
    # it never gets called verified — its own status says where it came from.
    for o in store_offers:
        if o.get("from_google") and not o.get("manual"):
            o["status"] = "google-index"
            o["checked"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
            kept.append(o)
        elif o.get("from_archive") and not o.get("manual"):
            # read from the web archive: a real number with a stated date,
            # weeks old, never visited live (the visit is what 403s)
            o["status"] = "archived"
            o["checked"] = o.get("crawled")
            kept.append(o)
    leads, off = shortlist([o for o in store_offers
                            if not o.get("from_google") and not o.get("from_archive")],
                           identity, limit)
    confirmed = []
    for o in leads:
        if should_stop and should_stop():
            break
        o.update(inspect_offer(o, identity))
        if o["status"] in ("wrong-item", "dead"):
            off += 1
            continue
        confirmed.append(o)
    kept += collapse(confirmed)
    return kept, off


def collapse(offers):
    """One row per product per shop, cheapest variant winning.

    A results page links the same shoe under several urls — colour swatches,
    tracking parameters, a grid tile and its title. Left alone they flood the
    list with the same thing at six prices, which reads as a broken price list
    even though every row was individually verified."""
    # a trailing "- 11" or "- White / 7" is the SIZE this page happens to be
    # showing, not a different product; without this one shoe arrives as six
    SIZE_TAIL = re.compile(r"(\s*[-\u2013]\s*[^-\u2013]*\b\d{1,2}(?:\.\d)?\b\s*)+$")
    best = {}
    for o in offers:
        raw = SIZE_TAIL.sub("", (o.get("page_name") or o.get("title") or "")).strip()
        name = re.sub(r"[^a-z0-9]+", "", raw.lower())
        # a declared style code identifies the colourway better than any name
        key = (o.get("store"), name or o.get("url"))
        cur = best.get(key)
        if cur is None:
            best[key] = o
            continue
        # keep the cheaper one, but never let a sold-out row hide a buyable one
        cur_gone = cur.get("status") in ("sold-out", "unpriced")
        new_gone = o.get("status") in ("sold-out", "unpriced")
        if cur_gone and not new_gone:
            best[key] = o
        elif new_gone == cur_gone and (o.get("price") or 9e9) < (cur.get("price") or 9e9):
            best[key] = o
    return list(best.values())


if __name__ == "__main__":
    import sys
    from agent.identify import identify
    url, q = sys.argv[1], " ".join(sys.argv[2:])
    print(json.dumps(inspect_offer({"url": url, "store": "?"}, identify(q)),
                     ensure_ascii=False, indent=1)[:2000])
