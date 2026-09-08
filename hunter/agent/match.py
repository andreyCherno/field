#!/usr/bin/env python3
"""Is the thing on this page the thing we hunted?

A store's search box answers with whatever it feels like. Ask ten shops for
"salomon xt-6" and you get back XT-4s, Speedcrosses, socks and a gift card —
all with a real price and a real link, all wrong. Everything downstream of a
search box is a *lead*, and this module is what turns a lead into a claim.

The rule that does most of the work: **a token carrying a digit is a model
code, and a model code must be present.** "XT-6" is not "XT-4"; "990v6" is not
"990v5". Descriptive words (black, gore-tex, low) only ever move the score
inside a band — they can never rescue a missing model code.

Separators are noise: xt-6 / XT 6 / xt6 are the same code, so comparison runs
on a squashed form. Bare 1-2 digit numbers are the exception — they must stand
as their own word, or "90" would happily match "900".

    python3 -m agent.match "Salomon XT-6 Gore-Tex Black" salomon xt-6
"""
import re, sys

# words stores abbreviate rather than spell — checked as alternatives so that
# "XT-6 GTX" is not rejected for failing to say "gore-tex"
SYNONYMS = {"goretex": ["gtx"], "gore": ["gtx", "goretex"], "tex": ["gtx", "goretex"],
            "retro": ["og"], "og": ["retro"], "grey": ["gray"], "gray": ["grey"]}

# words that carry no identity — every store sprinkles these into titles
STOP = {"the", "a", "an", "and", "of", "in", "for", "with", "new", "shoes",
        "shoe", "sneakers", "sneaker", "trainers", "mens", "womens", "men",
        "women", "man", "woman", "unisex", "kids", "adult", "size", "color",
        "colour", "edition", "official", "authentic", "buy", "online", "shop"}


def squash(s):
    """'XT-6 Gore-Tex' -> 'xt6goretex'. Separators are never identity."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def words(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()


def model_tokens(text):
    """The tokens that pin down *which* model: anything carrying a digit.
    'Salomon XT-6 Gore-Tex' -> ['xt6'];  'New Balance 990v6' -> ['990v6']."""
    out = []
    for raw in (text or "").split():
        t = squash(raw)
        if t and any(c.isdigit() for c in t) and t not in out:
            out.append(t)
    return out


def _hit(tok, parts, bag):
    """Is this token on the page? Own-word first, then squashed substring —
    except a 1-2 digit number, which must stand alone or '90' matches '900'."""
    for t in [tok] + SYNONYMS.get(tok, []):
        if t in bag:
            return True
        if t.isdigit() and len(t) < 3:
            continue
        if t.isdigit():
            # a model number must not be a slice of a longer number: "990"
            # matched an area rug coded 8990f, and 0.75 is above the bar
            if any(re.search(rf"(?<!\d){t}(?!\d)", p) for p in parts):
                return True
            continue
        if any(t in p for p in parts):
            return True
    return False


def score(title, identity, url="", brand="", extra=""):
    """(0..1, human reason). 1.0 only ever means: the style code is on the page.

    Without a style code the ceiling is 0.95 — a name can always be a different
    colourway or a kid's cut, and pretending otherwise is how a hunter promises
    a deal on the wrong shoe."""
    parts = [p for p in (squash(title), squash(brand), squash(url), squash(extra)) if p]
    bag = set(words(title) + words(brand) + words(extra))

    sku = squash(identity.get("sku"))
    if sku and len(sku) >= 5 and any(sku in p for p in parts):
        return 1.0, f'style code {identity["sku"]} is on the page'

    name = " ".join(filter(None, [identity.get("brand"), identity.get("product")])) \
           or identity.get("query", "")
    models = model_tokens(name)
    missing = [t for t in models if not _hit(t, parts, bag)]
    if missing:
        return 0.0, "model code " + "/".join(missing) + " is not on the page"

    brand_words = [w for w in words(identity.get("brand")) if w not in STOP]
    brand_ok = not brand_words or all(_hit(w, parts, bag) for w in brand_words)

    desc = [w for w in words(identity.get("product") or identity.get("query"))
            if w not in STOP and len(w) > 2
            and not any(c.isdigit() for c in w) and w not in brand_words]
    cover = sum(_hit(w, parts, bag) for w in desc) / len(desc) if desc else 1.0
    pct = int(round(cover * 100))

    if models:
        code = "/".join(models)
        # A missing descriptive word is a missing distinction, and there are
        # only ever two or three of them: "Air Max 90 Essential" carries the
        # code 90 AND the brand AND two thirds of "Air Max 90 Mercurial" while
        # being a different shoe. So partial coverage is worth a look and never
        # worth publishing — it lands between candidate_min and accept_min.
        if cover < 1:
            return 0.50, (f"model {code} matches but only {pct}% of the name — "
                          f"worth opening, not worth trusting")
        if brand_ok:
            return 0.95, f"model {code} and the whole name are on the page"
        # Shops leave the brand out of the title ("Air Max 90 SE") or put their
        # own name in JSON-LD's brand field; the code plus the full name still
        # carries it, just never to certainty.
        return 0.75, f"model {code} and the whole name match, brand not stated on the page"
    if not brand_ok:
        return round(0.30 * cover, 2), f'brand {identity.get("brand")} is not on the page' 
    return round(0.20 + 0.75 * cover, 2), \
        f"{pct}% of the name matches — no model code exists to pin it down"


if __name__ == "__main__":
    title = sys.argv[1]
    q = " ".join(sys.argv[2:])
    from agent.identify import identify
    print(score(title, identify(q)))
