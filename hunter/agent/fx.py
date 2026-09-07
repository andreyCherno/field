#!/usr/bin/env python3
"""Currency conversion for the hunter. Every store answers in its own
currency; all comparison and landed math happens in USD.

Rates: baked fallbacks below, refreshed from frankfurter.app (free, no key)
at most once a day into data/fx.json. If the refresh fails we keep going on
the last known rates — a slightly stale rate never blocks a hunt.
"""
import json, os, re, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(HERE), "data", "fx.json")

FALLBACK = {  # USD per 1 unit
    "USD": 1.0, "ILS": 0.27, "EUR": 1.09, "GBP": 1.28, "SEK": 0.095,
    "DKK": 0.146, "PLN": 0.25, "TRY": 0.03, "AED": 0.272, "AUD": 0.66,
    "CAD": 0.73, "JPY": 0.0067, "CHF": 1.12, "HKD": 0.128, "CNY": 0.14,
    # Asia-Pacific shops, added with them. A currency missing here converts
    # at 1.0 silently — SGD would have read as USD, a 33% overstatement.
    "SGD": 0.75, "KRW": 0.00072, "TWD": 0.031, "MYR": 0.22, "THB": 0.029,
    "NZD": 0.60, "INR": 0.012, "PHP": 0.017, "IDR": 0.000061, "VND": 0.000039,
}

_rates = None

def rates():
    global _rates
    if _rates is not None:
        return _rates
    _rates = dict(FALLBACK)
    try:
        cached = json.load(open(CACHE, encoding="utf-8"))
        _rates.update(cached.get("rates", {}))
        if cached.get("date") == date.today().isoformat():
            return _rates
    except (OSError, ValueError):
        pass
    try:  # refresh: frankfurter gives EUR-base -> invert to USD-per-unit
        with urllib.request.urlopen(
                "https://api.frankfurter.app/latest?from=USD", timeout=8) as r:
            data = json.load(r)
        fresh = {cur: 1.0 / v for cur, v in data.get("rates", {}).items() if v}
        fresh["USD"] = 1.0
        _rates.update({k: v for k, v in fresh.items() if k in FALLBACK})
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({"date": date.today().isoformat(), "rates": _rates},
                  open(CACHE, "w", encoding="utf-8"))
    except Exception:
        pass   # offline / blocked — fallback rates carry the day
    return _rates

def parse_amount(raw):
    """Any shop's way of writing a number -> a float, or None.

    Europe writes 154,34 for what the US writes 154.34, and 1.299,00 for
    1,299.00. Stripping commas — which is what this used to do everywhere —
    turns 154,34 into 15434 (a 100x overcharge) and 1.299,00 into 1.299
    (a 1000x undercount). Both were live: an adidas hunt reported $12,500.99
    for a running shoe.

    The rule that resolves it: whichever separator appears LAST is the decimal
    one. With only one separator present, a group of exactly three digits
    after it is a thousands group, and anything else is a decimal fraction.
    """
    if raw is None:
        return None
    t = re.sub(r"[^\d.,\-]", "", str(raw).replace("\u00a0", " ")).strip()
    if not t or not re.search(r"\d", t):
        return None
    last_dot, last_comma = t.rfind("."), t.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        dec = "." if last_dot > last_comma else ","
        t = t.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif last_comma >= 0:
        tail = len(t) - last_comma - 1
        t = t.replace(",", "") if (tail == 3 and t.count(",") >= 1) else t.replace(",", ".")
    elif last_dot >= 0:
        tail = len(t) - last_dot - 1
        if tail == 3:
            t = t.replace(".", "")          # 12.500 is twelve thousand five hundred
    try:
        return float(t)
    except ValueError:
        return None


def to_usd(price, currency):
    if price is None:
        return None
    return round(price * rates().get((currency or "USD").upper(), 1.0), 2)
