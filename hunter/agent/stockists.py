#!/usr/bin/env python3
"""Find official retailers the way a brand names them: on its own stockist page.

A brand's stockist / store-locator page is the definition of "authorised
seller", and it lists the small local shops in every country — the ones no
registry of famous e-tailers will ever contain. This walks in the brand's
front door, follows the link it labels "stockists" / "stores" / "where to
buy", and harvests every external shop domain on that page.

    python3 -m agent.stockists ourlegacy.com carhartt-wip.com salomon.com
"""
import json, re, sys, urllib.parse

STOCKIST_WORDS = re.compile(r"stockist|store[\s-]?locator|find[\s-]a[\s-]store|where[\s-]to[\s-]buy|"
                            r"retailers|dealers|our[\s-]stores|shops?\b|stores\b", re.I)
NOT_SHOPS = re.compile(r"(facebook|instagram|twitter|x\.com|tiktok|youtube|pinterest|linkedin|"
                       r"google|apple|shopify|cloudfront|akamai|cdn|klarna|paypal|afterpay|"
                       r"trustpilot|mailchimp|klaviyo|wikipedia|vimeo|spotify)", re.I)


def harvest(brand_domain, max_pages=3):
    from agent import browser
    home = f"https://www.{brand_domain.removeprefix('www.')}/"
    v = browser.visit(home, wait_ms=3000, collect=browser.LINKS_JS)
    links = v.get("collected") or []
    cands = []
    for l in links:
        txt = (l.get("text") or "") + " " + (l.get("href") or "")
        if STOCKIST_WORDS.search(txt) and browser._same_site(urllib.parse.urlparse(l["href"]).netloc, brand_domain):
            cands.append(l["href"])
    seen, pages = set(), []
    for c in cands:
        c = c.split("#")[0]
        if c not in seen:
            seen.add(c); pages.append(c)
    out = {"brand": brand_domain, "stockist_pages": pages[:max_pages], "retailers": {}}
    for p in pages[:max_pages]:
        try:
            vv = browser.visit(p, wait_ms=4000, collect=browser.LINKS_JS)
        except Exception:
            continue
        for l in vv.get("collected") or []:
            host = urllib.parse.urlparse(l.get("href") or "").netloc.lower().removeprefix("www.")
            if not host or browser._same_site(host, brand_domain) or NOT_SHOPS.search(host):
                continue
            out["retailers"].setdefault(host, (l.get("text") or "")[:60])
    return out


if __name__ == "__main__":
    for b in sys.argv[1:]:
        r = harvest(b)
        print(f'=== {b}: {len(r["stockist_pages"])} stockist page(s), {len(r["retailers"])} external shop domains')
        for p in r["stockist_pages"]: print("    page:", p[:100])
        for h, t in list(r["retailers"].items())[:25]: print(f'    {h:36s} {t}')
        json.dump(r, open(f"/tmp/claude-501/-Users-andreychernojr-field/3d689e92-9ea5-42db-9842-594c0b034501/scratchpad/stockists_{b}.json", "w"), indent=1, ensure_ascii=False)
    print("STOCKISTS DONE")
