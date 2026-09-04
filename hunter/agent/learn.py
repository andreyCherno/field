#!/usr/bin/env python3
"""The self-improvement pass. Run nightly or weekly (cheap; uses the reason model):

    python3 -m agent.learn

Reads data/attempts.jsonl — every search attempt with its store, query form,
hit count and errors — aggregates per store, and asks the LLM to rewrite each
store's playbook where the evidence says the current one underperforms:
wrong method, queries that never hit, an accepts_sku flag that is wrong,
a better tip. Also updates stats and promotes successful query rewrites into
aliases.json so identify.py answers them for free next time.
Playbooks are files in git — every "lesson" is a reviewable diff.
"""
import glob, json, os
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ATTEMPTS = os.path.join(ROOT, "data", "attempts.jsonl")

def load_attempts():
    per_store = defaultdict(list)
    if os.path.exists(ATTEMPTS):
        with open(ATTEMPTS, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                per_store[row["store"]].append(row)
    return per_store

def main():
    from agent import llm
    per_store = load_attempts()
    if not per_store:
        print("no attempts logged yet — nothing to learn from")
        return
    for path in glob.glob(os.path.join(ROOT, "playbooks", "*.json")):
        if os.path.basename(path).startswith("_"):
            continue
        pb = json.load(open(path, encoding="utf-8"))
        rows = per_store.get(pb["domain"])
        if not rows:
            continue
        hits = sum(r["hits"] for r in rows)
        errs = sum(1 for r in rows if r.get("error"))
        pb.setdefault("stats", {})
        pb["stats"].update(attempts=pb["stats"].get("attempts", 0) + len(rows),
                           hits=pb["stats"].get("hits", 0) + hits)
        if hits:
            pb["stats"]["last_success"] = date.today().isoformat()
        # only spend LLM tokens on stores that clearly struggle
        if len(rows) >= 5 and (hits == 0 or errs / len(rows) > 0.5):
            sample = json.dumps(rows[-20:], ensure_ascii=False)
            try:
                revised = llm.complete_json("reason",
                    "You maintain per-store search playbooks for a price-hunting"
                    " agent. Given the current playbook and a log of recent"
                    " attempts (query used, hits, errors), return the improved"
                    " playbook as JSON with the same schema. Change method,"
                    " search_url, accepts_sku or query_tips only when the log"
                    " supports it; keep the domain and stats untouched.",
                    f"Current playbook:\n{json.dumps(pb, ensure_ascii=False)}\n\n"
                    f"Recent attempts:\n{sample}")
                revised["domain"], revised["stats"] = pb["domain"], pb["stats"]
                pb = revised
                print(f"  {pb['domain']}: playbook revised")
            except llm.BudgetExceeded:
                print("  budget cap reached — stopping LLM revisions")
                break
            except Exception as e:
                print(f"  {pb['domain']}: revision failed ({type(e).__name__})")
        json.dump(pb, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # rotate the log so each learning pass sees fresh evidence
    done = ATTEMPTS + "." + date.today().isoformat()
    if os.path.exists(ATTEMPTS):
        os.replace(ATTEMPTS, done)
    print("learning pass done; attempts log rotated")

if __name__ == "__main__":
    main()
