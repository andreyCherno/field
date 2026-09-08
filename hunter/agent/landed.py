#!/usr/bin/env python3
"""Sticker price -> what it actually costs to get it to your door in Israel.

Three things the old flat formula got wrong, all of them producing numbers
that looked plausible:

1. **It read "priced in ILS" as "Israeli shop".** Shopify Markets prices by the
   requesting IP, so a Toronto shop happily quotes shekels. The old rule then
   skipped every import step for a parcel that has to clear customs. The store's
   *country* decides that, never the currency.

2. **It charged Israeli VAT on top of European VAT.** An EU or UK listing is
   VAT-inclusive by law; on export that VAT comes off, and Israeli VAT goes on
   the net. Adding 18% to a 22%-inclusive Italian price invents about a quarter
   of the number.

3. **It had no way to say "I don't know".** A foreign shop quoting shekels has
   probably already put destination tax in the price — and the page never says.
   That case is now reported as uncertain instead of guessed in either
   direction, which is the standing rule for this catalogue.

Deliberately cruder than shelf/items.json's itemised block (which also carries
shipping and per-line confidence). That engine lives in the MoneyMachine
backend and is not callable from here; when it is, this file becomes a call.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))

# VAT already inside a consumer price in that country, removed on export.
ORIGIN_VAT = {
    "IT": 0.22, "DE": 0.19, "FR": 0.20, "NL": 0.21, "SE": 0.25, "DK": 0.25,
    "PL": 0.23, "GB": 0.20, "ES": 0.21, "BE": 0.21, "AT": 0.20, "IE": 0.23,
    "FI": 0.255, "PT": 0.23, "AU": 0.10, "IL": 0.18,
    "US": 0.0,   # sales tax is added at checkout, never in the listed price
    "HK": 0.0, "AE": 0.0, "CH": 0.081, "JP": 0.10, "CA": 0.0,
}
NAME_TO_ISO = {
    "israel": "IL", "il": "IL", "italy": "IT", "usa": "US", "united states": "US",
    "uk": "GB", "united kingdom": "GB", "germany": "DE", "sweden": "SE",
    "france": "FR", "netherlands": "NL", "australia": "AU", "hong kong": "HK",
    "poland": "PL", "denmark": "DK", "spain": "ES", "belgium": "BE",
    "austria": "AT", "ireland": "IE", "finland": "FI", "portugal": "PT",
    "switzerland": "CH", "japan": "JP", "canada": "CA", "usa/italy": "US",
}
TLD_TO_ISO = {"co.il": "IL", "it": "IT", "de": "DE", "fr": "FR", "nl": "NL",
              "se": "SE", "dk": "DK", "pl": "PL", "co.uk": "GB", "es": "ES",
              "eu": "IT", "ch": "CH", "jp": "JP", "ca": "CA", "com.au": "AU"}

# Shops the shelf trades with that registry.json does not list. Hand-entered
# from each retailer's own stated location — these are matters of public record
# (a shop's home city), not guesses.
#
# farfetch.com is deliberately absent: it is a marketplace that ships from
# whichever partner boutique holds the item, so it has no single origin and
# "unknown" is the only true answer.
KNOWN_SHOPS = {
    "asos.com": "GB", "flannels.com": "GB", "harveynichols.com": "GB",
    "theoutnet.com": "GB", "endclothing.com": "GB",
    "capsuletoronto.com": "CA", "shop.havenshop.com": "CA", "havenshop.com": "CA",
    "ssense.com": "CA",
    "undefeated.com": "US", "sneakerpolitics.com": "US", "unionlosangeles.com": "US",
    "shopnicekicks.com": "US", "wishatl.com": "US", "cncpts.com": "US",
    "lapstoneandhammer.com": "US", "socialstatuspgh.com": "US",
    "extrabutterny.com": "US", "thepremierstore.com": "US",
    "a-ma-maniere.com": "US", "xhibition.co": "US", "blendsus.com": "US",
    "renarts.com": "US", "feature.com": "US", "packershoes.com": "US",
    "modaoperandi.com": "US",
    "slamjam.com": "IT", "rattiboutique.com": "IT", "michelefranzesemoda.com": "IT",
    "asphaltgold.com": "DE", "vooberlin.com": "DE", "afew-store.com": "DE",
    "footdistrict.com": "ES", "tres-bien.com": "SE",
}

_registry = None


def _by_domain():
    """domain -> ISO country, from registry.json (112 shops carry one)."""
    global _registry
    if _registry is None:
        _registry = {}
        try:
            for r in json.load(open(os.path.join(ROOT, "registry.json"), encoding="utf-8")):
                d = re.sub(r"^www\.", "", re.sub(r"^https?://", "", r.get("url") or "")).split("/")[0]
                raw = str(r.get("country", "")).strip()
                # registry rows carry either a name ("Italy") or an ISO code
                # ("HK") — the onboarding tool writes codes, so accept both
                iso = NAME_TO_ISO.get(raw.lower()) or (raw.upper() if re.fullmatch(r"[A-Za-z]{2}", raw) else None)
                if d and iso:
                    _registry[d] = iso
        except (OSError, ValueError):
            pass
    return _registry


def country_of(domain):
    """Where does this shop ship from? Registry first, then the TLD, else None
    — and None stays None rather than becoming a convenient default."""
    if not domain:
        return None
    d = re.sub(r"^www\.", "", str(domain).lower()).split("/")[0]
    hit = _by_domain().get(d) or KNOWN_SHOPS.get(d)
    if hit:
        return hit
    for tld, iso in TLD_TO_ISO.items():
        if d.endswith("." + tld):
            return iso
    return None


_SHIP = None


def ships_to_il(domain):
    """True / False / None from the shelf's shipping-policies.json (57 known).
    Keyed by the shelf's retailer slug, so match on the domain's first label."""
    global _SHIP
    if _SHIP is None:
        _SHIP = {}
        try:
            data = json.load(open(os.path.join(ROOT, "..", "shelf", "shipping-policies.json"),
                                  encoding="utf-8")).get("retailers", {})
            for k, v in data.items():
                _SHIP[k.lower()] = (v.get("IL") or {}).get("shipsTo")
        except Exception:
            pass
    if not domain:
        return None
    label = re.sub(r"^www\.", "", domain.lower()).split(".")[0]
    for k, v in _SHIP.items():
        if k == label or k in label or label in k:
            return v
    return None


def to_door(usd, domain=None, currency=None):
    """USD sticker -> {total, lines, confidence, note}.

    `lines` is the itemisation, because a total you cannot decompose is one you
    have no reason to trust."""
    if usd is None:
        return {"total": None, "lines": [], "confidence": "none",
                "note": "no price to work from"}
    ccy = (currency or "").upper()
    iso = country_of(domain)
    lines = [("Item price", round(usd, 2))]
    if ccy and ccy not in ("USD", "ILS"):
        fee = round(usd * CFG.get("card_fx_markup", 0.02), 2)
        lines.append((f"Card FX markup ({CFG.get('card_fx_markup', 0.02)*100:.0f}%) — {ccy}, "
                      f"the mid-market rate is not what your card gives you", fee))
        usd = usd + fee
    ship = ships_to_il(domain)

    if iso == "IL":
        return {"total": round(usd, 2), "lines": lines, "confidence": "high",
                "note": "Israeli shop — VAT is already in the price and nothing is imported"}

    if iso is None:
        return {"total": round(usd, 2), "lines": lines, "confidence": "unknown",
                "note": "shop country unknown — import steps could not be applied"}

    # A foreign shop quoting shekels is geo-pricing, and geo prices usually
    # already carry destination tax. The page never says which. Do not guess.
    if ccy == "ILS":
        return {"total": round(usd, 2), "lines": lines, "confidence": "uncertain",
                "note": "foreign shop priced in shekels — destination tax may already "
                        "be inside this number; confirm at checkout"}

    vat = ORIGIN_VAT.get(iso, 0.0)
    net = usd
    if vat:
        net = usd / (1 + vat)
        lines.append((f"Less {iso} VAT ({vat*100:.0f}%) — removed on export",
                      -round(usd - net, 2)))

    if net <= CFG["tax_free_under_usd"]:
        lines.append((f'Under the ${CFG["tax_free_under_usd"]} exemption', 0.0))
        total = net
    elif net <= CFG["customs_over_usd"]:
        v = net * CFG["vat_rate"]
        lines.append((f'Israeli import VAT ({CFG["vat_rate"]*100:.0f}%)', round(v, 2)))
        total = net + v
    else:
        v = net * CFG["vat_rate"]
        duty = net * 0.10
        lines.append((f'Israeli import VAT ({CFG["vat_rate"]*100:.0f}%)', round(v, 2)))
        lines.append((f'Customs duty over ${CFG["customs_over_usd"]}', round(duty, 2)))
        total = net + v + duty

    out = {"total": round(total, 2), "lines": lines, "confidence": "medium",
           "note": "shipping is not included — no delivery policy on file for this shop"}
    if ship is False:
        out["ships_to_il"] = False
        out["confidence"] = "no-delivery"
        out["note"] = "this shop does not ship to Israel (shelf shipping policy) — not a deal for you"
    elif ship is True:
        out["ships_to_il"] = True
        out["note"] = "ships to Israel (shelf shipping policy); shipping cost itself is not on file"
    return out


def total(usd, domain=None, currency=None):
    return to_door(usd, domain, currency)["total"]


if __name__ == "__main__":
    import sys
    print(json.dumps(to_door(float(sys.argv[1]), *sys.argv[2:]), ensure_ascii=False, indent=1))
