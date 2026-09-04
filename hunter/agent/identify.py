#!/usr/bin/env python3
"""Step 1 of a hunt: resolve whatever the user typed into a product identity.

    python3 -m agent.identify "salomon xt-6 gore-tex"
    python3 -m agent.identify "DV1748-101"

Output: {"query", "brand", "product", "sku", "aliases": [...]} — the sku is the
manufacturer style code that is identical in every store, which makes it the
strongest search key. Sources, in order: the alias dictionary (free, learned),
an SKU-shaped query taken as-is, then the LLM's product knowledge.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ALIASES = os.path.join(os.path.dirname(HERE), "aliases.json")

SKU_SHAPES = [
    re.compile(r"^[A-Z]{1,3}\d{4}-\d{3}$", re.I),   # Nike: DV1748-101
    re.compile(r"^[A-Z]{2}\d{4}$", re.I),            # adidas: IE3437
    re.compile(r"^\d{12,14}$"),                       # GTIN/EAN barcode
]

def load_aliases():
    if os.path.exists(ALIASES):
        return json.load(open(ALIASES, encoding="utf-8"))
    return {"queries": {}}

def identify(query):
    query = query.strip()
    known = load_aliases()["queries"].get(query.lower())
    if known:
        return {**known, "query": query, "source": "aliases"}
    if any(p.match(query) for p in SKU_SHAPES):
        return {"query": query, "brand": None, "product": None,
                "sku": query.upper(), "aliases": [], "source": "sku-shaped"}
    from agent import llm
    result = llm.complete_json("parse",
        "You identify fashion/footwear products. Answer with JSON only:"
        ' {"brand": str, "product": str, "sku": str|null, "aliases": [str, ...]}.'
        " sku is the manufacturer style code (e.g. Nike DV1748-101, adidas IE3437)"
        " if you know it for this exact product, else null. aliases are 2-5"
        " alternate search phrasings stores might use.",
        f"Identify: {query}")
    result["query"] = query
    result["source"] = "llm"
    return result

if __name__ == "__main__":
    print(json.dumps(identify(" ".join(sys.argv[1:])), ensure_ascii=False, indent=1))
