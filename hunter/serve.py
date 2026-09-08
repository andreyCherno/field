#!/usr/bin/env python3
"""The hunter's local control panel server. Run from the repo root or hunter/:

    python3 hunter/serve.py         # serves the whole site on :8000

A hunt is two steps on purpose:

    GET  /api/identify?q=...        what do you mean? -> a card you confirm
    GET  /api/hunt/stream?identity= go hunt, streaming one store at a time
    GET  /api/hunt/stop             abort the running hunt now

Nothing is searched until the identity is confirmed, because a hunt asks ~120
shops three questions each and the cost of getting the item wrong is 360 wrong
questions followed by a gallery of the wrong shoe.

Static files come from the repo root, so /hunter/, /shelf/ and the rest all
work like before. ANTHROPIC_API_KEY is only needed when the shelf catalogue
cannot identify the query on its own.
"""
import glob, json, os, sys, threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

VERSION = 6   # bump on every server change; the UI warns when it sees a stale server
lock = threading.Lock()          # one hunt at a time — the stores thank us
stop_flag = threading.Event()    # set by /api/hunt/stop; the hunt checks it constantly


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

    def query(self):
        return parse_qs(urlparse(self.path).query)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/version":
            try:
                from agent import browser, catalog
                pw, shelf = browser.available(), len(catalog.items())
            except Exception:
                pw, shelf = False, 0
            return self.send_json({"version": VERSION, "browser": pw, "shelf": shelf,
                                   "api_key": bool(os.environ.get("ANTHROPIC_API_KEY"))})
        if path == "/api/identify":
            q = (self.query().get("q") or [""])[0].strip()
            if not q:
                return self.send_json({"error": "empty query"}, 400)
            try:
                from agent.identify import proposal
                return self.send_json(proposal(q))
            except Exception as e:
                return self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if path == "/api/hunt/stop":
            stop_flag.set()
            return self.send_json({"stopping": True})
        if path == "/api/hunt/stream":
            return self.stream_hunt()
        if path == "/api/config":
            return self.send_json(json.load(open(os.path.join(HERE, "config.json"),
                                                 encoding="utf-8")))
        if path == "/api/stores":
            stores = []
            for p in sorted(glob.glob(os.path.join(HERE, "playbooks", "*.json"))):
                if os.path.basename(p).startswith("_"):
                    continue
                pb = json.load(open(p, encoding="utf-8"))
                stores.append({"domain": pb["domain"], "method": pb.get("method"),
                               "stats": pb.get("stats", {})})
            return self.send_json(stores)
        return super().do_GET()

    EDITABLE = {"sizes", "budget_caps_usd", "min_discount", "top_n", "match"}

    def stream_hunt(self):
        """Server-sent events: one event per store the moment it answers, each
        offer already walked into and read off its own product page."""
        qs = self.query()
        deep = (qs.get("deep") or ["0"])[0] == "1"
        headed = (qs.get("headed") or ["0"])[0] == "1"
        skip = [d for d in (qs.get("skip") or [""])[0].split(",") if d]
        raw_identity = (qs.get("identity") or [""])[0]
        query = (qs.get("q") or [""])[0].strip()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(obj):
            try:
                self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False   # browser left — hunt keeps quietly finishing

        class ClientGone(Exception):
            pass

        try:
            from agent.identify import identify, save_alias
            from agent.search import hunt as run_search
            from agent.fx import to_usd
            from agent import catalog, inspect
            from hunt import publish, landed, landed_detail

            if raw_identity:
                identity = json.loads(raw_identity)      # the card you confirmed
                identity.setdefault("query", query or identity.get("product") or "")
                item = (qs.get("item") or [""])[0].strip()
                if item:
                    identity["shelf_item"] = item          # write the answer back to this piece
            elif query:
                identity = identify(query)               # unconfirmed: CLI-style
            else:
                return emit({"type": "error", "error": "empty query"})

            # a previous hunt may still be finishing — say so instead of hanging
            if not lock.acquire(timeout=0.1):
                emit({"type": "status", "message": "ציד קודם עדיין מסיים — ממתין לתור…"})
                lock.acquire()
            stopped = False
            try:
                stop_flag.clear()
                from agent import browser as _b
                _b.FORCE_HEADED = headed          # this hunt only
                if raw_identity and identity.get("query"):
                    try:
                        save_alias(identity["query"], identity)   # never ask twice
                    except Exception:
                        pass
                if not emit({"type": "identity", "identity": identity}):
                    raise ClientGone()
                all_offers, off_total = [], 0

                def on_store(row, store_offers, limit=inspect.PER_STORE):
                    nonlocal off_total
                    kept, off = inspect.confirm_store(
                        store_offers, identity, should_stop=stop_flag.is_set, limit=limit)
                    for o in kept:
                        if o.get("price"):
                            o["usd"] = to_usd(o["price"], o.get("currency"))
                            d = landed_detail(o)
                            o["landed"] = d["total"]
                            o["landed_lines"] = d["lines"]
                            o["landed_confidence"] = d["confidence"]
                            o["landed_note"] = d["note"]
                    off_total += off
                    row["off_target"] = off
                    row["hits"] = sum(1 for o in kept if o.get("price"))
                    manual = [o for o in kept if o.get("manual")]
                    row["manual"] = len(manual)
                    if manual:            # why the shop could not be read
                        row["why"] = manual[0].get("why")
                    all_offers.extend(kept)
                    if not emit({"type": "store", "report": row, "offers": kept}):
                        raise ClientGone()   # browser left — stop hunting, free the lock

                # your own shelf first: 1,334 of its items already carry a
                # cross-retailer `sellers` list, so those leads cost nothing to
                # find. They still get walked into like any other lead.
                seed = catalog.seed_offers(identity)
                if seed and not stop_flag.is_set():
                    on_store({"store": f"המדף שלך ({len(seed)} מוכרים ידועים)",
                              "method": "shelf", "hits": 0, "error": None},
                             seed, limit=len(seed))
                    skip = list(skip) + [urlparse("https://" + (o["url"].split("//")[-1]))
                                         .netloc.replace("www.", "") for o in seed]

                run_search(identity, deep=deep, skip=skip, on_store=on_store,
                           should_stop=stop_flag.is_set)
                stopped = stop_flag.is_set()
                page = publish(identity, all_offers)
            finally:
                stop_flag.clear()
                _b.FORCE_HEADED = False
                lock.release()
            emit({"type": "done", "stopped": stopped, "off_target": off_total,
                  "page": "/hunter/items/" + os.path.basename(page),
                  "priced": len([o for o in all_offers if o.get("price")])})
        except ClientGone:
            pass
        except Exception as e:
            emit({"type": "error", "error": f"{type(e).__name__}: {e}"})

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
        return self.send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):   # keep the terminal quiet
        # a 404's first arg is an HTTPStatus, not a string — it used to raise
        # a TypeError into the log on every missing favicon
        if "/api/" in str(args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"hunter control panel: http://localhost:{port}/hunter/")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("note: ANTHROPIC_API_KEY not set — the shelf catalogue still identifies items")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
