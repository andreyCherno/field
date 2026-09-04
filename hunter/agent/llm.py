#!/usr/bin/env python3
"""One thin door to the LLM for the whole hunter, with a hard daily spend cap.

Roles map to models (config.json > llm.models can override):
    parse  -> claude-haiku-4-5   cheap bulk work: reading a store page, extracting price/SKU
    reason -> claude-sonnet-5    judgment work: writing playbooks, ledger updates, aliases

Provider-agnostic by design: everything above this file calls complete(role, ...);
swapping Anthropic for another provider means rewriting only this file.
Requires ANTHROPIC_API_KEY in the environment. Spend is metered from real
response.usage numbers into data/llm_spend.jsonl and hard-stops at the daily cap.
"""
import json, os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
CFG = json.load(open(os.path.join(os.path.dirname(HERE), "config.json"), encoding="utf-8"))

MODELS = CFG.get("llm", {}).get("models", {
    "parse": "claude-haiku-4-5",
    "reason": "claude-sonnet-5",
})
# $ per 1M tokens (input, output) — keep in sync with anthropic.com/pricing
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
}
DAILY_CAP_USD = CFG.get("llm", {}).get("daily_cap_usd", 1.50)
SPEND_LOG = os.path.join(DATA, "llm_spend.jsonl")

_client = None
def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY
    return _client

def spent_today():
    total = 0.0
    if os.path.exists(SPEND_LOG):
        today = date.today().isoformat()
        with open(SPEND_LOG, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if row["date"] == today:
                    total += row["usd"]
    return total

class BudgetExceeded(RuntimeError):
    pass

def complete(role, system, prompt, max_tokens=2000):
    """Return the model's text answer. Raises BudgetExceeded past the daily cap."""
    if spent_today() >= DAILY_CAP_USD:
        raise BudgetExceeded(f"daily LLM cap ${DAILY_CAP_USD} reached")
    model = MODELS[role]
    # hard 30s timeout — a hunt must never hang on one model call
    resp = client().with_options(timeout=30.0, max_retries=1).messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    pin, pout = PRICES.get(model, (5.0, 25.0))
    usd = resp.usage.input_tokens / 1e6 * pin + resp.usage.output_tokens / 1e6 * pout
    os.makedirs(DATA, exist_ok=True)
    with open(SPEND_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date.today().isoformat(), "model": model,
                            "in": resp.usage.input_tokens, "out": resp.usage.output_tokens,
                            "usd": round(usd, 6)}) + "\n")
    return "".join(b.text for b in resp.content if b.type == "text")

def complete_json(role, system, prompt, max_tokens=2000):
    """complete(), then parse the reply as JSON (tolerates a ```json fence)."""
    text = complete(role, system, prompt, max_tokens).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    return json.loads(text)
