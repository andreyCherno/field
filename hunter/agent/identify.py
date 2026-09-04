#!/usr/bin/env python3
"""Step 1 of a hunt: resolve whatever you typed into a product identity, and
describe it well enough that you can look at the card and say "yes, that one"
*before* 120 shops get asked anything.

    python3 -m agent.identify "salomon xt-6 gore-tex"
    python3 -m agent.identify "DV1748-101"

Output: {"query", "brand", "product", "sku", "aliases", "category", "colorway",
"year", "retail_usd", "note", "source"}. The sku is the manufacturer style code
— identical in every store on earth, which makes it both the strongest search
key and the only evidence that can score a page match 1.0.

Sources, in order: the alias dictionary (free, learned), the shelf catalogue
of 23k real pieces (free, grounded, and incapable of inventing a style code),
an SKU-shaped query taken as-is, then the model's product knowledge.
`source` is always reported, because "your own shelf says so", "the model
thinks so" and "you told me so" are three different kinds of fact — and the
whole point of the confirmation card is that you get to see which one you got.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ALIASES = os.path.join(os.path.dirname(HERE), "aliases.json")

SKU_SHAPES = [
    re.compile(r"^[A-Z]{1,3}\d{4}-\d{3}$", re.I),   # Nike: DV1748-101
    re.compile(r"^[A-Z]{2}\d{4}$", re.I),            # adidas: IE3437
    re.compile(r"^\d{12,14}$"),                       # GTIN/EAN barcode
]

FIELDS = ("brand", "product", "sku", "aliases", "category",
          "colorway", "year", "retail_usd", "note", "img", "source")


def load_aliases():
    if os.path.exists(ALIASES):
        return json.load(open(ALIASES, encoding="utf-8"))
    return {"queries": {}}


def save_alias(query, identity):
    """Remember a confirmed identity, so the same query never costs a model
    call — or a second confirmation — again."""
    db = load_aliases()
    db.setdefault("queries", {})[query.strip().lower()] = {
        k: identity.get(k) for k in FIELDS}
    json.dump(db, open(ALIASES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def blank(query, **kw):
    out = {k: None for k in FIELDS}
    out["aliases"] = []
    out.update(kw, query=query)
    return out


def identify(query, prefer_catalog=True):
    query = query.strip()
    known = load_aliases()["queries"].get(query.lower())
    if known:
        return {**blank(query), **known, "query": query, "source": "aliases"}
    if prefer_catalog:
        from agent import catalog
        hits = catalog.lookup(query, limit=1)
        # only a near-total match may pre-empt the model; a loose one belongs in
        # the candidate list where you can look at it, not in the identity
        if hits and hits[0]["score"] >= 0.95:
            return {**blank(query), **catalog.identity_from(hits[0]), "query": query}
    if any(p.match(query) for p in SKU_SHAPES):
        return blank(query, sku=query.upper(), source="sku-shaped",
                     note="A style code searches every store exactly; no name lookup needed.")
    try:
        from agent import llm
        result = llm.complete_json("parse",
            "You identify fashion/footwear products. Answer with JSON only:"
            ' {"brand": str, "product": str, "sku": str|null, "aliases": [str, ...],'
            ' "category": str, "colorway": str|null, "year": str|null,'
            ' "retail_usd": number|null, "note": str}.'
            " product is the model name WITHOUT the brand. sku is the manufacturer"
            " style code (e.g. Nike DV1748-101, adidas IE3437) only if you are sure"
            " it is this exact product, else null — a wrong code poisons the whole"
            " hunt. aliases are 2-5 alternate phrasings stores use for this item."
            " note is one sentence a shopper would use to recognise it.",
            f"Identify: {query}")
        return {**blank(query), **result, "query": query, "source": "llm"}
    except Exception as e:
        return blank(query, product=query, source="unidentified",
                     note=f"Could not identify ({type(e).__name__}) — hunting the raw text.")


def search_terms(identity, limit=3):
    """The exact phrases the stores will be asked. Showing these is most of
    what makes the confirmation card worth reading."""
    qs = []
    if identity.get("sku"):
        qs.append(identity["sku"])
    if identity.get("product"):
        qs.append(f'{identity.get("brand") or ""} {identity["product"]}'.strip())
    qs += [a for a in (identity.get("aliases") or []) if a]
    return [q for i, q in enumerate(qs) if q and q not in qs[:i]][:limit] \
        or [identity.get("query", "")]


def proposal(query):
    """Everything the confirmation card needs: what I think you meant, the
    grounded look-alikes off your own shelf, and the exact phrases the stores
    will be asked. Nothing is searched until you say this is the right item."""
    from agent import catalog
    idy = identify(query)
    return {"identity": idy, "search_terms": search_terms(idy),
            "candidates": catalog.lookup(query, limit=6),
            "catalog_size": len(catalog.items())}


if __name__ == "__main__":
    print(json.dumps(proposal(" ".join(sys.argv[1:])), ensure_ascii=False, indent=1))
