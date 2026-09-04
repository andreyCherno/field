#!/usr/bin/env python3
"""A real browser for the hunter — stores get opened exactly like a human
would: Playwright drives headless Chromium to the page, waits for it to
render, and the DOM is read from there.

Two jobs, one browser:
  * `search()`  — a store's search-results page, to collect *leads*
  * `render()`  — any page, used by inspect.py to read a product page itself

Extraction is JSON-LD first (free, and most shops embed it for Google), the
parse model second (deep mode only — costs cents).

One-time setup on the machine:  pip install playwright && playwright install chromium
Without it, everything degrades back to manual links — nothing breaks.
"""
import json, os, queue, re, threading

# Playwright's sync API is bound to the thread that created it, and
# ThreadingHTTPServer hands every request a fresh thread. Sharing one browser
# across them raises "cannot switch to a different thread" — which surfaces as
# every product page reading as dead, on every hunt after the first. So the
# browser lives in one dedicated worker thread and all callers post jobs to it.
_jobs = queue.Queue()
_worker = None
_boot = threading.Lock()

_CFG = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "config.json"), encoding="utf-8")).get("browser", {})
LOCALE = _CFG.get("locale", "en-US")
TZ = _CFG.get("timezone", "Asia/Jerusalem")

# A product page is "ready" once schema.org data or a price meta tag exists.
# Waiting for *that* instead of a flat sleep is what keeps page-by-page
# inspection affordable: ready pages cost ~0.3s instead of 2.5s.
PRODUCT_READY = """() => {
  if (document.querySelector('meta[property$="price:amount"]')) return true;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]'))
    if ((s.textContent || '').includes('"Product"')) return true;
  return false;
}"""


def available():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _serve():
    """The one thread that ever touches Playwright."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    exe = os.environ.get("HUNTER_CHROMIUM")   # pre-installed chromium override
    br = pw.chromium.launch(headless=True, executable_path=exe if exe else None)
    try:
        while True:
            job = _jobs.get()
            if job is None:
                return
            fn, args, out = job
            try:
                out.put(("ok", fn(br, *args)))
            except Exception as e:                      # noqa: BLE001
                out.put(("err", e))
    finally:
        try:
            br.close(); pw.stop()
        except Exception:
            pass


def _submit(fn, *args):
    """Run `fn(browser, *args)` on the browser thread and return its result."""
    global _worker
    with _boot:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_serve, daemon=True, name="hunter-browser")
            _worker.start()
    out = queue.Queue(1)
    _jobs.put((fn, args, out))
    kind, val = out.get()
    if kind == "err":
        raise val
    return val


def close():
    global _worker
    if _worker and _worker.is_alive():
        _jobs.put(None)
        _worker.join(timeout=10)
    _worker = None


def _context(br):
    # locale decides WHICH market's price the shop serves — see config.browser
    return br.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        locale=LOCALE, timezone_id=TZ)


def visit(url, wait_ms=2500, timeout_ms=20000, ready_js=None):
    """Open one page and report what we landed on: {html, text, url, status}.

    The *final* url matters as much as the html. A delisted product quietly
    302s to the shop's homepage, which still returns 200 and still has a title
    — read only the html and you conclude "wrong item" about a page that
    simply no longer exists.

    `ready_js` is a JS predicate: when it returns true the page is done and we
    stop waiting — `wait_ms` becomes the ceiling instead of a flat sleep."""
    return _submit(_do_visit, url, wait_ms, timeout_ms, ready_js)


def _do_visit(br, url, wait_ms, timeout_ms, ready_js):
    ctx = _context(br)
    try:
        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if ready_js:
            try:
                page.wait_for_function(ready_js, timeout=wait_ms)
            except Exception:
                pass          # never ready — read whatever did render
            page.wait_for_timeout(250)
        else:
            page.wait_for_timeout(wait_ms)   # let client-side results render
        return {"html": page.content(),
                "text": page.evaluate("document.body ? document.body.innerText : ''"),
                "url": page.url, "status": resp.status if resp else None}
    finally:
        ctx.close()


def render(url, wait_ms=2500, timeout_ms=20000, ready_js=None):
    """visit(), for callers that only want (rendered_html, visible_text)."""
    v = visit(url, wait_ms, timeout_ms, ready_js)
    return v["html"], v["text"]


def jsonld_products(html):
    """Every schema.org Product on the page, **in document order** — the first
    one is the page's own product, later ones are the recommendation carousel.
    Order is the whole reason this exists: read the wrong node and you publish
    the price of the 'customers also bought' shoe."""
    found = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        queue = [data]
        while queue:                       # breadth-first keeps document order
            node = queue.pop(0)
            if isinstance(node, list):
                queue = list(node) + queue
                continue
            if not isinstance(node, dict):
                continue
            t = node.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                found.append(node)
            queue.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return found


def read_product(node):
    """One schema.org Product node -> the fields the hunter cares about.
    Price None and in_stock None both mean *unknown*, never 'no'."""
    offer = node.get("offers") or {}
    if isinstance(offer, list):
        offer = next((o for o in offer if isinstance(o, dict)), {})
    price = offer.get("price") or offer.get("lowPrice")
    try:
        price = float(str(price).replace(",", ""))
    except (TypeError, ValueError):
        price = None
    avail = str(offer.get("availability") or "").lower()
    brand = node.get("brand")
    img = node.get("image")
    return {
        "name": (node.get("name") or "").strip(),
        "brand": (brand.get("name", "") if isinstance(brand, dict) else str(brand or "")).strip(),
        "price": price,
        "currency": (offer.get("priceCurrency") or "").upper() or None,
        "in_stock": (True if "instock" in avail or "limitedavailability" in avail
                     else False if ("outofstock" in avail or "soldout" in avail
                                    or "discontinued" in avail) else None),
        "url": node.get("url") or offer.get("url") or "",
        "img": (img[0] if isinstance(img, list) and img else str(img or "")) if img else "",
        "sku": str(node.get("sku") or node.get("mpn") or "") or None,
    }


def offers_from_jsonld(html, domain):
    """Search-results extraction: every Product block the results page embeds."""
    offers = []
    for node in jsonld_products(html):
        p = read_product(node)
        if p["price"] is None or not p["url"]:
            continue
        p["store"] = domain
        p["title"] = p.pop("name")
        if p["url"].startswith("/"):
            p["url"] = f'https://{domain}{p["url"]}'
        offers.append(p)
    return offers


def offers_from_llm(text, domain, query, hint=""):
    from agent import llm
    return [dict(h, store=domain) for h in llm.complete_json("parse",
        "Extract product offers from this rendered store search-results page."
        ' JSON only: [{"title": str, "brand": str, "price": number,'
        ' "currency": str, "url": str}]. Only real product results for the'
        " query; empty array if none." + (f" Hint: {hint}" if hint else ""),
        f"Query: {query}\nStore: {domain}\nPage text:\n{text[:24000]}", max_tokens=2500)
        if h.get("price")]


def search(pb, url, query, deep=False):
    """Browser search of one store. Returns leads ([] on any failure)."""
    html, text = render(url)
    offers = offers_from_jsonld(html, pb["domain"])
    if not offers and deep:
        offers = offers_from_llm(text, pb["domain"], query, pb.get("price_selector", ""))
    return offers
