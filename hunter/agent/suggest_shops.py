#!/usr/bin/env python3
"""Propose official retailers per country, then keep only what verifies live.

The model proposes; the probe disposes. A language model knows the names of
the authorised multi-brand shops in most countries — and also confidently
invents some. So nothing it says reaches the registry until agent/onboard.py
has actually reached the shop, found its search, and read a currency off a
product page. Marketplaces and resale are excluded by instruction and by a
domain blocklist, because the whole point is official sellers.

    python3 -m agent.suggest_shops JP KR TW SG          # propose + verify + add
    python3 -m agent.suggest_shops --dry JP             # propose only, print
"""
import json, os, re, sys, time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(ROOT, "data", "suggest_shops.jsonl")

MARKETPLACES = re.compile(
    r"(amazon|ebay|taobao|tmall|aliexpress|alibaba|rakuten\.co\.jp|mercari|yahoo|"
    r"shopee|lazada|coupang|gmarket|11st|kream|stockx|goat\.com|grailed|vinted|"
    r"depop|poizon|dewu|farfetch|lyst|klarna|etsy|walmart|target\.com|jd\.com)", re.I)

ANGLES = {
    "street": "streetwear and sneaker boutiques (the kind that stock Salomon, ASICS, adidas, "
              "Carhartt WIP, Stone Island, Our Legacy, Norse Projects)",
    "designer": "designer / contemporary multi-brand boutiques, outdoor specialists and "
                "department stores with their own online shop",
    "running": "specialist running, trail and outdoor retailers (the kind that stock Salomon, "
               "ASICS, adidas Adizero, Hoka, On, Arc'teryx) plus independent sneaker shops in "
               "second cities, not the capital",
    "cities": "independent multi-brand boutiques named city by city — list the 3 to 4 largest "
              "cities of the country and for each the best-known independent sneaker, streetwear "
              "or contemporary-menswear shop with its own webshop that ships abroad",
    "outlets": "official outlet and off-price webshops, department-store online arms, and "
               "multi-brand sports retailers (Intersport-type chains) with international shipping",
}
PROMPT = ("You list AUTHORISED multi-brand retailers that have their own e-commerce site and "
          "ship internationally. Country: {cc}. Angle: {angle}. "
          "Rules: official/authorised stockists only — NO marketplaces, NO resale, NO "
          "brand-owned mono-brand stores, NO price aggregators. Prefer independent "
          "boutiques and department stores that carry brands like Salomon, ASICS, "
          "adidas, Carhartt WIP, Stone Island, Our Legacy, Norse Projects. Return JSON "
          'only: [{{"domain": "example.com", "name": "…", "city": "…", '
          '"category": "streetwear boutique|fashion|luxury boutique|outdoor|department store", '
          '"note": "one line"}}, …] with 6 to 10 entries. If unsure a domain is exact, omit it.')


def propose(cc, angle="street"):
    from agent import llm
    try:
        rows = llm.complete_json("reason", "You are a careful retail researcher. JSON only.",
                                 PROMPT.format(cc=cc, angle=ANGLES[angle]), max_tokens=1500)
    except ValueError:      # malformed JSON from the model — one retry, stricter
        rows = llm.complete_json("reason", "Reply with ONLY a valid JSON array. No prose, "
                                 "no comments, no trailing commas, every string double-quoted.",
                                 PROMPT.format(cc=cc, angle=ANGLES[angle]), max_tokens=1500)
    out = []
    for r in rows if isinstance(rows, list) else []:
        d = str(r.get("domain", "")).lower().strip().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
        if not d or "." not in d or MARKETPLACES.search(d):
            continue
        out.append({**r, "domain": d})
    return out


def run(countries, dry=False):
    from agent import onboard
    known = {json.load(open(os.path.join(ROOT, "playbooks", f), encoding="utf-8"))["domain"]
             for f in os.listdir(os.path.join(ROOT, "playbooks")) if f.endswith(".json") and not f.startswith("_")}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    summary = {}
    for cc in countries:
        cands, seen = [], set()
        for angle in ANGLES:
            try:
                for c in propose(cc, angle):
                    if c["domain"] not in seen:
                        seen.add(c["domain"]); cands.append(c)
            except Exception as e:
                print(f"[{cc}/{angle}] proposal failed: {type(e).__name__}: {e}", flush=True)
        if not cands:
            continue
        print(f"\n[{cc}] model proposed {len(cands)}: " + ", ".join(c["domain"] for c in cands), flush=True)
        added, failed = [], []
        for c in cands:
            if c["domain"] in known:
                print(f"   {c['domain']:28s} already in registry", flush=True)
                continue
            if dry:
                continue
            t0 = time.time()
            try:
                o = onboard.add(c["domain"], country=cc, name=c.get("name"),
                                category=c.get("category"), note=c.get("note"))
            except Exception as e:
                o = {"verdict": f"error {type(e).__name__}"}
            ok = o.get("verdict") == "ok"
            (added if ok else failed).append((c["domain"], o.get("verdict"), o.get("method"), o.get("currency")))
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"date": date.today().isoformat(), "cc": cc, "domain": c["domain"],
                                    "verdict": o.get("verdict"), "method": o.get("method"),
                                    "currency": o.get("currency"), "secs": int(time.time() - t0)}) + "\n")
            print(f"   {c['domain']:28s} {'ADDED' if ok else 'no':6s} {o.get('method') or o.get('verdict')}  {o.get('currency') or ''}  ({int(time.time()-t0)}s)", flush=True)
        summary[cc] = (added, failed)
    print("\n=== SUMMARY", flush=True)
    for cc, (a, f) in summary.items():
        print(f"  {cc}: added {len(a)} · rejected {len(f)}", flush=True)
        for d, v, m, cur in a:
            print(f"      + {d:28s} {m:16s} {cur or ''}", flush=True)
    print("SUGGEST DONE", flush=True)
    return summary


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    ccs = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(ccs or ["JP"], dry=dry)
