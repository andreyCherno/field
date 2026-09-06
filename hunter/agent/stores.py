#!/usr/bin/env python3
"""Which shops to ask, and in what order.

A single-item hunt is a queue of ~116 shops, each costing a page load. Two
facts were sitting unused in the repo and both change that queue:

1. **data/attempts.jsonl already knows which shops produce.** 2,284 attempts
   are logged and exactly 28 shops have ever returned a hit — yet every
   playbook still carries `stats: {hits: 0}` and nothing sorted by it, so the
   "level 1: shops that already proved productive" idea in search.py's
   docstring never actually ran. Asking the proven 28 first is the difference
   between first results in seconds and first results in minutes.

2. **registry.json knows what each shop sells.** 32 of 111 shops are
   furniture, lighting or homeware. They were asked for sneakers on every
   hunt, and they can never have the item.

Relevance is a veto, never a preference: an unknown shop and an unknown item
are both asked, because unknown is not "no". The only shops skipped are ones
whose whole trade is provably a different world from the item's.
"""
import json, os, re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The two worlds this registry actually spans. A shop in one cannot stock the
# other — nobody buys a Gel-Kayano from a chandelier shop.
HOME = {"furniture", "lighting", "home", "furniture/lighting/home",
        "kitchen", "homeware", "decor"}
WEARABLE_CATS = {"footwear", "tops", "bottoms", "outerwear", "accessories"}
# item words that put a hunt firmly in the home world
HOME_WORDS = re.compile(
    r"\b(lamp|lampe|chandelier|pendant|sconce|sofa|couch|chair|stool|table|desk|"
    r"shelf|shelving|cabinet|dresser|bed|mattress|rug|carpet|curtain|cushion|"
    r"vase|mirror|cookware|pan|pot|cutlery|plate|bowl|kettle|fan|luminaire)\b", re.I)


def _domain(url):
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url or "").split("/")[0])


_reg = None


def registry():
    """domain -> registry row."""
    global _reg
    if _reg is None:
        _reg = {}
        try:
            for r in json.load(open(os.path.join(ROOT, "registry.json"), encoding="utf-8")):
                d = _domain(r.get("url"))
                if d:
                    _reg[d] = r
        except (OSError, ValueError):
            pass
    return _reg


def productivity():
    """domain -> hits it has ever produced, from the attempts log."""
    out = defaultdict(int)
    path = os.path.join(ROOT, "data", "attempts.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                out[row.get("store")] += row.get("hits") or 0
    except OSError:
        pass
    return out


def world_of_item(identity):
    """'home', 'wear', or None when we genuinely cannot tell."""
    text = " ".join(filter(None, [identity.get("brand"), identity.get("product"),
                                  identity.get("query")]))
    if HOME_WORDS.search(text or ""):
        return "home"
    cat = str(identity.get("category") or "").strip().lower()
    if cat in WEARABLE_CATS:
        return "wear"
    if cat in HOME.union({"other"}) and cat != "other":
        return "home"
    return None


def world_of_shop(domain):
    row = registry().get(domain)
    if not row:
        return None
    cat = str(row.get("category") or "").strip().lower()
    if cat in HOME:
        return "home"
    if cat:
        return "wear"        # fashion, boutique, streetwear, luxury e-tailer...
    return None


def irrelevant(domain, identity):
    """True only when this shop's whole trade is the other world. Returns
    (skip, reason)."""
    want = world_of_item(identity)
    have = world_of_shop(domain)
    if want is None or have is None or want == have:
        return False, None
    return True, f"sells {have}, hunting {want}"


def order(playbooks, identity=None):
    """Playbooks in the order worth asking, plus the ones to skip.

    Returns (queue, skipped) where skipped is [(pb, reason)]. Ordering:
    shops that have produced hits first (most productive first), then shops
    never tried, then shops tried and never productive. Structured methods
    break ties, because they answer in milliseconds instead of seconds."""
    hits = productivity()
    tried = set()
    path = os.path.join(ROOT, "data", "attempts.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    tried.add(json.loads(line).get("store"))
                except ValueError:
                    pass
    except OSError:
        pass

    queue, skipped = [], []
    for pb in playbooks:
        d = pb["domain"]
        # A shop we have already proven unsearchable. This is honoured, not
        # merely documented: re-running two url variants and a type-search on
        # five such shops cost about 90 seconds of every hunt. Delete the
        # "skip" key from the playbook to try it again.
        if pb.get("skip"):
            skipped.append((pb, pb["skip"].get("reason", "marked unsearchable")))
            continue
        if identity:
            skip, why = irrelevant(d, identity)
            if skip:
                skipped.append((pb, why))
                continue
        queue.append(pb)

    fast = {"shopify-suggest": 0, "search-url": 1, "llm-parse": 2}

    def key(pb):
        d = pb["domain"]
        h = hits.get(d, 0)
        band = 0 if h > 0 else (1 if d not in tried else 2)
        return (band, -h, fast.get(pb.get("method"), 3), d)

    queue.sort(key=key)
    return queue, skipped


if __name__ == "__main__":
    import glob, sys
    pbs = [json.load(open(p, encoding="utf-8"))
           for p in sorted(glob.glob(os.path.join(ROOT, "playbooks", "*.json")))
           if not os.path.basename(p).startswith("_")]
    idy = {"query": " ".join(sys.argv[1:]) or "asics gel kayano 14",
           "product": " ".join(sys.argv[1:]) or "asics gel kayano 14", "category": "Footwear"}
    q, sk = order(pbs, idy)
    print(f"item world: {world_of_item(idy)}  |  ask {len(q)}, skip {len(sk)}")
    print("\nfirst 15 asked:")
    for pb in q[:15]:
        print(f'   {pb["domain"]:26s} {pb.get("method"):16s} hits={productivity().get(pb["domain"],0)}')
    print(f"\nskipped ({len(sk)}):", ", ".join(pb["domain"] for pb, _ in sk[:14]), "...")
