#!/usr/bin/env python3
"""Currency conversion for the hunter. Every store answers in its own
currency; all comparison and landed math happens in USD.

Rates: baked fallbacks below, refreshed from frankfurter.app (free, no key)
at most once a day into data/fx.json. If the refresh fails we keep going on
the last known rates — a slightly stale rate never blocks a hunt.
"""
import json, os, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(HERE), "data", "fx.json")

FALLBACK = {  # USD per 1 unit
    "USD": 1.0, "ILS": 0.27, "EUR": 1.09, "GBP": 1.28, "SEK": 0.095,
    "DKK": 0.146, "PLN": 0.25, "TRY": 0.03, "AED": 0.272, "AUD": 0.66,
    "CAD": 0.73, "JPY": 0.0067, "CHF": 1.12, "HKD": 0.128, "CNY": 0.14,
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

def to_usd(price, currency):
    if price is None:
        return None
    return round(price * rates().get((currency or "USD").upper(), 1.0), 2)
