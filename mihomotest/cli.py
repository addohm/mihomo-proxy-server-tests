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

Works on Linux, macOS and Windows. Uses the system's curl and ping.
While the test runs, traffic that goes through mihomo uses the server under test.

Optional arguments
  --filter REGEX   only test servers whose name matches
  --size MB        download size per server (upload is half). Default 10
  --api ADDR       controller address if it is not found automatically
                   (127.0.0.1:9090, unix:/path/to.sock or \\\\.\\pipe\\name)
  --secret TEXT    controller secret (or set MIHOMO_SECRET; asked for if needed)
  --out DIR        where to save the reports (default: next to mihomo_speedtest.py)
"""
import argparse
import datetime
import getpass
import os
import re
import shutil
import signal
import sys
import time
import unicodedata

from .mihomo import STOP_SIGNALS, MihomoError, SecretNeeded, find_controller, list_servers
from .report import HEADERS, cells, mmss, notes, save_reports
from .system import IS_WIN, prereqs
from .tests import counts, run_tests

if IS_WIN:
    os.system("")  # enable ANSI colours in Windows terminals
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # where mihomo_speedtest.py lives
WIDTHS = [(3, True), (34, False), (10, False), (8, True), (7, True), (10, True), (8, True), (11, False)]
NOT_FOUND = ("\n  • Make sure your client (or mihomo) is running."
             "\n  • Enable its external controller, or pass --api ADDRESS (and --secret)."
             "\n    Examples: --api 127.0.0.1:9090   --api unix:/tmp/mihomo.sock   --api \\\\.\\pipe\\mihomo")


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
        if unicodedata.combining(ch) or ch in "‍️":
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


def check_prereqs():
    print(bold("Checking prerequisites"))
    good = True
    for name, state, detail, hint in prereqs():
        mark = ok("✔") if state else warn("!") if state is None else bad("✘")
        print(f"  {mark} {fit(name, 12)} {detail}")
        if state is False:
            good = False
            print(f"      {warn('→ ' + hint)}")
    return good


def connect(args):
    """find_controller, asking for the secret if needed. Returns (Controller, version) or exits."""
    try:
        return find_controller(args.api, args.secret)
    except SecretNeeded as e:
        for ctl in e.controllers:
            print(warn(f"\nFound mihomo at {ctl}, but its API needs the secret."))
            print(dim("  It is in your client's settings (external controller / API secret)."))
            if not sys.stdin.isatty():
                break
            for _ in range(3):
                ctl.secret = getpass.getpass("  Secret (hidden): ")
                version = ctl.version()[1]
                if version:
                    return ctl, version
                print(bad("  That secret was not accepted."))
    except MihomoError:
        pass
    sys.exit(bad("\nCould not reach a running mihomo.") + NOT_FOUND)


def print_table(results):
    print("\n" + bold(" ".join(fit(h, w, rt) for h, (w, rt) in zip(HEADERS, WIDTHS))))
    print(dim(" ".join("─" * w for w, _ in WIDTHS)))
    for i, r in enumerate(results, 1):
        vals = cells(i, r)
        line = " ".join(fit(v, w, rt) for v, (w, rt) in zip(vals[:-1], WIDTHS[:-1]))
        colour = {"ok": ok, "partial": warn, "unreachable": warn}.get(r["result"], bad)
        print(f"{line} {colour(fit(r['result'], WIDTHS[-1][0]))}" + (dim(f" {r['note']}") if r["note"] else ""))


def on_event(kind, data):
    if kind == "start":
        print(f"Servers to test: {bold(data['total'])}   estimated time: ~{mmss(data['estimate'])}")
        print(dim(f"Current state: {data['state'].describe()}. During the test TUN is off and mihomo is in "
                  "global mode;\nyour settings are restored at the end (also on Ctrl+C)."))
    elif kind == "server":
        status_line(f"[{data['index']}/{data['total']}] {data['server']['name']}   ~{mmss(data['left'])} left")
    elif kind == "pinging":
        status_line("Finishing pings...")
    elif kind == "stopped":
        clear_status()
        print(warn("\nStopped - showing the servers tested so far."))
    elif kind == "restored":
        clear_status()
        print(dim(f"\nmihomo restored: {data['state'].describe()}."))


def positive(kind):
    def parse(v):
        n = kind(v)
        if n <= 0:
            raise argparse.ArgumentTypeError(f"must be greater than 0, got {v}")
        return n
    return parse


def main():
    ap = argparse.ArgumentParser(prog="mihomo_speedtest.py", description="Test every server in your running mihomo.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--filter", help="only test servers whose name matches this regex")
    ap.add_argument("--size", type=positive(float), default=10, help="download MB per server (upload is half). Default 10")
    ap.add_argument("--timeout", type=positive(int), default=25, help="max seconds per transfer. Default 25")
    ap.add_argument("--api", help="controller address, if not found automatically")
    ap.add_argument("--secret", default="", help="controller secret (or MIHOMO_SECRET)")
    ap.add_argument("--out", default=APP_DIR, help="report folder")
    args = ap.parse_args()

    print(bold("mihomo server speed test\n"))
    if not check_prereqs():
        sys.exit(bad("\nPlease install what is missing above, then run again."))

    ctl, version = connect(args)
    print(f"\nConnected to mihomo {version} at {ctl}")

    def stop(*_):
        raise KeyboardInterrupt
    for sig in STOP_SIGNALS[1:]:  # SIGINT already raises KeyboardInterrupt
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), stop)
            except (OSError, ValueError):
                pass

    t0 = time.time()
    try:
        servers = list_servers(ctl)
        if args.filter:
            rx = re.compile(args.filter, re.I)
            servers = [s for s in servers if rx.search(s["name"])]
        if not servers:
            sys.exit(bad("mihomo reports no servers to test."))
        results, _ = run_tests(ctl, servers, args.size, args.timeout, on_event)
    except MihomoError as e:
        sys.exit(bad(str(e)))

    if not results:
        return
    print_table(results)
    n = counts(results)
    runtime = mmss(time.time() - t0)
    print(f"\n{ok(n['ok'])} ok, {warn(n['partial'])} partial, {warn(n['unreachable'])} unreachable, "
          f"{bad(n['failed'])} failed of {len(results)} "
          f"servers in {runtime}")
    for note in notes(n):
        print(warn("\n" + note))
    fast = [r for r in results if r["down"]]
    if fast:
        b = max(fast, key=lambda r: r["down"])
        print(f"Fastest download: {bold(b['name'])} ({b['down']:.1f} Mbps)")
    meta = {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "version": version, "api": str(ctl),
            "size": args.size, "runtime": runtime, "total": len(results), **n}
    md, cs = save_reports(results, meta, args.out)
    print(f"\nReports saved:\n  {md}\n  {cs}")
