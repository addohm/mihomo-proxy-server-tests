#!/usr/bin/env python3
"""
mihomo_speedtest.py - test every server in your running mihomo, exactly as mihomo uses it.

Just run it:   python mihomo_speedtest.py

What it does
  1. Finds your running mihomo (any client: Clash Verge Rev, Mihomo Party, Nyanpasu,
     FlClash, ClashX Meta, plain mihomo...) through its external-controller API.
  2. Temporarily turns TUN off and switches mihomo to global mode.
  3. For each server: selects it, downloads and uploads through mihomo, and pings the
     server address mihomo actually connected to.
  4. Restores TUN, mode and your selection (also on Ctrl+C), prints the results and saves
     a Markdown and a CSV report.

Works on Linux, macOS and Windows. Uses curl and ping through bash or PowerShell.
While the test runs, traffic that goes through mihomo uses the server under test.

Optional arguments
  --filter REGEX   only test servers whose name matches
  --size MB        download size per server (upload is half). Default 10
  --api ADDR       controller address if it is not found automatically
                   (127.0.0.1:9090, unix:/path/to.sock or \\\\.\\pipe\\name)
  --secret TEXT    controller secret (or set MIHOMO_SECRET; asked for if needed)
  --out DIR        where to save the reports (default: next to this script)
"""
import argparse
import concurrent.futures as cf
import csv
import datetime
import getpass
import glob
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse

IS_WIN = os.name == "nt"
NULL_DEV = "NUL" if IS_WIN else "/dev/null"
PROBE_URL = "http://www.gstatic.com/generate_204"
DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
GROUP_TYPES = {"selector", "urltest", "fallback", "loadbalance", "relay", "direct", "reject",
               "rejectdrop", "pass", "passrule", "compatible", "dns", "smart"}

UNREACHABLE_NOTE = ("Note: 'unreachable' does NOT mean the server is down. The server is online (it answers "
                    "ping), but mihomo could not connect to its proxy port from this network - usually the port "
                    "or protocol is blocked on your connection, so the server cannot be used directly from here.")

# ----------------------------------------------------------------- terminal ui
if IS_WIN:
    os.system("")  # enable ANSI colours in Windows terminals
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(t, code):
    return f"\033[{code}m{t}\033[0m" if USE_COLOR else str(t)


def ok(t): return c(t, "32")
def bad(t): return c(t, "31")
def warn(t): return c(t, "33")
def dim(t): return c(t, "2")
def bold(t): return c(t, "1")


def width(s):
    w = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in "\u200d\ufe0f":
            continue
        w += 2 if unicodedata.east_asian_width(ch) in "WF" or ord(ch) >= 0x1F000 else 1
    return w


def fit(s, n, right=False):
    s = str(s)
    if width(s) > n:
        while width(s) > n - 1:
            s = s[:-1]
        s += "…"
    pad = " " * (n - width(s))
    return pad + s if right else s + pad


def status_line(text):
    if USE_COLOR:
        text = fit(text, shutil.get_terminal_size((100, 20)).columns - 1).rstrip()
        print(f"\r\033[K{text}", end="", flush=True)
    else:
        print(text, flush=True)


def clear_status():
    if USE_COLOR:
        print("\r\033[K", end="", flush=True)


def mmss(sec):
    sec = int(sec)
    return f"{sec // 60}m{sec % 60:02d}s"


# ------------------------------------------------------------- shell plumbing
def ps_exe():
    return shutil.which("pwsh") or shutil.which("powershell")


def run_shell(args, timeout=60):
    """Run an argv list through bash (Linux/macOS) or PowerShell (Windows)."""
    if IS_WIN:
        q = lambda a: "'" + str(a).replace("'", "''") + "'"
        cmd = [ps_exe(), "-NoProfile", "-NonInteractive", "-Command", "& " + " ".join(q(a) for a in args)]
    else:
        cmd = [shutil.which("bash") or "bash", "-c", " ".join(shlex.quote(str(a)) for a in args)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 127, "", str(e)


def curl(proxy, url, extra=(), max_time=20):
    """curl through mihomo. Returns (exit_code, http_code, bytes/s down, bytes/s up)."""
    args = ["curl.exe" if IS_WIN else "curl", "-sS", "-o", NULL_DEV, "-x", proxy,
            "--connect-timeout", "10", "-m", str(max_time),
            "-w", "%{http_code} %{speed_download} %{speed_upload}", *extra, url]
    rc, out, _ = run_shell(args, timeout=max_time + 15)
    p = out.split()
    try:
        return rc, int(p[0]), float(p[1]), float(p[2])
    except (IndexError, ValueError):
        return rc, 0, 0.0, 0.0


def ping(ip, count=4):
    """ICMP ping. Returns (avg_ms | None, loss_pct | None)."""
    args = ["ping", "-n", str(count), "-w", "2000", ip] if IS_WIN else ["ping", "-c", str(count), "-W", "2", ip]
    _, out, _ = run_shell(args, timeout=count * 3 + 5)
    times = [float(x) for x in re.findall(r"time[=<]\s*([\d.]+)\s*ms", out, re.I)]
    if not times and IS_WIN:  # localised Windows output
        times = [float(x) for x in re.findall(r"[=<]\s*(\d+)\s*ms", out)][:count]
    if not times:
        return None, 100.0
    return statistics.mean(times), round(100 * (1 - len(times) / count))


# -------------------------------------------------------------- prerequisites
def check_prereqs():
    rows = [("Python 3.8+", sys.version_info >= (3, 8), sys.version.split()[0], "Install Python 3.8 or newer")]
    if IS_WIN:
        rows += [("PowerShell", bool(ps_exe()), ps_exe() or "not found", "Included with Windows"),
                 ("curl.exe", bool(shutil.which("curl.exe")), shutil.which("curl.exe") or "not found",
                  "Included in Windows 10+; or: winget install cURL.cURL")]
    else:
        rows += [("bash", bool(shutil.which("bash")), shutil.which("bash") or "not found",
                  "Install bash with your package manager"),
                 ("curl", bool(shutil.which("curl")), shutil.which("curl") or "not found",
                  "sudo apt install curl | sudo dnf install curl | brew install curl")]
    rows.append(("ping", bool(shutil.which("ping")), shutil.which("ping") or "not found",
                 "sudo apt install iputils-ping | sudo dnf install iputils"))
    try:
        import yaml  # noqa: F401
        rows.append(("PyYAML", True, yaml.__version__, ""))
    except ImportError:
        rows.append(("PyYAML", None, "not installed (optional: helps find the controller secret and "
                     "ping servers that never connect)", f"{sys.executable} -m pip install pyyaml"))
    print(bold("Checking prerequisites"))
    good = True
    for name, state, detail, hint in rows:
        mark = ok("✔") if state else warn("!") if state is None else bad("✘")
        print(f"  {mark} {fit(name, 12)} {detail}")
        if state is False:
            good = False
            print(f"      {warn('→ ' + hint)}")
    return good


# ------------------------------------------------------- finding the controller
class Controller:
    """mihomo external-controller REST API over TCP, a Unix socket or a Windows named pipe."""

    def __init__(self, kind, addr, secret="", source=""):
        self.kind, self.addr, self.secret, self.source = kind, addr, secret or "", source

    def __str__(self):
        return {"tcp": f"http://{self.addr}", "unix": f"unix:{self.addr}", "pipe": self.addr}[self.kind]

    def _exchange(self, raw, timeout):
        if self.kind == "pipe":
            with open(self.addr, "r+b", buffering=0) as f:
                f.write(raw)
                chunks = []
                while True:
                    try:
                        b = f.read(65536)
                    except OSError:
                        break
                    if not b:
                        break
                    chunks.append(b)
                return b"".join(chunks)
        if self.kind == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self.addr)
        else:
            host, port = self.addr.rsplit(":", 1)
            sock = socket.create_connection((host.strip("[]"), int(port)), timeout=timeout)
        with sock:
            sock.sendall(raw)
            chunks = []
            while True:
                b = sock.recv(65536)
                if not b:
                    break
                chunks.append(b)
            return b"".join(chunks)

    def call(self, method, path, body=None, timeout=10):
        """Returns (http_status, json_or_None); status 0 = no connection."""
        data = json.dumps(body).encode() if body is not None else b""
        head = (f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\n")
        if self.secret:
            head += f"Authorization: Bearer {self.secret}\r\n"
        try:
            resp = self._exchange(head.encode() + b"\r\n" + data, timeout)
            hdr, _, payload = resp.partition(b"\r\n\r\n")
            status = int(hdr.split(b" ", 2)[1])
        except (OSError, ValueError, IndexError):
            return 0, None
        try:
            return status, json.loads(payload) if payload.strip() else None
        except ValueError:
            return status, None


def running_cores():
    """Command lines of running mihomo / clash processes."""
    if IS_WIN:
        if not ps_exe():
            return []
        cmd = [ps_exe(), "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'mihomo|clash' } | "
               "ForEach-Object { $_.CommandLine }"]
    else:
        cmd = ["bash", "-c", "ps -eo args | grep -iE 'mihomo|clash' | grep -v grep"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, errors="replace").stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def arg_value(cmdline, flag):
    m = re.search(rf"(?:^|\s){re.escape(flag)}\s+(\"[^\"]+\"|\S+)", cmdline)
    return m.group(1).strip('"') if m else None


def tcp_addr(value):
    v = re.sub(r"^https?://", "", str(value)).strip().rstrip("/")
    if ":" not in v:
        return None
    host, port = v.rsplit(":", 1)
    if host in ("", "0.0.0.0", "*", "[::]", "::"):
        host = "127.0.0.1"
    return f"{host}:{port}" if port.isdigit() else None


def load_yaml(path):
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - missing PyYAML, unreadable or invalid file
        return None


def config_files():
    """Config files of running cores first, then the usual locations of mihomo and GUI clients."""
    home = os.path.expanduser("~")
    files = []
    for cmd in running_cores():
        f, d = arg_value(cmd, "-f"), arg_value(cmd, "-d")
        files += [f] if f else []
        files += [os.path.join(d, "config.yaml")] if d else []
    env = os.environ
    bases = [env.get("XDG_CONFIG_HOME", os.path.join(home, ".config")),
             env.get("XDG_DATA_HOME", os.path.join(home, ".local", "share")),
             env.get("APPDATA", ""), env.get("LOCALAPPDATA", ""),
             os.path.join(home, "Library", "Application Support")]
    for base in filter(None, bases):
        for d in ("mihomo", "clash.meta", "clash"):
            files.append(os.path.join(base, d, "config.yaml"))
        for d in glob.glob(os.path.join(base, "*")):
            if re.search(r"clash|mihomo|flclash|nyanpasu|sparkle|koala", os.path.basename(d), re.I):
                files += glob.glob(os.path.join(d, "*.yaml")) + glob.glob(os.path.join(d, "*", "*.yaml"))
    files += ["/etc/mihomo/config.yaml", "/etc/clash/config.yaml"]
    seen, out = set(), []
    for f in files:
        f = os.path.abspath(f)
        if f not in seen and os.path.isfile(f) and os.access(f, os.R_OK):
            seen.add(f)
            out.append(f)
    return out


def find_controller(args):
    """Locate and connect to the running mihomo's API. Returns (Controller, version) or exits."""
    secret = args.secret or os.environ.get("MIHOMO_SECRET", "")
    cands = []

    def add(kind, addr, sec, source):
        if addr and all(x.addr != addr for x in cands):
            cands.append(Controller(kind, addr, secret or sec, source))

    if args.api:
        a = args.api
        if a.lower().startswith("unix:"):
            add("unix", a[5:], "", "--api")
        elif a.startswith("\\\\.\\pipe\\"):
            add("pipe", a, "", "--api")
        else:
            add("tcp", tcp_addr(a), "", "--api")
    else:
        for cmd in running_cores():  # -ext-ctl / -ext-ctl-unix / -ext-ctl-pipe / -secret
            sec = arg_value(cmd, "-secret") or ""
            for flag, kind in (("-ext-ctl-unix", "unix"), ("-ext-ctl-pipe", "pipe"), ("-ext-ctl", "tcp")):
                v = arg_value(cmd, flag)
                if v:
                    add(kind, tcp_addr(v) if kind == "tcp" else v, sec, "running process")
        for path in config_files():
            cfg = load_yaml(path)
            if not cfg:
                continue
            sec = str(cfg.get("secret") or "")
            if cfg.get("external-controller-unix"):
                add("unix", str(cfg["external-controller-unix"]), sec, path)
            if cfg.get("external-controller-pipe"):
                add("pipe", str(cfg["external-controller-pipe"]), sec, path)
            if cfg.get("external-controller"):
                add("tcp", tcp_addr(cfg["external-controller"]), sec, path)
        for p in (9090, 9097, 9091, 9095, 9898, 7900):
            add("tcp", f"127.0.0.1:{p}", "", "default port")
        if IS_WIN:
            for name in ("verge-mihomo", "mihomo", "mihomo-party"):
                add("pipe", f"\\\\.\\pipe\\{name}", "", "default pipe")

    locked = []
    for ctl in cands:
        if ctl.kind == "unix" and (IS_WIN or not os.path.exists(ctl.addr)):
            continue
        if ctl.kind == "pipe" and not IS_WIN:
            continue
        status, body = ctl.call("GET", "/version", timeout=1.5)
        if status == 200 and isinstance(body, dict) and "version" in body:
            return ctl, body["version"]
        if status == 401:
            locked.append(ctl)
    for ctl in locked:
        print(warn(f"\nFound mihomo at {ctl}, but its API needs the secret."))
        print(dim("  It is in your client's settings (external controller / API secret)."))
        if not sys.stdin.isatty():
            break
        for _ in range(3):
            ctl.secret = getpass.getpass("  Secret (hidden): ")
            if ctl.call("GET", "/version", timeout=3)[0] == 200:
                return ctl, ctl.call("GET", "/version")[1]["version"]
            print(bad("  That secret was not accepted."))
    sys.exit(bad("\nCould not reach a running mihomo.") +
             "\n  • Make sure your client (or mihomo) is running."
             "\n  • Enable its external controller, or pass --api ADDRESS (and --secret)."
             "\n    Examples: --api 127.0.0.1:9090   --api unix:/tmp/mihomo.sock   --api \\\\.\\pipe\\mihomo")


# ------------------------------------------------------------------- the test
class MihomoState:
    """Puts mihomo into test state (TUN off, global mode, a local proxy port) and restores it."""

    def __init__(self, ctl):
        self.ctl = ctl
        st, conf = ctl.call("GET", "/configs")
        if st != 200 or not isinstance(conf, dict):
            sys.exit(bad(f"mihomo API error reading /configs (HTTP {st})."))
        self.conf = conf
        self.mode = conf.get("mode", "rule")
        self.tun = bool((conf.get("tun") or {}).get("enable"))
        st, g = ctl.call("GET", "/proxies/GLOBAL")
        self.global_now = (g or {}).get("now") if st == 200 else None
        self.opened_port = None
        self.applied = False

    def proxy_url(self):
        host = str(self.conf.get("bind-address") or "*")
        host = "127.0.0.1" if host in ("*", "0.0.0.0", "::", "") else host
        for key, scheme in (("mixed-port", "socks5h"), ("socks-port", "socks5h"), ("port", "http")):
            if self.conf.get(key):
                return f"{scheme}://{host}:{self.conf[key]}"
        with socket.socket() as s:  # no local port configured: open a temporary one
            s.bind(("127.0.0.1", 0))
            self.opened_port = s.getsockname()[1]
        self.ctl.call("PATCH", "/configs", {"mixed-port": self.opened_port})
        time.sleep(1)
        return f"socks5h://127.0.0.1:{self.opened_port}"

    def apply(self):
        self.applied = True
        if self.tun:
            self.ctl.call("PATCH", "/configs", {"tun": {"enable": False}})
        self.ctl.call("PATCH", "/configs", {"mode": "global"})
        time.sleep(2)  # let routes settle after TUN goes down

    def select(self, name):
        st, _ = self.ctl.call("PUT", "/proxies/GLOBAL", {"name": name})
        return st in (200, 204)

    def restore(self):
        if not self.applied:
            return
        self.applied = False
        if self.global_now:
            self.ctl.call("PUT", "/proxies/GLOBAL", {"name": self.global_now})
        self.ctl.call("PATCH", "/configs", {"mode": self.mode})
        if self.opened_port:
            self.ctl.call("PATCH", "/configs", {"mixed-port": 0})
        if self.tun:
            self.ctl.call("PATCH", "/configs", {"tun": {"enable": True}})

    def describe(self):
        return (f"mode '{self.mode}', TUN {'on' if self.tun else 'off'}"
                + (f", GLOBAL '{self.global_now}'" if self.global_now else ""))


def list_servers(ctl):
    st, body = ctl.call("GET", "/proxies", timeout=20)
    if st != 200 or not body:
        sys.exit(bad(f"mihomo API error reading /proxies (HTTP {st})."))
    allp = body.get("proxies", {})
    is_server = lambda n: n in allp and str(allp[n].get("type", "")).lower() not in GROUP_TYPES
    order = [n for n in (allp.get("GLOBAL") or {}).get("all", []) if is_server(n)]
    order += [n for n in allp if is_server(n) and n not in order]
    return [{"name": n, "type": str(allp[n].get("type", "?")).lower()} for n in order]


def config_hosts(names):
    """name -> server host from the config file that matches the most server names (fallback only)."""
    best, want = {}, set(names)
    for path in config_files():
        cfg = load_yaml(path)
        if not cfg:
            continue
        m = {p["name"]: str(p.get("server", "")) for p in (cfg.get("proxies") or [])
             if isinstance(p, dict) and p.get("name") in want and p.get("server")}
        if len(m) > len(best):
            best = m
    return best


def mihomo_resolve(ctl, host):
    """Resolve with mihomo's own DNS (its nameserver-policy etc.)."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    st, body = ctl.call("GET", "/dns/query?" + urllib.parse.urlencode({"name": host, "type": "A"}), timeout=8)
    for a in ((body or {}).get("Answer") or []) if st == 200 else []:
        if a.get("type") == 1:
            return a.get("data")
    return None


def remote_ip(ctl, name):
    """IP mihomo actually dialed for this server, from its live connection list."""
    st, body = ctl.call("GET", "/connections", timeout=5)
    for conn in ((body or {}).get("connections") or []) if st == 200 else []:
        if name in (conn.get("chains") or []):
            rd = (conn.get("metadata") or {}).get("remoteDestination") or ""
            ip = rd.rsplit(":", 1)[0].strip("[]") if rd.count(":") == 1 or rd.startswith("[") else rd
            if ip:
                return ip
    return None


def test_server(ctl, state, proxy, srv, args, up_file):
    r = {"name": srv["name"], "type": srv["type"], "ip": None, "ping": None, "loss": None,
         "down": None, "up": None, "result": "", "note": ""}
    if not state.select(srv["name"]):
        r["result"], r["note"] = "failed", "mihomo would not select this server"
        return r
    # warm-up: the first connection through a newly selected server can take a few seconds
    deadline, rc = time.time() + 10, -1
    while time.time() < deadline:
        rc, code, _, _ = curl(proxy, PROBE_URL, max_time=8)
        if code in (200, 204):
            break
        time.sleep(1)
    reached = rc == 0
    # download, while reading mihomo's connection list for the server address it dialed
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(curl, proxy, DOWN_URL.format(n=int(args.size * 1e6)), (), args.timeout)
        while not fut.done():
            r["ip"] = r["ip"] or remote_ip(ctl, srv["name"])
            time.sleep(0.5)
        drc, dcode, dspeed, _ = fut.result()
    if dcode == 200 and dspeed > 0:
        r["down"] = dspeed * 8 / 1e6
        if drc == 28:
            r["note"] = "slow: download hit the time limit"
    for _ in range(2 if reached else 1):  # one retry for a working server
        urc, ucode, _, uspeed = curl(proxy, UP_URL, ["-H", "Expect:", "--data-binary", f"@{up_file}"],
                                     args.timeout)
        if (ucode in (200, 201, 204) or urc == 28) and uspeed > 0:
            r["up"] = uspeed * 8 / 1e6
            if urc == 28:
                r["note"] = (r["note"] + "; " if r["note"] else "") + "slow: upload hit the time limit"
            break
    if r["down"] is not None and r["up"] is not None:
        r["result"] = "ok"
    elif r["down"] is None and r["up"] is None:
        r["result"] = "failed"
        r["note"] = ("mihomo could not connect through this server" if drc not in (0, 28)
                     else "timed out" if drc == 28 else f"HTTP {dcode}")
    else:
        r["result"] = "partial"
        r["note"] = ("upload failed" if r["up"] is None else "download failed") + \
                    (f"; {r['note']}" if r["note"] else "")
    return r


# ---------------------------------------------------------------------- output
COLS = [("#", 3, True), ("Server", 34, False), ("Type", 10, False), ("Ping ms", 8, True),
        ("Loss %", 7, True), ("Down Mbps", 10, True), ("Up Mbps", 8, True), ("Result", 11, False)]


def fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def cells(i, r):
    return [str(i), r["name"], r["type"], fmt(r["ping"]), fmt(r["loss"], 0), fmt(r["down"]), fmt(r["up"]),
            r["result"]]


def print_table(results):
    print("\n" + bold(" ".join(fit(h, w, rt) for h, w, rt in COLS)))
    print(dim(" ".join("─" * w for _, w, _ in COLS)))
    for i, r in enumerate(results, 1):
        vals = cells(i, r)
        line = " ".join(fit(v, w, rt) for v, (_, w, rt) in zip(vals[:-1], COLS[:-1]))
        colour = {"ok": ok, "partial": warn, "unreachable": warn}.get(r["result"], bad)
        print(f"{line} {colour(fit(r['result'], COLS[-1][1]))}" + (dim(f" {r['note']}") if r["note"] else ""))


def save_reports(results, meta, out_dir):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for d in (out_dir, os.getcwd(), tempfile.gettempdir()):
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            out_dir = d
            break
    md_path = os.path.join(out_dir, f"mihomo-speedtest-{stamp}.md")
    csv_path = os.path.join(out_dir, f"mihomo-speedtest-{stamp}.csv")
    lines = ["# mihomo server test", "",
             f"- Date: {meta['date']}", f"- mihomo: {meta['version']} ({meta['api']})",
             f"- Download size: {meta['size']:g} MB, upload {meta['size'] / 2:g} MB",
             f"- Servers: {meta['total']}, ok: {meta['ok']}, partial: {meta['partial']}, "
             f"unreachable: {meta['unreachable']}, failed: {meta['failed']}",
             f"- Runtime: {meta['runtime']}", "",
             "| # | Server | Type | Ping ms | Loss % | Down Mbps | Up Mbps | Result | Note |",
             "|---:|---|---|---:|---:|---:|---:|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append("| " + " | ".join(v.replace("|", "\\|") for v in cells(i, r) + [r["note"]]) + " |")
    if meta["unreachable"]:
        lines += ["", f"> {UNREACHABLE_NOTE}"]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel opens UTF-8 names
        w = csv.writer(f)
        w.writerow(["server", "type", "ping_ms", "loss_pct", "download_mbps", "upload_mbps", "result", "note"])
        for r in results:
            w.writerow([r["name"], r["type"], "" if r["ping"] is None else round(r["ping"], 1),
                        "" if r["loss"] is None else r["loss"],
                        "" if r["down"] is None else round(r["down"], 2),
                        "" if r["up"] is None else round(r["up"], 2), r["result"], r["note"]])
    return md_path, csv_path


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Test every server in your running mihomo.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--filter", help="only test servers whose name matches this regex")
    ap.add_argument("--size", type=float, default=10, help="download MB per server (upload is half). Default 10")
    ap.add_argument("--timeout", type=int, default=25, help="max seconds per transfer. Default 25")
    ap.add_argument("--api", help="controller address, if not found automatically")
    ap.add_argument("--secret", default="", help="controller secret (or MIHOMO_SECRET)")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)), help="report folder")
    args = ap.parse_args()

    print(bold("mihomo server speed test\n"))
    if not check_prereqs():
        sys.exit(bad("\nPlease install what is missing above, then run again."))

    ctl, version = find_controller(args)
    print(f"\nConnected to mihomo {version} at {ctl}")
    servers = list_servers(ctl)
    if args.filter:
        rx = re.compile(args.filter, re.I)
        servers = [s for s in servers if rx.search(s["name"])]
    if not servers:
        sys.exit(bad("mihomo reports no servers to test."))
    state = MihomoState(ctl)
    est = len(servers) * (6 + 1.4 * args.size)  # ~20 s per server at 10 MB, incl. failing ones
    print(f"Servers to test: {bold(len(servers))}   estimated time: ~{mmss(est)}")
    print(dim(f"Current state: {state.describe()}. During the test TUN is off and mihomo is in global "
              "mode;\nyour settings are restored at the end (also on Ctrl+C)."))

    def stop(*_):
        raise KeyboardInterrupt
    for sig in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), stop)
            except (OSError, ValueError):
                pass

    results, t0 = [], time.time()
    tmp = tempfile.mkdtemp(prefix="mihomo-st-")
    up_file = os.path.join(tmp, "upload.bin")
    with open(up_file, "wb") as f:
        f.write(os.urandom(int(args.size * 500_000)))
    pinger = cf.ThreadPoolExecutor(max_workers=8)
    pings = {}
    try:
        proxy = state.proxy_url()
        state.apply()
        for i, srv in enumerate(servers, 1):
            left = (time.time() - t0) / (i - 1) * (len(servers) - i + 1) if i > 1 else est
            status_line(f"[{i}/{len(servers)}] {srv['name']}   ~{mmss(left)} left")
            r = test_server(ctl, state, proxy, srv, args, up_file)
            results.append(r)
            if r["ip"]:
                pings[r["name"]] = pinger.submit(ping, r["ip"])
        # servers mihomo never connected to: ping the address from the config, resolved by mihomo
        missing = [r for r in results if r["name"] not in pings]
        hosts = config_hosts([r["name"] for r in missing]) if missing else {}
        for r in missing:
            ip = mihomo_resolve(ctl, hosts[r["name"]]) if r["name"] in hosts else None
            if ip:
                pings[r["name"]] = pinger.submit(ping, ip)
        status_line("Finishing pings...")
        for r in results:
            if r["name"] in pings:
                r["ping"], r["loss"] = pings[r["name"]].result()
                if r["result"] == "failed" and r["ping"] is not None:
                    r["result"] = "unreachable"
                    r["note"] = "server online (answers ping), but its proxy port can't be reached from this network"
        clear_status()
    except KeyboardInterrupt:
        clear_status()
        print(warn("\nStopped - showing the servers tested so far."))
    finally:
        state.restore()
        pinger.shutdown(wait=False)
        shutil.rmtree(tmp, ignore_errors=True)
    print(dim(f"\nmihomo restored: {state.describe()}."))

    if not results:
        return
    print_table(results)
    n = {k: sum(r["result"] == k for r in results) for k in ("ok", "partial", "unreachable", "failed")}
    runtime = mmss(time.time() - t0)
    print(f"\n{ok(n['ok'])} ok, {warn(n['partial'])} partial, {warn(n['unreachable'])} unreachable, "
          f"{bad(n['failed'])} failed of {len(results)} "
          f"servers in {runtime}")
    if n["unreachable"]:
        print(warn("\n" + UNREACHABLE_NOTE))
    fast = [r for r in results if r["down"]]
    if fast:
        b = max(fast, key=lambda r: r["down"])
        print(f"Fastest download: {bold(b['name'])} ({b['down']:.1f} Mbps)")
    meta = {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "version": version, "api": str(ctl),
            "size": args.size, "runtime": runtime, "total": len(results), **n}
    md, cs = save_reports(results, meta, args.out)
    print(f"\nReports saved:\n  {md}\n  {cs}")


if __name__ == "__main__":
    main()
