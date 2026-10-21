#!/usr/bin/env python3
"""Smoke test of the packaged program against a stand-in mihomo, on any OS (GitHub Actions runs it on Linux,
Windows and both kinds of Mac after each build; it also runs on any test machine without a mihomo of its own).

    python testserver/ci_smoke.py PATH/TO/mihomo-speedtest[.exe]

It downloads mihomo (MIHOMO_VERSION; or set MIHOMO_EXE to a mihomo binary already on the machine, e.g. in
mainland China where GitHub isn't reachable), then starts two cores in a temporary folder:
  - "relay-core" (renamed so the test doesn't find it): a plain proxy on 127.0.0.1:17891, standing in for a
    server on the internet;
  - "mihomo" (the one under test): servers "local" (socks5 to the relay: works) and "dead" (socks5 to a closed
    port), group PROXY, mode rule, TUN off. Its controller is the kind the OS's clients use: a Unix socket on
    Linux, TCP with a secret on macOS (read from its config, so PyYAML must be in the build), a named pipe on
    Windows. The program has to find it on its own.
Then:
  1. terminal run (--cli): exit 0, "local" ok and "dead" not, Markdown + CSV saved;
  2. mode, TUN and every selector group exactly as before (PROXY left on "dead" on purpose);
  3. started without arguments (a double-click): it serves the GUI page (with its token) and its API, and
     quits through the page's Quit.
Exit code 1 on any failure; each check prints PASS / FAIL (and a GitHub annotation on failure).
"""
import csv
import glob
import gzip
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mihomotest.mihomo import Controller  # noqa: E402

MIHOMO_VERSION = "v1.19.32"
IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
FAILED = []


def check(ok, what, detail=""):
    print(("PASS " if ok else "FAIL ") + what + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        FAILED.append(what)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error::{what}: {detail}"[:1000], flush=True)
    return ok


def download_mihomo(folder):
    local = os.environ.get("MIHOMO_EXE")  # a mihomo already on this machine (e.g. where GitHub isn't reachable)
    if local:
        exe = os.path.join(folder, "mihomo" + (".exe" if IS_WIN else ""))
        shutil.copy2(local, exe)
        return exe
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    osname = "windows" if IS_WIN else "darwin" if IS_MAC else "linux"
    flavour = arch if arch == "arm64" else "amd64-compatible"
    ext = "zip" if IS_WIN else "gz"
    url = (f"https://github.com/MetaCubeX/mihomo/releases/download/{MIHOMO_VERSION}/"
           f"mihomo-{osname}-{flavour}-{MIHOMO_VERSION}.{ext}")
    data = urllib.request.urlopen(url, timeout=120).read()
    exe = os.path.join(folder, "mihomo" + (".exe" if IS_WIN else ""))
    if IS_WIN:
        path = os.path.join(folder, "m.zip")
        with open(path, "wb") as f:
            f.write(data)
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".exe"))
            with z.open(name) as src, open(exe, "wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        with open(exe, "wb") as f:
            f.write(gzip.decompress(data))
        os.chmod(exe, 0o755)
    return exe


def start_cores(tmp, exe):
    relay_dir, client_dir = os.path.join(tmp, "relay"), os.path.join(tmp, "client")
    os.makedirs(relay_dir)
    os.makedirs(client_dir)
    relay_exe = os.path.join(relay_dir, "relay-core" + (".exe" if IS_WIN else ""))
    shutil.copy2(exe, relay_exe)
    with open(os.path.join(relay_dir, "config.yaml"), "w") as f:
        f.write("mixed-port: 17891\nbind-address: 127.0.0.1\nmode: direct\nlog-level: warning\n")
    if IS_WIN:
        ctl, ctl_cfg = Controller("pipe", r"\\.\pipe\mihomo-ci"), "external-controller-pipe: '\\\\.\\pipe\\mihomo-ci'\n"
    elif IS_MAC:
        ctl, ctl_cfg = Controller("tcp", "127.0.0.1:19090", "ci-secret"), \
            "external-controller: 127.0.0.1:19090\nsecret: ci-secret\n"
    else:
        sock = os.path.join(client_dir, "ctl.sock")
        ctl, ctl_cfg = Controller("unix", sock), f"external-controller-unix: {sock}\n"
    with open(os.path.join(client_dir, "config.yaml"), "w") as f:
        f.write("mixed-port: 17890\nbind-address: 127.0.0.1\nmode: rule\nlog-level: warning\n" + ctl_cfg +
                "proxies:\n"
                "  - {name: local, type: socks5, server: 127.0.0.1, port: 17891}\n"
                "  - {name: dead, type: socks5, server: 127.0.0.1, port: 9}\n"
                "proxy-groups:\n  - {name: PROXY, type: select, proxies: [local, dead]}\n"
                "rules:\n  - MATCH,PROXY\n")
    procs = [subprocess.Popen([relay_exe, "-d", relay_dir], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT),
             subprocess.Popen([exe, "-d", client_dir], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)]
    for _ in range(60):
        if ctl.call("GET", "/version")[0] == 200:
            break
        time.sleep(1)
    check(ctl.call("GET", "/version")[0] == 200, "stand-in mihomo answers on its controller", str(ctl))
    return procs, ctl


def snapshot(ctl):
    _, c = ctl.call("GET", "/configs")
    _, p = ctl.call("GET", "/proxies")
    c, p = c or {}, (p or {}).get("proxies", {})
    return {"mode": c.get("mode"), "tun": bool((c.get("tun") or {}).get("enable")),
            "groups": {k: v.get("now") for k, v in p.items() if str(v.get("type", "")).lower() == "selector"}}


def run_cli(binary, out):
    p = subprocess.run([binary, "--cli", "--size", "1", "--timeout", "15", "--out", out],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    print(p.stdout[-3000:], p.stderr[-2000:], sep="\n", flush=True)
    check(p.returncode == 0, "terminal run exits 0", f"exit {p.returncode}")
    csvs = [f for f in os.listdir(out) if f.endswith(".csv")] if os.path.isdir(out) else []
    mds = [f for f in os.listdir(out) if f.endswith(".md")] if os.path.isdir(out) else []
    check(len(csvs) == 1 and len(mds) == 1, "Markdown and CSV report saved", str(os.listdir(out) if os.path.isdir(out) else out))
    if csvs:
        with open(os.path.join(out, csvs[0]), newline="", encoding="utf-8-sig") as f:
            rows = {r["server"]: r for r in csv.DictReader(f)}
        local, dead = rows.get("local", {}), rows.get("dead", {})
        check(local.get("result") == "ok", "server 'local' works (ok)", json.dumps(local))
        check(bool(dead) and dead.get("result") != "ok", "server 'dead' doesn't work", json.dumps(dead))


def run_gui(binary, tmp):
    # BROWSER: a program that "opens" the page and does nothing (webbrowser runs it with the URL)
    noop = "/usr/bin/true"
    if IS_WIN:
        noop = os.path.join(tmp, "noop.bat")
        with open(noop, "w") as f:
            f.write("@exit /b 0\n")
    env = dict(os.environ, BROWSER=noop)
    p = subprocess.Popen([binary], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                         text=True, encoding="utf-8", errors="replace")
    url, seen = None, []
    deadline = time.time() + 120
    while time.time() < deadline and url is None:
        line = p.stdout.readline()
        if not line:
            break
        seen.append(line)
        m = re.search(r"(http://127\.0\.0\.1:\d+/\?t=[\w-]+)", line)
        url = m.group(1) if m else None
    if not check(url is not None, "double-click mode serves the GUI", "".join(seen)[-1500:]):
        p.kill()
        return
    token = url.split("t=", 1)[1]
    page = urllib.request.urlopen(url, timeout=20).read().decode("utf-8")
    check("<html" in page.lower() and "{{TOKEN}}" not in page and token in page, "the page is bundled and served")
    base = url.split("/?", 1)[0]
    req = urllib.request.Request(base + "/api/state", headers={"X-Token": token})
    state = json.loads(urllib.request.urlopen(req, timeout=60).read())
    check(state.get("connected") and {"local", "dead"} <= {s["name"] for s in state.get("servers", [])},
          "the GUI finds the stand-in mihomo", json.dumps(state)[:500])
    req = urllib.request.Request(base + "/api/quit", data=b"{}", method="POST",
                                 headers={"X-Token": token, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()
    try:
        code = p.wait(30)
    except subprocess.TimeoutExpired:
        p.kill()
        code = "still running"
    check(code == 0, "Quit on the page ends the program", f"exit {code}")


def main():
    # the workflow passes a pattern (dist/release/mihomo-speedtest-*-...): bash expands it, PowerShell (the
    # Windows runner's shell) doesn't, so expand it here
    found = sorted(glob.glob(sys.argv[1]))
    binary = os.path.abspath(found[0] if found else sys.argv[1])
    if not check(os.path.isfile(binary), "the program to test exists", binary):
        sys.exit(1)
    tmp = tempfile.mkdtemp(prefix="mst-ci-")
    procs = []
    try:
        procs, ctl = start_cores(tmp, download_mihomo(tmp))
        ctl.call("PUT", "/proxies/PROXY", {"name": "dead"})  # restore must put this back, not "local"
        before = snapshot(ctl)
        check(before["groups"].get("PROXY") == "dead", "stand-in set up", json.dumps(before))
        run_cli(binary, os.path.join(tmp, "reports"))
        after = snapshot(ctl)
        check(after == before, "mode, TUN and every group restored", f"before {before} after {after}")
        run_gui(binary, tmp)
        check(snapshot(ctl) == before, "still restored after the GUI")
    finally:
        for p in procs:
            p.kill()
        time.sleep(1)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not FAILED else str(len(FAILED)) + ' FAILED: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
