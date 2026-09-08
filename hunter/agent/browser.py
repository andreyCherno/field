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

_ALL = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "config.json"), encoding="utf-8"))
_CFG = _ALL.get("browser", {})
LOCALE = _CFG.get("locale", "en-US")
TZ = _CFG.get("timezone", "Asia/Jerusalem")
# How the browser runs — see config.browser.mode:
#   headless   invisible Chromium, fresh context per page (default; fastest)
#   headed     a visible Chrome window with a PERSISTENT profile: cookies,
#              consent choices you made yourself, logins — it browses the way
#              you do, in front of you. Slower; some shops that refuse a
#              headless robot serve a real window normally.
#   attach     drive a Chrome you already started with
#              --remote-debugging-port=9222 (your own profile, your session)
MODE = _CFG.get("mode", "headless")
PROFILE_DIR = os.path.expanduser(_CFG.get("profile_dir", "~/.field-hunter-chrome"))
CDP_URL = _CFG.get("cdp_url", "http://localhost:9222")
CAND_FLOOR = _ALL.get("match", {}).get("candidate_min", 0.45)
# Harvesting reads 100+ anchors per results page, so it uses the *publish* bar,
# not the candidate bar. The candidate bar exists to justify opening a page we
# have structured data for; here it just buys page loads on lamps that happen
# to have a 14 in the name.
HARVEST_FLOOR = _ALL.get("match", {}).get("accept_min", 0.6)

# Which sizes this product page is actually offering, and which of them you
# can buy. Shops mark a dead size a dozen ways — disabled, aria-disabled, a
# "sold out" class, a line-through — so all of them are checked and anything
# ambiguous is reported as unknown rather than as available.
SIZES_JS = """() => {
  const out = [], seen = new Set();
  const push = (label, avail) => {
    const t = (label || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 14 || seen.has(t)) return;
    if (/^(select|choose|size|guide|chart)$/i.test(t)) return;
    seen.add(t);
    out.push({size: t, available: avail});
  };
  const dead = e => {
    if (e.disabled === true) return true;
    if (e.getAttribute && e.getAttribute('aria-disabled') === 'true') return true;
    const blob = ((e.className && e.className.toString()) || '') + ' ' +
                 ((e.getAttribute && (e.getAttribute('data-status') || '')) || '') + ' ' +
                 ((e.getAttribute && (e.getAttribute('title') || '')) || '');
    if (/disabled|sold[-_ ]?out|unavailable|out[-_ ]?of[-_ ]?stock|not[-_ ]?available/i.test(blob)) return true;
    try { if (getComputedStyle(e).textDecorationLine.indexOf('line-through') >= 0) return true; } catch (err) {}
    return false;
  };
  for (const o of document.querySelectorAll('select[name*="size" i] option, select[id*="size" i] option'))
    push(o.textContent || o.value, !dead(o));
  const scopes = document.querySelectorAll(
    '[class*="size" i], [id*="size" i], [data-option-name*="size" i], fieldset');
  for (const sc of scopes) {
    for (const e of sc.querySelectorAll('button, label, li, a, [data-size], [data-value]')) {
      if (e.querySelector('button, label, li')) continue;
      push(e.getAttribute('data-size') || e.getAttribute('data-value') || e.textContent, !dead(e));
    }
  }
  return out.slice(0, 60);
}"""

# A results page is a list of doors, not a list of facts. We only need the
# doors: which links on this page are plausibly the item. Price and name are
# read later, from behind each door, by inspect.py.
LINKS_JS = """() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    let h = a.href;
    if (!h || h.indexOf('http') !== 0) continue;
    h = h.split('#')[0];
    if (seen.has(h)) continue;
    seen.add(h);
    const img = a.querySelector('img');
    const r = a.getBoundingClientRect();
    out.push({href: h,
              text: (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 180),
              img: img ? (img.currentSrc || img.src || '') : '',
              area: Math.round(r.width * r.height)});
    if (out.length > 1500) break;
  }
  return out;
}"""

# paths that mean "this link is a product", across the shop software in the
# registry: shopify, magento, woocommerce, salesforce, custom
PRODUCT_PATH = re.compile(
    r"/(products?|prd|pd|dp|item|itm|style|artikel|art|shop|buy|p)(/|-|_|\.|\?|=|$)", re.I)
# ...and paths that never are. `product-category` and `product-tag` matter:
# WordPress names its listing pages that, and PRODUCT_PATH happily matches the
# "product" prefix, so a North Face shop was offering "men's shirts" as a
# product for a Nike query.
NOT_PRODUCT = re.compile(
    r"/(cart|account|login|register|checkout|blog|blogs|news|pages?|help|faq|about|"
    r"contact|policy|policies|terms|privacy|shipping|returns|gift|wishlist|compare|"
    r"search|collections?|category|categories|product-category|product-tag|"
    r"product_cat|brands?|designers?|magazine|journal|lookbook|stories|guides?|"
    r"size-guide|c|sitemap|store-locator)(/|$)", re.I)
# A path segment that really does name one product. `p` and `pd` are included
# because plenty of shops use /collections/all/p/<slug> or /categories/shoes/pd/
# — dropping them made every such shop yield nothing. The surrounding slashes
# are what keep this from matching "product-category".
IS_PRODUCT = re.compile(r"/(products?|prd|pd|p|dp|item|itm|style|artikel)/", re.I)

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


_persistent = None   # the shared context in attach mode
_headed = None       # the visible persistent Chrome, opened the first time it is asked for
_pw = None
FORCE_HEADED = False   # a hunt can ask for the visible browser for every shop


def _serve():
    """The one thread that ever touches Playwright."""
    global _persistent, _pw
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    _pw = pw
    exe = os.environ.get("HUNTER_CHROMIUM")   # pre-installed chromium override
    br = None
    if MODE == "attach":
        br = pw.chromium.connect_over_cdp(CDP_URL)
        _persistent = br.contexts[0] if br.contexts else br.new_context()
    else:
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
        for c in (_headed, _persistent):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        try:
            br.close(); pw.stop()
        except Exception:
            pass


def _headed_ctx():
    """The visible Chrome with the persistent profile — a real window, your
    cookies, the consent choices you made yourself. Opened once, kept open."""
    global _headed
    if _headed is None:
        kw = dict(headless=False, locale=LOCALE, timezone_id=TZ,
                  viewport={"width": 1280, "height": 900})
        try:
            _headed = _pw.chromium.launch_persistent_context(PROFILE_DIR, channel="chrome", **kw)
        except Exception:
            _headed = _pw.chromium.launch_persistent_context(PROFILE_DIR, **kw)
    return _headed


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


class _Shared:
    """In headed/attach mode every page lives in the one persistent context, so
    'closing the context' after a page must only close the page."""
    def __init__(self, ctx): self.ctx = ctx; self.pages = []
    def new_page(self):
        p = self.ctx.new_page(); self.pages.append(p); return p
    @property
    def request(self): return self.ctx.request
    def close(self):
        for p in self.pages:
            try: p.close()
            except Exception: pass


def _context(br, headed=False):
    if headed or FORCE_HEADED or MODE == "headed":
        return _Shared(_headed_ctx())
    if _persistent is not None:
        return _Shared(_persistent)
    # locale decides WHICH market's price the shop serves — see config.browser
    return br.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        locale=LOCALE, timezone_id=TZ)


def visit(url, wait_ms=2500, timeout_ms=20000, ready_js=None, collect=None, headed=False):
    """Open one page and report what we landed on: {html, text, url, status}.

    The *final* url matters as much as the html. A delisted product quietly
    302s to the shop's homepage, which still returns 200 and still has a title
    — read only the html and you conclude "wrong item" about a page that
    simply no longer exists.

    `ready_js` is a JS predicate: when it returns true the page is done and we
    stop waiting — `wait_ms` becomes the ceiling instead of a flat sleep."""
    return _submit(_do_visit, url, wait_ms, timeout_ms, ready_js, collect, headed)


def _do_visit(br, url, wait_ms, timeout_ms, ready_js, collect=None, headed=False):
    ctx = _context(br, headed)
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
        out = {"html": page.content(),
               "text": page.evaluate("document.body ? document.body.innerText : ''"),
               "url": page.url, "status": resp.status if resp else None}
        if collect:
            try:
                out["collected"] = page.evaluate(collect)
            except Exception:
                out["collected"] = []
        return out
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
    from agent import fx
    price = fx.parse_amount(offer.get("price") or offer.get("lowPrice"))
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


# Where is this shop's search box, and what does it call its query parameter?
# 80 of the playbooks were seeded with a guessed /search?q={q}; the shop's own
# homepage states the real answer in its <form>, so read it instead of guessing.
SEARCH_FORM_JS = """() => {
  const out = [];
  for (const f of document.querySelectorAll('form')) {
    const act = f.getAttribute('action') || '';
    const bag = (act + ' ' + (f.id || '') + ' ' + (f.className || '') + ' ' +
                 (f.getAttribute('role') || '') + ' ' + (f.name || ''));
    const inputs = [...f.querySelectorAll('input')].filter(i => {
      const t = (i.type || '').toLowerCase();
      return t === 'search' || t === 'text' || t === '';
    });
    const searchy = i => /^(q|s|query|search|keyword|keywords|text|term)$/i.test(i.name || '') ||
        /search|suche|recherche|busca|ricerca|zoek|\u05d7\u05d9\u05e4\u05d5\u05e9/i
          .test((i.name || '') + ' ' + (i.id || '') + ' ' + (i.placeholder || ''));
    const named = inputs.find(searchy);
    if (!/search|suche|recherche|busca|ricerca|zoek/i.test(bag) && !named) continue;
    let href;
    try { href = new URL(act || location.pathname, location.href).href; } catch (e) { continue; }
    out.push({action: href,
              method: (f.getAttribute('method') || 'get').toLowerCase(),
              param: (named && named.name) || (inputs[0] || {}).name || 'q',
              hidden: [...f.querySelectorAll('input[type=hidden]')]
                        .filter(i => i.name && i.value).map(i => [i.name, i.value]).slice(0, 4)});
  }
  return out;
}"""


# Where a shop hides its search box. Ordered most-specific first.
SEARCH_INPUTS = [
    'input[type="search"]', 'input[name="q"]', 'input[name="s"]',
    'input[name="query"]', 'input[name="keyword"]', 'input[name="text"]',
    'input[name*="search" i]', 'input[id*="search" i]',
    'input[placeholder*="search" i]', 'input[aria-label*="search" i]',
    'input[placeholder*="\u05d7\u05d9\u05e4\u05d5\u05e9"]',
    'input[placeholder*="suche" i]', 'input[placeholder*="ricerca" i]',
    'input[placeholder*="recherche" i]', 'input[placeholder*="buscar" i]',
    # class-named inputs: modern shops ship <input class="search-input_1ENs">
    # with no name, no id and no placeholder at all
    'input[class*="search" i]', 'input[data-testid*="search" i]',
    '[role="searchbox"]', '[role="combobox"] input',
]
# ...and what you press to make it appear. Often a bare <div>, not a button.
SEARCH_TOGGLES = [
    'button[aria-label*="search" i]', 'a[aria-label*="search" i]',
    '[data-testid*="search" i]', 'button[class*="search" i]',
    'a[class*="search" i]', '[id*="search-toggle" i]', '[class*="search-icon" i]',
    '[class*="icon-search" i]', 'div[class*="search" i]', 'span[class*="search" i]',
    '[class*="\u05d7\u05d9\u05e4\u05d5\u05e9"]',
]
# A visible text box that is plainly something else. Deliberately does NOT
# include a bare "name": "Search by product name" is a real and common search
# placeholder, and matching it made the last-resort finder reject the very box
# it exists to find.
NOT_SEARCH = re.compile(
    r"\b(e-?mail|newsletter|subscribe|signup|sign-up|zip|postcode|postal|phone|tel|"
    r"coupon|promo|voucher|discount|qty|quantity|address|first-?name|last-?name|"
    r"full-?name|password|username|comment|message|review)\b", re.I)
IS_SEARCH_WORD = re.compile(
    r"search|\u05d7\u05d9\u05e4\u05d5\u05e9|suche|recherche|ricerca|buscar|zoek", re.I)


def _blob(el):
    try:
        return " ".join(filter(None, (el.get_attribute(a) for a in
                                      ("name", "id", "placeholder", "aria-label", "class"))))
    except Exception:
        return ""


def _rejected_as_non_search(el):
    """A box that names itself a search box is one, whatever else it says."""
    b = _blob(el)
    if IS_SEARCH_WORD.search(b or ""):
        return False
    return bool(NOT_SEARCH.search(b or ""))


# A consent dialog has its own search box ("Cookie list search" on OneTrust
# sites). Typing a product name into a privacy preferences panel is not a
# search — it is interacting with a consent UI, which this never does.
CONSENT_HINT = re.compile(r"cookie|consent|onetrust|gdpr|privacy|\bot-", re.I)


def _press(el):
    """Click an element even when a consent overlay is floating above it.

    A normal click is refused by Playwright because the banner would receive
    it — and clicking a consent banner is exactly what this must never do.
    dispatch_event delivers the click to the search button itself, so the
    banner is neither accepted, dismissed, nor touched; it is simply not
    answered, and the site's own no-consent default stands."""
    try:
        el.click(timeout=2500)
        return True
    except Exception:
        pass
    try:
        el.dispatch_event("click")
        return True
    except Exception:
        return False


def _type_into(page, el, text):
    """Put text in the box and submit, overlay or no overlay."""
    try:
        el.click(timeout=2000)
        el.fill(text)
    except Exception:
        try:
            el.evaluate("e => e.focus()")
        except Exception:
            return False
        page.keyboard.type(text, delay=15)
    page.keyboard.press("Enter")
    return True


def _is_consent(el):
    try:
        blob = " ".join(filter(None, (el.get_attribute(a) for a in
                                      ("id", "name", "aria-label", "class"))))
    except Exception:
        return False
    return bool(CONSENT_HINT.search(blob or ""))


def _same_element(a, b):
    try:
        return bool(a.evaluate("(e, o) => e === o", b))
    except Exception:
        return False


def _focused_input(page):
    """The box the shop itself focused. Clicking a search icon almost always
    focuses the search field, which identifies it more reliably than any
    selector — it needs no name, id, placeholder or class to be found."""
    try:
        handle = page.evaluate_handle("() => document.activeElement")
    except Exception:
        return None
    try:
        el = handle.as_element()
        if el and el.evaluate("e => e.tagName") in ("INPUT", "TEXTAREA") \
                and el.is_visible() and el.is_enabled() and not _is_consent(el) \
                and not _rejected_as_non_search(el):
            return el
    except Exception:
        pass
    finally:
        # a long-lived single browser process: these accumulate otherwise
        try:
            handle.dispose()
        except Exception:
            pass
    return None


def _any_text_box(page):
    """Last resort: a visible text box that is not obviously a newsletter,
    address or quantity field."""
    try:
        # filter first, then stop — a page with a dozen hidden filter inputs
        # would otherwise use up the cap before reaching the visible box
        found = 0
        for el in page.query_selector_all(
                'input[type="search"], input[type="text"], input:not([type])'):
            if not (el.is_visible() and el.is_enabled()) or _is_consent(el):
                continue
            found += 1
            if _rejected_as_non_search(el):
                if found >= 12:
                    break
                continue
            return el
    except Exception:
        pass
    return None


def _first_visible(page, selectors):
    for sel in selectors:
        try:
            for el in page.query_selector_all(sel)[:6]:
                if el.is_visible() and el.is_enabled() and not _is_consent(el):
                    return el
        except Exception:
            continue
    return None


def type_search(domain, query, wait_ms=3000, timeout_ms=20000, headed=False):
    """Search a shop the way a person does: open it, find the search box, type,
    press Enter, read whatever comes back.

    This exists because a third of the registry cannot be searched by URL —
    the playbook template was a guess that 404s, or search is a JavaScript
    widget with no form and no guessable url at all. Typing works on both.

    Cookie and consent banners are deliberately never clicked. If one blocks
    the box, this fails and the shop is reported unreadable, which is the
    honest outcome — consent is not something to give on someone's behalf.
    """
    return _submit(_do_type_search, domain, query, wait_ms, timeout_ms, headed)


def _do_type_search(br, domain, query, wait_ms, timeout_ms, headed=False):
    ctx = _context(br, headed)
    try:
        page = ctx.new_page()
        landed = None
        for host in (domain.removeprefix("www."), "www." + domain.removeprefix("www.")):
            try:
                resp = page.goto(f"https://{host}/", wait_until="domcontentloaded",
                                 timeout=timeout_ms)
                if resp and resp.status < 400:
                    landed = resp
                    break
            except Exception:
                continue
        if landed is None:
            raise RuntimeError("homepage unreachable")
        page.wait_for_timeout(1200)

        box = _first_visible(page, SEARCH_INPUTS)
        if box is None:
            # The box is behind a magnifier icon and is rendered only after the
            # click, so look again *after waiting* — and give it two goes,
            # because the first click often just opens a drawer.
            # Try each toggle selector in turn. A failed press must not end the
            # hunt: the broad selectors often match an unclickable wrapper
            # first, and the real icon is behind the next one.
            pressed = []
            for sel in SEARCH_TOGGLES:
                if box is not None or len(pressed) >= 3:
                    break
                cand = _first_visible(page, [sel])
                if cand is None:
                    continue
                if any(_same_element(cand, p) for p in pressed):
                    continue          # two selectors, one node — do not re-press
                pressed.append(cand)
                if not _press(cand):
                    continue
                page.wait_for_timeout(1500)
                box = _focused_input(page) or _first_visible(page, SEARCH_INPUTS)
            if box is None:
                box = _any_text_box(page)
        if box is None:
            raise RuntimeError("no search box on the page")

        if not _type_into(page, box, query):
            raise RuntimeError("search box would not accept text")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        page.wait_for_timeout(wait_ms)
        return {"html": page.content(),
                "text": page.evaluate("document.body ? document.body.innerText : ''"),
                "url": page.url, "status": 200,
                "collected": page.evaluate(LINKS_JS)}
    finally:
        ctx.close()


def find_search_url(domain, timeout_ms=20000):
    """Open the shop's front page and read its search form. Returns a
    search_url template ('https://host/search?q={q}') or None.

    This is the difference between guessing at a shop and looking at it."""
    from urllib.parse import urlencode, urlparse
    for host in (domain, "www." + domain.removeprefix("www.")):
        try:
            v = visit(f"https://{host}/", wait_ms=2500, timeout_ms=timeout_ms,
                      collect=SEARCH_FORM_JS)
        except Exception:
            continue
        if v.get("status") and v["status"] >= 400:
            continue
        for f in v.get("collected") or []:
            if f.get("method") != "get" or not f.get("param"):
                continue
            u = urlparse(f["action"])
            if not _same_site(u.netloc, domain):
                continue
            extra = [(k, val) for k, val in (f.get("hidden") or [])
                     if k.lower() not in ("q", "s", "query", "search")]
            qs = urlencode(extra) if extra else ""
            base = f'{u.scheme}://{u.netloc}{u.path or "/"}'
            return f'{base}?{f["param"]}={{q}}' + (f"&{qs}" if qs else "")
    return None


def _same_site(netloc, domain):
    a, b = netloc.lower().removeprefix("www."), domain.lower().removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def harvest(links, domain, identity, limit=6, floor=HARVEST_FLOOR):
    """Rendered results page -> the product links that are plausibly the item.

    No per-store selectors, and none needed: the model-code rule does the
    filtering. A shop that answers "gel kayano 14" with New Balance 992s
    yields nothing here, which is the correct answer and the whole complaint
    this fixes. Price and name are deliberately NOT taken from this page —
    inspect.py reads them from behind each link."""
    from urllib.parse import urlparse
    from agent import match
    out = {}
    for l in links or []:
        href = l.get("href") or ""
        u = urlparse(href)
        if not _same_site(u.netloc, domain):
            continue
        if NOT_PRODUCT.search(u.path) and not IS_PRODUCT.search(u.path):
            continue
        if u.path.strip("/") == "":
            continue          # the results page itself, or the shop's front door
        # The query string is never product identity — and on a results page it
        # literally contains the words we searched for, so scoring it would let
        # the search url certify itself as a perfect match.
        slug = re.sub(r"[^A-Za-z0-9]+", " ", u.path)
        text = (l.get("text") or "").strip()
        sc, why = match.score(text or slug, identity, url=href, extra=slug)
        if sc < floor:
            continue
        prev = out.get(href)
        if prev and prev["match"] >= sc:
            continue
        out[href] = {"store": domain, "title": text or slug.strip(), "url": href,
                     "price": None, "img": l.get("img") or "",
                     "match": sc, "match_why": why,
                     "looks_like_product": bool(PRODUCT_PATH.search(u.path)),
                     "area": l.get("area") or 0}
    leads = sorted(out.values(),
                   key=lambda o: (-o["match"], not o["looks_like_product"], -o["area"]))
    return leads[:limit]


def search(pb, url, identity, deep=False, limit=6, floor=None, headed=False):
    """Search one store the way a person does: open the results page in a real
    browser, then take the links that actually match what we are hunting.

    Raises if the page could not be opened at all — that is a broken playbook
    or a blocked shop, and it must be reported as such rather than dressed up
    as a result."""
    v = visit(url, wait_ms=3000, collect=LINKS_JS, headed=headed or bool(pb.get("needs_headed")))
    domain = pb["domain"]
    # 1. Structured data, when the shop emits it on a results page. This uses
    #    the *candidate* bar, not the publish bar: a results-page title is
    #    often truncated ("Nike Air Max 90 Men's...") and inspect.py re-scores
    #    the authoritative page name at the publish bar anyway. Judging it
    #    twice at the higher bar throws away leads that would have confirmed.
    offers = offers_from_jsonld(v["html"], domain)
    if offers:
        from agent import match
        kept = []
        for o in offers:
            o["match"], o["match_why"] = match.score(
                o.get("title"), identity, url=o.get("url", ""), brand=o.get("brand", ""))
            if o["match"] >= (floor if floor is not None else CAND_FLOOR):
                kept.append(o)
        offers = sorted(kept, key=lambda o: -o["match"])[:limit]
    # 2. Otherwise walk the links. This uses the publish bar: there are 100+
    #    anchors per page and no structured data behind any of them, so a
    #    weak match is just a page load spent on a lamp.
    if not offers:
        offers = harvest(v.get("collected"), domain, identity, limit,
                         floor if floor is not None else HARVEST_FLOOR)
    # 3. last resort, and only when asked: pay the model to read the page
    if not offers and deep:
        offers = offers_from_llm(v["text"], domain,
                                 identity.get("query", ""), pb.get("price_selector", ""))
    return offers, v
