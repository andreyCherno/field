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
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def read_page(url, timeout_ms=20000):
    """Open one product page and report what it actually says.
    Returns None when the page could not be opened at all."""
    try:
        v = browser.visit(url, wait_ms=4000, timeout_ms=timeout_ms,
                          ready_js=browser.PRODUCT_READY)
    except Exception as e:
        return {"error": type(e).__name__}
    html, text = v["html"], v["text"]

    # did we land where we were sent? a delisted product 302s to the homepage
    want, got = urlparse(url), urlparse(v["url"])
    tail = want.path.rstrip("/").rsplit("/", 1)[-1]
    moved = bool(tail) and tail not in got.path
    out = {"name": None, "brand": "", "price": None, "currency": None,
           "in_stock": None, "img": "", "sku": None, "read_by": None,
           "status_code": v["status"], "landed_on": v["url"] if moved else None,
           "text": text[:4000]}
    if v["status"] and v["status"] >= 400:
        return {"error": f'HTTP {v["status"]}'}

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

    page = read_page(offer["url"], timeout_ms)
    if page is None or page.get("error"):
        out["status"] = "dead"
        out["error"] = (page or {}).get("error", "unreachable")
        return out

    out["page_name"] = page["name"]
    out["read_by"] = page["read_by"]
    out["read_locale"] = browser.LOCALE   # which market's price this is
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
    if page["read_by"] == "text" and not agrees:
        out["status"] = "price-unconfirmed"       # never trust a lone text scrape
        out["page_price_seen"] = page["price"]
        return out

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
    """Cheap gate before any page is opened: score the search-result titles and
    keep only the ones worth the walk. Returns (leads, off_target_count)."""
    leads, off = [], 0
    for o in offers:
        if o.get("manual") or not o.get("price"):
            continue
        o["match"], o["match_why"] = match.score(
            o.get("title"), identity, url=o.get("url", ""), brand=o.get("brand", ""))
        if o["match"] < CANDIDATE_MIN:
            o["status"] = "off-target"
            off += 1
        else:
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
    leads, off = shortlist(store_offers, identity, limit)
    for o in leads:
        if should_stop and should_stop():
            break
        o.update(inspect_offer(o, identity))
        if o["status"] in ("wrong-item", "dead"):
            off += 1
            continue
        kept.append(o)
    return kept, off


if __name__ == "__main__":
    import sys
    from agent.identify import identify
    url, q = sys.argv[1], " ".join(sys.argv[2:])
    print(json.dumps(inspect_offer({"url": url, "store": "?"}, identify(q)),
                     ensure_ascii=False, indent=1)[:2000])
