#!/usr/bin/env python3
"""Add a shop to the registry from nothing but its domain.

"More shops from every country" is not a data-entry job; hand-writing a
playbook per shop is how 80 of the first 100 got a guessed search url that
404'd. This probes what the shop actually exposes and writes down only what
was observed:

    python3 -m agent.onboard probe juicestore.com          # dry run, prints
    python3 -m agent.onboard add   juicestore.com --country HK --name "JUICE" \\
                                   --category "streetwear boutique" --note "..."

What is observed, in order: does it answer plain HTTP or only a real browser;
is it Shopify (suggest.json answers); does it publish a sitemap; what is its
search url (its own form, else typed into its search box); what currency does
a product page render. Country comes from the TLD or from you — never guessed
from the currency, because Shopify Markets prices by the visitor's IP.
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request, ssl
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "en"}
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _get(url, n=300_000, t=12):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=t, context=_ctx)
    return r.status, r.geturl(), r.read(n).decode("utf-8", "replace")


def probe(domain, verbose=True):
    from agent import browser, sitemap, landed
    d = re.sub(r"^https?://", "", domain).strip("/").removeprefix("www.")
    o = {"domain": d, "host": None, "plain_http": None, "browser": None, "shopify_host": None,
         "sitemap": None, "search_url": None, "search_url_source": None, "currency": None,
         "country": landed.country_of(d), "sample_product": None}
    say = (lambda *a: print("   ", *a, flush=True)) if verbose else (lambda *a: None)

    for host in ("www." + d, d):                      # 1. reachability
        try:
            s, fu, h = _get(f"https://{host}/")
            o["plain_http"], o["host"] = s, host
            break
        except urllib.error.HTTPError as e:
            o["plain_http"] = e.code
        except Exception as e:
            o["plain_http"] = type(e).__name__
    if o["plain_http"] != 200:
        try:
            v = browser.visit(f"https://www.{d}/", wait_ms=2500)
            o["browser"] = v["status"]
            o["host"] = o["host"] or urllib.parse.urlparse(v["url"]).netloc
        except Exception as e:
            o["browser"] = type(e).__name__
    say(f'reach: plain={o["plain_http"]} browser={o["browser"]} host={o["host"]}')
    if o["plain_http"] != 200 and o["browser"] != 200:
        o["verdict"] = "unreachable"
        return o

    for host in ("www." + d, d):                      # 2. shopify?
        try:
            s, fu, h = _get(f"https://{host}/search/suggest.json?q=a&resources[type]=product&resources[limit]=1", 50_000, 8)
            if '"products"' in h:
                o["shopify_host"] = host
                break
        except Exception:
            pass
    say(f'shopify: {o["shopify_host"] or "no"}')

    info = sitemap.robots(d)                          # 3. sitemap (honours robots)
    o["sitemap"] = info["sitemaps"][0] if info["sitemaps"] else None
    o["robots_disallow_all"] = any(x.strip() == "/" for x in info["disallow"])
    say(f'sitemap: {o["sitemap"] or "none"}  disallow-all={o["robots_disallow_all"]}')

    if o["shopify_host"]:                             # 4. search url
        o["search_url"] = f'https://{o["shopify_host"]}/search?q={{q}}'
        o["search_url_source"] = "shopify: suggest.json verified live"
    else:
        try:
            o["search_url"] = browser.find_search_url(d)
            if o["search_url"]:
                o["search_url_source"] = "read from the shop's own search form"
        except Exception:
            pass
        if not o["search_url"]:
            try:
                v = browser.type_search(d, "shoes", wait_ms=3500)
                u = v["url"]
                for form in (urllib.parse.quote("shoes"), urllib.parse.quote_plus("shoes"), "shoes"):
                    u = u.replace(form, "{q}")
                if "{q}" in u:
                    o["search_url"] = u
                    o["search_url_source"] = "observed: typed into the shop's own search box"
            except Exception:
                pass
    say(f'search: {o["search_url"] or "not found"}')

    try:                                              # 5. currency, from a real product page
        from agent import inspect as insp
        sample = None
        if o["shopify_host"]:
            s, fu, h = _get(f"https://{o['shopify_host']}/search/suggest.json?q=a&resources[type]=product&resources[limit]=1", 50_000, 8)
            ps = json.loads(h).get("resources", {}).get("results", {}).get("products", [])
            if ps:
                sample = f"https://{o['shopify_host']}{ps[0].get('url','')}".split("?")[0]
        if not sample and o["search_url"]:
            v = browser.visit(o["search_url"].format(q=urllib.parse.quote("shoes")), wait_ms=3000, collect=browser.LINKS_JS)
            for l in v.get("collected") or []:
                u = urllib.parse.urlparse(l.get("href") or "")
                if browser._same_site(u.netloc, d) and browser.PRODUCT_PATH.search(u.path):
                    sample = l["href"].split("?")[0]
                    break
        if sample:
            o["sample_product"] = sample
            p = insp.read_page(sample)
            o["currency"] = p.get("currency")
            o["sample_price"] = p.get("price")
            o["sample_name"] = (p.get("name") or "")[:60]
    except Exception as e:
        o["currency_error"] = type(e).__name__
    say(f'currency: {o["currency"] or "?"}  sample={o.get("sample_name") or "-"} {o.get("sample_price") or ""}')

    if o["shopify_host"]:
        o["method"] = "shopify-suggest"
    elif o["search_url"]:
        o["method"] = "search-url"
    elif o["sitemap"] and not o["robots_disallow_all"]:
        o["method"] = "sitemap-index"
    else:
        o["method"] = None
    o["verdict"] = "ok" if o["method"] else "no route"
    return o


def add(domain, country=None, name=None, category=None, note=None, ships_il="unknown"):
    o = probe(domain)
    if o["verdict"] != "ok":
        print(f'  NOT ADDED — {o["verdict"]}')
        return o
    d = o["domain"]
    pb = {"domain": d, "method": o["method"], "accepts_sku": True, "query_tips": "",
          "rate_limit_s": 2, "stats": {"attempts": 0, "hits": 0, "last_success": None},
          "currency": o["currency"] or "USD",
          "currency_source": ("read off a product page" if o["currency"] else
                              "UNKNOWN — defaulted to USD, fix me"),
          "onboarded": date.today().isoformat()}
    if o["search_url"]:
        pb["search_url"] = o["search_url"]
        pb["search_url_source"] = o["search_url_source"]
    if o["shopify_host"] and o["shopify_host"] != d:
        pb["host"] = o["shopify_host"]
    if o["plain_http"] != 200:
        pb["needs_browser"] = True
    path = os.path.join(ROOT, "playbooks", d.replace(".", "-") + ".json")
    json.dump(pb, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    regp = os.path.join(ROOT, "registry.json")
    reg = json.load(open(regp, encoding="utf-8"))
    if not any(d in (r.get("url") or "") for r in reg):
        reg.append({"name": name or d, "url": f"https://{o['host'] or 'www.' + d}",
                    "country": country or o["country"], "category": category,
                    "focus": note or "", "ships_to_israel": ships_il,
                    "note": note or "", "platform": "shopify" if o["shopify_host"] else "custom",
                    "source": f"onboard {date.today().isoformat()}"})
        json.dump(reg, open(regp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f'  ADDED {d} as {o["method"]} · currency {pb["currency"]} · country {country or o["country"]}')
    if o["sitemap"] and not o["robots_disallow_all"]:
        print(f'  (publishes a sitemap — run: python3 -m agent.sitemap build {d})')
    return o


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "add"])
    ap.add_argument("domain")
    ap.add_argument("--country"); ap.add_argument("--name"); ap.add_argument("--category")
    ap.add_argument("--note"); ap.add_argument("--ships-il", default="unknown")
    a = ap.parse_args()
    print(f"=== {a.domain}")
    if a.cmd == "probe":
        print("   ", json.dumps(probe(a.domain), ensure_ascii=False))
    else:
        add(a.domain, a.country, a.name, a.category, a.note, a.ships_il)
