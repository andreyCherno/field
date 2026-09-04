#!/usr/bin/env python3
"""The hunter's local control panel server. Run from the repo root or hunter/:

    python3 hunter/serve.py         # serves the whole site on :8000
                                    # POST /api/hunt runs a hunt from the browser
                                    # GET  /api/stores lists the playbooks

Static files come from the repo root, so /hunter/, /shelf/ and the rest all
work like before — plus the API the hunter UI uses to run hunts without a
terminal. Needs ANTHROPIC_API_KEY in the environment for name identification
and deep parsing (SKU-shaped hunts work without it).
"""
import glob, json, os, sys, threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

lock = threading.Lock()   # one hunt at a time — the stores thank us

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/config":
            return self.send_json(json.load(open(os.path.join(HERE, "config.json"),
                                                 encoding="utf-8")))
        if self.path == "/api/stores":
            stores = []
            for p in sorted(glob.glob(os.path.join(HERE, "playbooks", "*.json"))):
                if os.path.basename(p).startswith("_"):
                    continue
                pb = json.load(open(p, encoding="utf-8"))
                stores.append({"domain": pb["domain"], "method": pb.get("method"),
                               "stats": pb.get("stats", {})})
            return self.send_json(stores)
        return super().do_GET()

    EDITABLE = {"sizes", "budget_caps_usd", "min_discount", "top_n"}

    def do_POST(self):
        if self.path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            changes = json.loads(self.rfile.read(length))
            path = os.path.join(HERE, "config.json")
            cfg = json.load(open(path, encoding="utf-8"))
            cfg.update({k: v for k, v in changes.items() if k in self.EDITABLE})
            json.dump(cfg, open(path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            return self.send_json(cfg)
        if self.path != "/api/hunt":
            return self.send_json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length))
            query, deep = req.get("query", "").strip(), bool(req.get("deep"))
            if not query:
                return self.send_json({"error": "empty query"}, 400)
            from agent.identify import identify
            from agent.search import hunt as run_search
            from agent.verify import verify_all
            from hunt import publish, landed
            with lock:
                identity = identify(query)
                report = []
                offers = verify_all(run_search(identity, deep=deep, report=report))
                page = publish(identity, offers)
            for o in offers:
                if o.get("price"):
                    o["landed"] = landed(o["price"])
            offers.sort(key=lambda o: o.get("landed") or 1e9)
            self.send_json({"identity": identity, "offers": offers, "report": report,
                            "page": "/hunter/items/" + os.path.basename(page)})
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def log_message(self, fmt, *args):   # keep the terminal quiet
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"hunter control panel: http://localhost:{port}/hunter/")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("note: ANTHROPIC_API_KEY not set — name identification and deep search are off")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
