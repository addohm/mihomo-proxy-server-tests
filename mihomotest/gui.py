"""--gui: the same test as a local web page in the user's normal browser. A small stdlib web server on
127.0.0.1 with a random token per launch (other websites can't drive it); the engine is the CLI's."""
import datetime
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser

from .mihomo import MihomoError, SecretNeeded, find_controller, list_servers
from .report import BASELINE, LEGEND, finish, load_report, mmss, notes, reports_dir, summary
from .tests import RESULTS, WORKING, counts, run_tests

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui.html")
FIELDS = ("name", "type", "conn", "conn_jitter", "ping", "ping_jitter", "loss", "down", "up", "first", "result",
          "note", "error", "check", "trace", "trace_hops")


class App:
    def __init__(self, args):
        self.args, self.token = args, secrets.token_urlsafe(18)
        self.out = args.out
        self.lock = threading.Lock()
        self.quit = threading.Event()
        self.ctl, self.version, self.servers, self.conn_error, self.need_secret = None, "", [], "", False
        self.run, self.worker, self.state, self.stop = None, None, None, threading.Event()
        self.connect(args.api, args.secret)

    def connect(self, api, secret):
        self.need_secret, self.conn_error = False, ""
        try:
            self.ctl, self.version = find_controller(api or None, secret or "")
            self.servers = list_servers(self.ctl)
        except SecretNeeded:
            self.ctl, self.need_secret = None, True
            self.conn_error = "mihomo's controller needs its secret (see your client's settings)."
        except MihomoError as e:
            self.ctl, self.conn_error = None, str(e)

    def snapshot(self):
        with self.lock:
            return {"connected": bool(self.ctl), "controller": str(self.ctl) if self.ctl else "",
                    "version": self.version, "error": self.conn_error, "need_secret": self.need_secret,
                    "servers": [{"name": s["name"], "type": s["type"], "selectable": bool(s.get("path"))}
                                for s in self.servers],
                    "out": self.out, "baseline": os.path.isfile(os.path.join(self.out, BASELINE)),
                    "legend": LEGEND, "run": self.run}

    def start(self, opts):
        with self.lock:
            if not self.ctl or (self.worker and self.worker.is_alive()):
                return "not connected" if not self.ctl else "a test is already running"
            chosen = set(opts.get("servers") or [])
            servers = [s for s in self.servers if s["name"] in chosen]
            if not servers:
                return "choose at least one server"
            base = None
            if opts.get("compare"):
                path = os.path.join(self.out, BASELINE)
                if not os.path.isfile(path):
                    return "there is no baseline yet: run once with 'Save as baseline'"
                base = (path, *load_report(path))
            self.stop.clear()
            self.run = {"running": True, "phase": "starting", "index": 0, "total": len(servers), "current": "",
                        "left": None, "results": [], "summary": "", "meanings": [], "comparison": None,
                        "reports": None, "error": "", "stopped": False, "started": time.time(), "runtime": ""}
            self.worker = threading.Thread(target=self._run, args=(servers, opts, base), name="speedtest")
            self.worker.start()
        return ""

    def _event(self, kind, data):
        with self.lock:
            run = self.run
            if kind == "start":
                self.state = data["state"]
                run["phase"] = "testing"
            elif kind == "server":
                run.update(phase="testing", index=data["index"], current=data["server"]["name"], left=data["left"])
            elif kind == "result":
                run["results"].append(public(data["result"]))
            elif kind in ("pinging", "tracing"):
                run["phase"] = kind
            elif kind == "stopped":
                run.update(phase="restoring", stopped=True)
            elif kind == "restored":
                run["phase"] = "restored"

    def _run(self, servers, opts, base):
        t0 = time.time()
        try:
            results, stopped = run_tests(self.ctl, servers, float(opts.get("size") or 10), int(opts.get("timeout") or 25),
                                         self._event, trace=bool(opts.get("trace")), should_stop=self.stop.is_set)
            n = counts(results)
            meta = {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "version": self.version,
                    "api": str(self.ctl), "size": float(opts.get("size") or 10), "runtime": mmss(time.time() - t0),
                    "total": len(results), "counts": n, "meanings": RESULTS}
            done = finish(results, meta, self.out, base, [s["name"] for s in self.servers],
                          bool(opts.get("save_baseline"))) if results else None
            with self.lock:
                self.run.update(results=[public(r) for r in results], summary=summary(n, len(results)),
                                meanings=notes(n, RESULTS), runtime=meta["runtime"], stopped=stopped,
                                comparison=done and done["comparison"],
                                reports=done and {k: done[k] for k in ("md", "csv", "baseline", "tip")})
        except MihomoError as e:
            with self.lock:
                self.run["error"] = str(e)
        except Exception as e:  # noqa: BLE001 - shown on the page instead of a silent dead thread
            with self.lock:
                self.run["error"] = f"{type(e).__name__}: {e}"
        finally:
            with self.lock:
                self.run.update(running=False, phase="done")

    def stop_now(self):
        """Stop the run (after the current server) and put mihomo back at once (it's safe twice)."""
        self.stop.set()
        if self.state and self.worker and self.worker.is_alive():
            self.state.restore()

    def reports(self):
        try:
            names = sorted((f for f in os.listdir(self.out) if f.startswith("mihomo-speedtest-") and f.endswith(".md")),
                           reverse=True)
        except OSError:
            names = []
        return [{"name": f, "date": f[17:25] + " " + f[26:32]} for f in names[:50]]

    def report(self, name):
        name = os.path.basename(name)
        if not (name.startswith("mihomo-speedtest-") and name.endswith(".md")):
            return None
        try:
            with open(os.path.join(self.out, name), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None


def public(r):
    d = {k: r.get(k) for k in FIELDS}
    d["meaning"] = RESULTS.get(r.get("result"), "")
    d["working"] = r.get("result") in WORKING
    return d


class Handler(http.server.BaseHTTPRequestHandler):
    app = None  # set by serve()

    def log_message(self, *_):
        pass

    def _ok_host(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")  # a page from elsewhere can't rebind a name to us

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self, query):
        token = self.headers.get("X-Token") or (query.get("t") or [""])[0]
        return self._ok_host() and secrets.compare_digest(token, self.app.token)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if not self._authorised(q):
            return self._send(403, "Open the address printed by mihomo_speedtest.py (it holds the key).", "text/plain")
        if url.path == "/":
            with open(PAGE, encoding="utf-8") as f:
                return self._send(200, f.read().replace("{{TOKEN}}", self.app.token), "text/html")
        if url.path == "/api/state":
            return self._send(200, self.app.snapshot())
        if url.path == "/api/reports":
            return self._send(200, self.app.reports())
        if url.path == "/api/report":
            text = self.app.report((q.get("name") or [""])[0])
            return self._send(200, text, "text/plain") if text is not None else self._send(404, {"error": "no such report"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if not self._authorised({}):  # POSTs need the header: a form on another site can't set it
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad request"})
        if url.path == "/api/connect":
            if self.app.worker and self.app.worker.is_alive():
                return self._send(409, {"error": "a test is running"})
            self.app.connect(body.get("api", ""), body.get("secret", ""))
            return self._send(200, self.app.snapshot())
        if url.path == "/api/run":
            err = self.app.start(body)
            return self._send(409 if err else 200, {"error": err})
        if url.path == "/api/stop":
            self.app.stop_now()
            return self._send(200, {"error": ""})
        if url.path == "/api/quit":
            self.app.quit.set()
            return self._send(200, {"error": ""})
        self._send(404, {"error": "not found"})


def serve(args, port=0):
    """Run the page until Quit, Ctrl+C or the window closes; mihomo is restored if a test is running."""
    app = App(args)
    Handler.app = app
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={app.token}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"The test runs in your browser. If it doesn't open by itself, open:\n  {url}\n"
          "Close this window (or press Ctrl+C) to quit.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while not app.quit.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        if app.worker and app.worker.is_alive():
            print("Stopping the test and restoring your settings...")
            app.stop_now()
            app.worker.join(90)
        httpd.shutdown()
