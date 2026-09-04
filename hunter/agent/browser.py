#!/usr/bin/env python3
"""A real browser for the hunter — the stores that used to say "manual" get
opened exactly like a human would: Playwright drives headless Chromium to the
store's search page, waits for it to render, and offers are extracted from
the page. Extraction order:
  1. JSON-LD Product blocks in the rendered DOM (free, most stores have them)
  2. the parse model over the rendered text (deep mode only — costs cents)

One-time setup on the machine:  pip install playwright && playwright install chromium
Without it, everything degrades back to manual links — nothing breaks.
"""
import json, re

_pw = _browser = None

def available():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False

def _page():
    global _pw, _browser
    from playwright.sync_api import sync_playwright
    if _browser is None:
        import os
        _pw = sync_playwright().start()
        exe = os.environ.get("HUNTER_CHROMIUM")   # pre-installed chromium override
        _browser = _pw.chromium.launch(headless=True,
                                       executable_path=exe if exe else None)
    ctx = _browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        locale="en-US")
    return ctx, ctx.new_page()

def close():
    global _pw, _browser
    if _browser:
        _browser.close(); _pw.stop()
        _pw = _browser = None

def render(url, wait_ms=2500, timeout_ms=20000):
    """Return (rendered_html, visible_text) after the page settles."""
    ctx, page = _page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(wait_ms)          # let client-side results render
        html = page.content()
        text = page.evaluate("document.body ? document.body.innerText : ''")
        return html, text
    finally:
        ctx.close()

def offers_from_jsonld(html, domain):
    """Free extraction: schema.org Product blocks most shops embed for Google."""
    offers = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if node.get("@type") in ("Product", ["Product"]):
                offer = node.get("offers") or {}
                if isinstance(offer, list):
                    offer = offer[0] if offer else {}
                price = offer.get("price") or offer.get("lowPrice")
                try:
                    price = float(str(price).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                offers.append({
                    "store": domain,
                    "title": node.get("name", ""),
                    "brand": (node.get("brand") or {}).get("name", "")
                             if isinstance(node.get("brand"), dict) else str(node.get("brand") or ""),
                    "price": price,
                    "currency": offer.get("priceCurrency", ""),
                    "url": node.get("url") or offer.get("url") or "",
                    "img": (node.get("image") or [""])[0] if isinstance(node.get("image"), list)
                           else str(node.get("image") or ""),
                })
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    # relative urls -> absolute
    for o in offers:
        if o["url"].startswith("/"):
            o["url"] = f"https://{domain}{o['url']}"
    return [o for o in offers if o["url"]]

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
    """Browser search of one store. Returns offers ([] on any failure)."""
    html, text = render(url)
    offers = offers_from_jsonld(html, pb["domain"])
    if not offers and deep:
        offers = offers_from_llm(text, pb["domain"], query, pb.get("price_selector", ""))
    return offers
