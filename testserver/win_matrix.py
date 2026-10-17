"""Windows tester: run the speed test against config variants of the PC's running mihomo client and check it.

    py -3.13 win_matrix.py C:\\path\\to\\mihomo_speedtest.py [--python PY.EXE] [--filter REGEX] [--only NAME ...]

The Windows counterpart of matrix.py. The client (e.g. Clash Verge Rev) keeps running: each variant is a
copy of the core's own config (its -f file, or config.yaml in its -d home) with one change, written next to it and loaded through the API
(PUT /configs); the original file is loaded back at the end, also after an error or Ctrl+C. The client's own
settings are never touched. Variants:
    default      the config as the client wrote it (controller over its named pipe)
    secret       a secret in the config (mihomo doesn't check it on a named pipe: this checks it doesn't
                 get in the way)
    socks/http   socks-port or port instead of mixed-port
    noport       no local proxy port: the test must open and close a temporary one
    provider     servers loaded from a file proxy provider instead of the config
Before each variant: mode rule, GLOBAL on the first chosen server. After the test, mode, TUN, local ports
and every selector group's selection must be exactly as before; it must have found mihomo and tested every
chosen server. Left out: TUN (on a tester PC it would carry the tunnel itself), and a TCP controller
(mihomo only opens one at start-up, not when a config is loaded through the API; that code is the same on
every OS and covered by matrix.py on Linux). Clients with a TCP controller (e.g. Clash Nyanpasu) work too,
but skip "secret": the controller keeps its start-up secret through a config load.
Needs PyYAML (py -3.13 on the tester PC); --python picks the Python that runs the speed test.
"""
import argparse
import copy
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

import yaml

GROUP_TYPES = {"selector", "urltest", "fallback", "loadbalance", "relay", "direct", "reject",
               "rejectdrop", "pass", "passrule", "compatible", "dns", "smart"}
VARIANTS = ["default", "secret", "socks", "http", "noport", "provider"]


class Core:
    """The running mihomo: its controller (named pipe, or else the TCP external-controller in its config),
    config file and home directory, from its command line."""

    def __init__(self):
        ps = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Where-Object { $_.Name "
              "-match 'mihomo' } | ForEach-Object { $_.CommandLine }"]
        cmd = subprocess.run(ps, capture_output=True, text=True).stdout
        arg = lambda flag: (re.search(rf"{flag}\s+(\"[^\"]+\"|\S+)", cmd) or [None, None])[1]
        self.pipe, self.home = arg("-ext-ctl-pipe"), arg("-d")
        if not self.home:
            sys.exit(f"no mihomo with -d found:\n{cmd}")
        self.home = self.home.strip('"')
        # without -f mihomo reads config.yaml in its home directory (e.g. Clash Party)
        self.config = (arg("-f") or os.path.join(self.home, "config.yaml")).strip('"')
        self.original = yaml.safe_load(open(self.config, encoding="utf-8"))
        self.secret = str(self.original.get("secret") or "")
        self.tcp = None if self.pipe else str(self.original.get("external-controller") or "").replace("0.0.0.0", "127.0.0.1")
        if not (self.pipe or self.tcp):
            sys.exit("the core has neither -ext-ctl-pipe nor an external-controller in its config")

    def _open(self):
        if self.pipe:
            return open(self.pipe, "r+b", buffering=0)
        host, port = self.tcp.rsplit(":", 1)
        return socket.create_connection((host, int(port)), timeout=20).makefile("rwb", buffering=0)

    def api(self, method, path, body=None, timeout=20):
        """HTTP/1.0 over the pipe or TCP, independent of the code under test. Returns (status, JSON or None)."""
        data = json.dumps(body).encode() if body is not None else b""
        head = (f"{method} {path} HTTP/1.0\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\n" + (f"Authorization: Bearer {self.secret}\r\n" if self.secret else ""))
        end = time.time() + timeout
        while True:
            try:
                f = self._open()
                break
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.1)
        with f:
            f.write(head.encode() + b"\r\n" + data)
            chunks = []
            while True:
                try:
                    b = f.read(65536)
                except OSError:
                    break
                if not b:
                    break
                chunks.append(b)
        hdr, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        status = int(hdr.split(b" ", 2)[1]) if hdr else 0
        try:
            return status, json.loads(payload) if payload.strip() else None
        except ValueError:
            return status, None

    def load(self, path, secret):
        """Make mihomo load a config file; the secret is the one that file sets."""
        st, body = self.api("PUT", "/configs?force=true", {"path": path})
        if st not in (200, 204):
            raise SystemExit(f"mihomo refused {path}: HTTP {st} {body}")
        old = self.secret
        for _ in range(30):
            for self.secret in (secret, old):  # a TCP controller keeps its start-up secret through a load
                if (self.api("GET", "/version")[1] or {}).get("version"):
                    return
            time.sleep(1)
        raise SystemExit(f"mihomo's API did not come back after loading {path}")

    def snapshot(self):
        c = self.api("GET", "/configs")[1] or {}
        p = (self.api("GET", "/proxies")[1] or {}).get("proxies", {})
        return {"mode": c.get("mode"), "tun": bool((c.get("tun") or {}).get("enable")),
                "mixed-port": c.get("mixed-port", 0), "socks-port": c.get("socks-port", 0), "port": c.get("port", 0),
                "selections": {n: v.get("now") for n, v in p.items() if str(v.get("type", "")).lower() == "selector"}}


def build(core, name):
    """The variant's config, its file path, its secret and extra speed-test arguments."""
    cfg = copy.deepcopy(core.original)
    args = []
    if name == "secret":
        cfg["secret"] = secrets.token_hex(12)
        if core.tcp:  # the test reads the secret from the core's own -f file, not from this variant's copy
            args = ["--secret", cfg["secret"]]
    elif name in ("socks", "http", "noport"):
        cfg.pop("mixed-port", None)
        if name != "noport":
            cfg["socks-port" if name == "socks" else "port"] = 7898
    elif name == "provider":
        path = os.path.join(core.home, "mst-provider.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"proxies": cfg.pop("proxies", [])}, f, allow_unicode=True, sort_keys=False)
        cfg.pop("proxy-providers", None)
        cfg["proxy-providers"] = {"mst": {"type": "file", "path": "./mst-provider.yaml", "health-check": {"enable": False}}}
        cfg["proxy-groups"] = [{"name": "PROXY", "type": "select", "use": ["mst"]}]
        cfg["rules"] = ["MATCH,PROXY"]
    path = os.path.join(core.home, f"mst-variant-{name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return path, str(cfg.get("secret") or ""), args


def server_names(core, rx):
    p = (core.api("GET", "/proxies")[1] or {}).get("proxies", {})
    names = [n for n in (p.get("GLOBAL") or {}).get("all", []) if str(p.get(n, {}).get("type", "")).lower() not in GROUP_TYPES]
    for prov in ((core.api("GET", "/providers/proxies")[1] or {}).get("providers") or {}).values():
        if str(prov.get("vehicleType", "")).lower() != "compatible":
            names += [x["name"] for x in prov.get("proxies") or [] if x.get("name") not in names]
    return [n for n in names if re.search(rx, n, re.I)][:2]


def run_variant(core, name, opts, out_dir):
    path, secret, extra = build(core, name)
    core.load(path, secret)
    pick = server_names(core, opts.filter)
    if not pick:
        return False, "no servers match --filter", ""
    core.api("PATCH", "/configs", {"mode": "rule"})
    core.api("PUT", "/proxies/GLOBAL", {"name": pick[0]})  # fails harmlessly for provider-only servers
    before = core.snapshot()
    rx = "|".join("^" + re.escape(n) + "$" for n in pick)
    env = dict(os.environ, MIHOMO_SECRET="")
    p = subprocess.run([opts.python, opts.speedtest, "--filter", rx, "--size", "2", "--out", out_dir, *extra],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                       stdin=subprocess.DEVNULL, timeout=900)
    out = p.stdout + p.stderr
    after = core.snapshot()
    found = "Connected to mihomo" in out
    tested = sum(1 for n in pick if re.search(r"^\s*\d+ " + re.escape(n[:20]), out, re.M)) == len(pick)
    restored = after == before
    mark = lambda ok: "yes" if ok else "NO"  # ASCII: this runs over SSH, where the console may not be UTF-8
    checks = f"found {mark(found)}  tested {mark(tested)}  restored {mark(restored)}  exit {p.returncode}"
    if not restored:
        checks += f"\n      before {json.dumps(before)}\n      after  {json.dumps(after)}"
    return found and tested and restored and p.returncode == 0, checks, out


def main():
    sys.stdout.reconfigure(errors="replace")  # over SSH the console encoding may lack the test's characters
    ap = argparse.ArgumentParser()
    ap.add_argument("speedtest")
    ap.add_argument("--python", default=sys.executable, help="Python that runs the speed test")
    ap.add_argument("--filter", default="Hong Kong", help="regex for the (first two) servers to test")
    ap.add_argument("--only", nargs="*", choices=VARIANTS, help="run only these variants")
    opts = ap.parse_args()
    opts.speedtest = os.path.abspath(opts.speedtest)
    core = Core()
    start = core.snapshot()
    print(f"mihomo config: {core.config}\ncontroller: {core.pipe or core.tcp}\nspeed test with: {opts.python}")
    passed, names = 0, opts.only or VARIANTS
    out_dir = tempfile.mkdtemp(prefix="mst-matrix-")
    try:
        for name in names:
            print(f"--- {name}", flush=True)
            if name == "secret" and core.tcp:
                print("SKIP  secret    a TCP controller keeps the secret it started with; a config load can't change it")
                passed += 1
                continue
            try:
                ok, checks, out = run_variant(core, name, opts, out_dir)
            except subprocess.TimeoutExpired:
                ok, checks, out = False, "speed test still running after 15 min", ""
            print(f"{'PASS' if ok else 'FAIL'}  {name:9} {checks}", flush=True)
            if not ok:
                print("\n".join("      | " + l for l in out.strip().splitlines()[-25:]))
            passed += ok
    finally:
        core.load(core.config, str(core.original.get("secret") or ""))  # the client's own config
        for sel, now in start["selections"].items():
            if now:
                core.api("PUT", "/proxies/" + urllib.parse.quote(sel, safe=""), {"name": now})
        core.api("PATCH", "/configs", {"mode": start["mode"]})
        for f in os.listdir(core.home):
            if f.startswith("mst-"):
                os.remove(os.path.join(core.home, f))
        print("client config, mode and selections put back:", "yes" if core.snapshot() == start else "NO - check by hand")
    print(f"{passed} of {len(names)} variants passed")
    sys.exit(0 if passed == len(names) else 1)


if __name__ == "__main__":
    main()
