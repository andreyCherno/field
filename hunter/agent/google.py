#!/usr/bin/env python3
"""Prices for shops that refuse to be read, via Google's own index.

Fifteen shops in this registry answer HTTP 403 to everything — their pages,
their robots.txt, all of it. Google has already visited those pages, and its
Custom Search JSON API returns the structured data it extracted from them:
`pagemap.offer[].price`, `pagemap.product[].price`, and the og:price meta
tags. So the price can come from Google's index without the blocked shop
being touched at all.

**Why this and not Google Shopping.** google.com/robots.txt says:

    Disallow: /search          Disallow: /shopping?
    Disallow: /shopping/search Disallow: /shopping/product/

Scraping Google Shopping is the same refusal this project honours at every
shop, and honouring it selectively would make the whole verified/uncertain
distinction worthless. The Custom Search JSON API is the route Google
publishes for exactly this, free for 100 queries a day.

STATUS (checked 7 Sep 2026): Google has CLOSED the Custom Search JSON API to
new customers; existing keys work until 1 Jan 2027. So this module only helps
someone who already holds a key. It stays because it is correct and harmless
when unconfigured — but it is no longer the answer for blocked shops.
agent/archive.py is: the public web archive, no key, no account.

If you do hold a key:  "google": {"api_key": "...", "cx": "...", "daily_cap": 100}
in hunter/config.json, or GOOGLE_API_KEY / GOOGLE_CX in the environment.
Without them this module reports itself unavailable and nothing else changes.
"""
import json, os, re, urllib.parse, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG_PATH = os.path.join(ROOT, "config.json")
SPEND = os.path.join(ROOT, "data", "google_calls.jsonl")
ENDPOINT = "https://www.googleapis.com/customsearch/v1"


def _cfg():
    try:
        g = json.load(open(CFG_PATH, encoding="utf-8")).get("google", {})
    except (OSError, ValueError):
        g = {}
    return {"api_key": os.environ.get("GOOGLE_API_KEY") or g.get("api_key"),
            "cx": os.environ.get("GOOGLE_CX") or g.get("cx"),
            "daily_cap": g.get("daily_cap", 100)}


def available():
    c = _cfg()
    return bool(c["api_key"] and c["cx"])


def calls_today():
    n = 0
    today = date.today().isoformat()
    try:
        with open(SPEND, encoding="utf-8") as f:
            for line in f:
                try:
                    n += 1 if json.loads(line).get("date") == today else 0
                except ValueError:
                    pass
    except OSError:
        pass
    return n


def _log(q):
    os.makedirs(os.path.dirname(SPEND), exist_ok=True)
    with open(SPEND, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date.today().isoformat(), "q": q}) + "\n")


def _price_from(item):
    """Whatever price Google extracted from that page, and its currency.

    Sources in order of trust, same discipline as reading a page ourselves:
    schema.org offer > product > og:price meta. Returns (amount, currency)
    with either half None when the page did not say."""
    from agent import fx
    pm = item.get("pagemap") or {}
    for key in ("offer", "product", "aggregateoffer"):
        for row in pm.get(key) or []:
            amt = fx.parse_amount(row.get("price") or row.get("lowprice"))
            if amt is not None:
                cur = (row.get("pricecurrency") or row.get("currency") or "").upper() or None
                return amt, cur
    for tags in pm.get("metatags") or []:
        for k in ("product:price:amount", "og:price:amount", "twitter:data1"):
            amt = fx.parse_amount(tags.get(k))
            if amt is not None:
                cur = (tags.get("product:price:currency")
                       or tags.get("og:price:currency") or "").upper() or None
                return amt, cur
    return None, None


def search(query, domain=None, limit=5, timeout=15):
    """Ask Google's index. Returns [] when unconfigured or over the daily cap —
    never raises, because a missing key must not break a hunt."""
    c = _cfg()
    if not (c["api_key"] and c["cx"]):
        return []
    if calls_today() >= c["daily_cap"]:
        return []
    q = f"site:{domain} {query}" if domain else query
    url = (f"{ENDPOINT}?key={urllib.parse.quote(c['api_key'])}"
           f"&cx={urllib.parse.quote(c['cx'])}&num={min(limit, 10)}"
           f"&q={urllib.parse.quote(q)}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return []
    _log(q)
    out = []
    for it in data.get("items", []) or []:
        amt, cur = _price_from(it)
        out.append({"url": it.get("link", ""), "title": it.get("title", ""),
                    "snippet": it.get("snippet", ""), "price": amt, "currency": cur})
    return out


def offers_for(pb, identity, limit=5):
    """Leads for one shop, in the hunter's offer shape.

    A price here was read by Google off the shop's page, not by us off the
    live page — so it is recorded as `google-index` provenance and can never
    be called `verified`. It is a real number with a stated source, which is
    the honest middle between a price and a shrug."""
    from agent import match
    name = " ".join(filter(None, [identity.get("brand"), identity.get("product")])) \
        or identity.get("query", "")
    hits = search(name, domain=pb["domain"], limit=limit)
    out = []
    for h in hits:
        if not h["url"]:
            continue
        sc, why = match.score(h["title"], identity, url=h["url"], extra=h["snippet"])
        if sc < 0.6:
            continue
        out.append({"store": pb["domain"], "url": h["url"], "title": h["title"],
                    "price": h["price"], "currency": h["currency"],
                    "match": sc, "match_why": why,
                    "from_google": True,
                    "why": "price as Google indexed it — the shop refuses direct reads"})
    return out


if __name__ == "__main__":
    import sys
    if not available():
        print("google search is not configured — see the setup notes at the top of this file")
        raise SystemExit(1)
    dom = sys.argv[1]
    q = " ".join(sys.argv[2:])
    print(f"calls used today: {calls_today()}/{_cfg()['daily_cap']}")
    for h in search(q, domain=dom):
        print(f'  {str(h["price"]):>9} {str(h["currency"] or ""):4s} {h["title"][:52]}')
        print(f'            {h["url"][:100]}')
