#!/usr/bin/env python3
"""Step 2 of a hunt: run the store playbooks and collect offers.

    python3 -m agent.search "salomon xt-6"            # level 1-2
    python3 -m agent.search --deep "salomon xt-6"     # level 3: LLM-parse hard stores too

Levels:
  1  stores whose playbook already proved productive (stats.hits > 0)
  2  every store with a structured method (shopify-suggest / search-url)
  3  --deep: also llm-parse stores — fetch the search page and let the parse
     model extract offers (costs cents; capped by the daily LLM budget)

Every attempt is logged to data/attempts.jsonl — the raw material learn.py
uses to rewrite playbooks. Offers go to verify.py before publication.
"""
import glob, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(400_000).decode("utf-8", "replace")

def log_attempt(row):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "attempts.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def playbooks():
    for path in sorted(glob.glob(os.path.join(ROOT, "playbooks", "*.json"))):
        if os.path.basename(path).startswith("_"):
            continue
        yield path, json.load(open(path, encoding="utf-8"))

def queries_for(identity, pb):
    qs = []
    if identity.get("sku") and pb.get("accepts_sku", True):
        qs.append(identity["sku"])
    if identity.get("product"):
        qs.append(f'{identity.get("brand") or ""} {identity["product"]}'.strip())
    qs += identity.get("aliases", [])
    if not qs:
        qs = [identity["query"]]
    return qs[:3]

def run_shopify_suggest(pb, q):
    url = (f'https://{pb["domain"]}/search/suggest.json?q={urllib.parse.quote(q)}'
           "&resources[type]=product&resources[limit]=10")
    data = json.loads(fetch(url))
    out = []
    for p in data.get("resources", {}).get("results", {}).get("products", []):
        try:
            price = float(p.get("price"))
        except (TypeError, ValueError):
            continue
        out.append({"store": pb["domain"], "title": p.get("title", ""),
                    "brand": p.get("vendor", ""), "price": price,
                    "url": f'https://{pb["domain"]}{p.get("url","")}'.split("?")[0]})
    return out

def run_llm_parse(pb, q):
    from agent import llm
    url = pb["search_url"].format(q=urllib.parse.quote(q))
    html = fetch(url)
    html = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"\s+", " ", html)[:30_000]
    hint = pb.get("price_selector", "")
    return llm.complete_json("parse",
        "Extract product offers from this store search-results page. JSON only:"
        ' [{"title": str, "brand": str, "price": number, "currency": str, "url": str}].'
        " Absolute URLs. Empty array if no real product results."
        + (f" Store hint: {hint}" if hint else ""),
        f"Query: {q}\nPage from {pb['domain']}:\n{html}", max_tokens=3000)

def hunt(identity, deep=False):
    offers = []
    for path, pb in playbooks():
        method = pb.get("method", "search-url")
        if method == "llm-parse" and not deep:
            continue
        for q in queries_for(identity, pb):
            t0, hits, err = time.time(), [], None
            try:
                if method == "shopify-suggest":
                    hits = run_shopify_suggest(pb, q)
                elif method == "llm-parse":
                    hits = [h for h in run_llm_parse(pb, q) if h.get("price")]
                    for h in hits:
                        h["store"] = pb["domain"]
                else:  # search-url: no parser — record the URL for the browser
                    hits = [{"store": pb["domain"], "title": None, "price": None,
                             "url": pb["search_url"].format(q=urllib.parse.quote(q)),
                             "manual": True}]
            except Exception as e:
                err = type(e).__name__
            log_attempt({"store": pb["domain"], "method": method, "query": q,
                         "hits": len([h for h in hits if not h.get("manual")]),
                         "error": err, "ms": int((time.time() - t0) * 1000),
                         "item": identity.get("sku") or identity["query"]})
            offers += hits
            if hits and not all(h.get("manual") for h in hits):
                break   # this store answered — next store
            time.sleep(pb.get("rate_limit_s", 1))
    return offers

if __name__ == "__main__":
    deep = "--deep" in sys.argv
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    from agent.identify import identify
    identity = identify(q)
    print(json.dumps({"identity": identity, "offers": hunt(identity, deep)},
                     ensure_ascii=False, indent=1))
