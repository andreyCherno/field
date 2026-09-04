#!/usr/bin/env python3
"""Bundle the worker: inject ../playbooks/*.json and ui.html into worker.js
-> dist/worker.js, ready for `wrangler deploy`. Run after any playbook change.
"""
import glob, json, os

HERE = os.path.dirname(os.path.abspath(__file__))

playbooks = []
for path in sorted(glob.glob(os.path.join(HERE, "..", "playbooks", "*.json"))):
    if os.path.basename(path).startswith("_"):
        continue
    pb = json.load(open(path, encoding="utf-8"))
    pb.pop("stats", None)   # runtime stats stay local, not in the product
    playbooks.append(pb)

ui = open(os.path.join(HERE, "ui.html"), encoding="utf-8").read()
src = open(os.path.join(HERE, "worker.js"), encoding="utf-8").read()
src = src.replace("/*PLAYBOOKS*/[]", json.dumps(playbooks, ensure_ascii=False))
src = src.replace('/*UI*/""', json.dumps(ui, ensure_ascii=False))

os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
out = os.path.join(HERE, "dist", "worker.js")
open(out, "w", encoding="utf-8").write(src)
print(f"built {out}: {len(playbooks)} playbooks, {len(src)//1024}KB")
