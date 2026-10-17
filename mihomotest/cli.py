"""
mihomo_speedtest.py - test every server in your running mihomo, exactly as mihomo uses it.

Just run it:   python mihomo_speedtest.py

What it does
  1. Finds your running mihomo (any client: Clash Verge Rev, Clash Party, Nyanpasu,
     FlClash, ClashX Meta, plain mihomo...) through its external-controller API.
  2. Temporarily turns TUN off and switches mihomo to global mode.
  3. For each server: selects it, times 5 new connections through it (Conn ms), then
     downloads and uploads through mihomo (speeds count the transfer itself, not the
     connection setup). For servers that don't work it shows why (mihomo's own error,
     plus a direct check of the server's port); it also flags slow starters and other
     apps' traffic that shared the server during its test.
  4. After all transfers, pings every server address mihomo actually connected to
     (10 pings each). Servers behind Cloudflare's CDN are labelled: their ping shows
     the CDN edge, not the server. With --trace, traces the servers that didn't work.
  5. Restores TUN, mode and your server selections (also on Ctrl+C, or on Windows when the
     window is closed), prints the results and saves a Markdown and a CSV report (and, with
     --compare, what changed since a saved run).

Works on Linux, macOS and Windows. Uses the system's curl and ping.
While the test runs, traffic that goes through mihomo uses the server under test.

Optional arguments
  --filter REGEX   only test servers whose name matches
  --size MB        download size per server (upload is half). Default 10. Raise it
                   (e.g. 50) when the Note says a transfer was too quick to time
  --timeout SEC    max seconds per transfer. Default 25
  --api ADDR       controller address if it is not found automatically
                   (127.0.0.1:9090, unix:/path/to.sock or \\\\.\\pipe\\name)
  --secret TEXT    controller secret (or set MIHOMO_SECRET; asked for if needed)
  --out DIR        where to save the reports (default: mihomo-speedtest in your Documents
                   folder, or in your home folder if there is no Documents folder)
  --save-baseline  also save this run as baseline.csv in the report folder
  --compare [FILE] after the test, show what changed since baseline.csv (or since the
                   given report .csv): servers that stopped working or work again, big
                   changes (over 30%) in speed, Conn ms or ping, new and removed servers
  --gui            open the test as a page in your web browser (on this computer only)
  --no-browser     with --gui: only print the page's address (e.g. on a server, through
                   an SSH tunnel: ssh -L PORT:127.0.0.1:PORT server)
  --trace          for servers that didn't work, show where the path stops and who owns
                   each hop: a path trace for servers that don't answer ping, a TCP trace to
                   the port for those that do (Linux, run with sudo). Hop addresses are looked up
                   with Team Cymru's ASN service (through mihomo's DNS, else whois.cymru.com).
"""
import argparse
import csv
import datetime
import getpass
import os
import re
import shutil
import signal
import sys
import textwrap
import threading
import time
import unicodedata

from .mihomo import STOP_SIGNALS, MihomoError, SecretNeeded, find_controller, list_servers
from .report import BASELINE, HEADERS, LEGEND, cells, finish, load_report, mmss, notes, reports_dir, summary
from .system import IS_WIN, prereqs
from .tests import RESULTS, counts, run_tests

if IS_WIN:
    os.system("")  # enable ANSI colours in Windows terminals
for _stream in (sys.stdout, sys.stderr):  # e.g. ✔ or server names when output goes to a file on Windows
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")
RESTORED = threading.Event()
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
WIDTHS = [(3, True), (28, False), (9, False), (10, True), (10, True), (6, True), (9, True), (8, True), (11, False)]
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
        colour = {"ok": ok, "partial": warn, "blocked": warn, "refused": warn, "rejected": warn}.get(r["result"], bad)
        print(f"{line} {colour(fit(r['result'], WIDTHS[-1][0]))}" + (dim(f" {r['note']}") if r["note"] else ""))


def on_event(kind, data):
    if kind == "start":
        print(f"Servers to test: {bold(data['total'])}   estimated time: ~{mmss(data['estimate'])}")
        print(dim(f"Current state: {data['state'].describe()}. During the test TUN is off and mihomo is in "
                  "global mode;\nyour settings are restored at the end (also on Ctrl+C)."))
    elif kind == "server":
        status_line(f"[{data['index']}/{data['total']}] {data['server']['name']}   ~{mmss(data['left'])} left")
    elif kind == "busy":
        print(warn(f"Other apps are sending ~{data['rate'] / 1e6:.1f} MB/s through mihomo right now; during the "
                   "test that traffic uses the server under test,\nso speeds may read low. Servers affected "
                   "are marked 'busy' in the Note."))
    elif kind == "pinging":
        status_line(f"Pinging {data['count']} servers...")
    elif kind == "tracing":
        status_line(f"Tracing {data['count']} servers that didn't work (up to ~1 min)...")
    elif kind == "stopped":
        clear_status()
        print(warn("\nStopped - showing the servers tested so far."))
    elif kind == "restored":
        RESTORED.set()
        clear_status()
        print(dim(f"\nmihomo restored: {data['state'].describe()}."))


def stop_on_console_close():
    """Windows: closing the console window (or logging off / shutting down) gives the process ~5 s and no
    signal Python sees. Turn it into Ctrl+C and hold the window until mihomo is restored. Returns the
    handler, which must be kept referenced."""
    import _thread
    import ctypes

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def handler(event):
        if event not in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
            return 0  # Ctrl+C / Ctrl+Break: Python's own handling
        _thread.interrupt_main()
        RESTORED.wait(4.5)
        return 1
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
    return handler


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
    ap.add_argument("--size", type=positive(float), default=10, help="download MB per server (upload is half). Default 10; raise it when "
                    "the Note says a transfer was too quick to time")
    ap.add_argument("--timeout", type=positive(int), default=25, help="max seconds per transfer. Default 25")
    ap.add_argument("--api", help="controller address, if not found automatically")
    ap.add_argument("--secret", default="", help="controller secret (or MIHOMO_SECRET)")
    ap.add_argument("--out", default=reports_dir(), help=f"report folder. Default {reports_dir()}")
    ap.add_argument("--save-baseline", action="store_true",
                    help=f"also save this run as {BASELINE} in the report folder, to compare later runs with")
    ap.add_argument("--compare", nargs="?", const="", metavar="REPORT.csv",
                    help=f"after the test, show what changed since {BASELINE} (or since the given report)")
    ap.add_argument("--gui", action="store_true", help="open the test as a page in your web browser")
    ap.add_argument("--no-browser", action="store_true",
                    help="with --gui: only print the page's address (e.g. on a server, through an SSH tunnel)")
    ap.add_argument("--trace", action="store_true",
                    help="trace the servers that didn't work and look up who owns each hop (sends hop addresses "
                         "to Team Cymru's lookup service)")
    args = ap.parse_args()
    base_path = os.path.join(args.out, BASELINE)
    base = None
    if args.compare is not None:
        cpath = args.compare or base_path
        if not os.path.isfile(cpath):
            sys.exit(bad(f"Nothing to compare with: {cpath} doesn't exist.") +
                     ("\n  Run once with --save-baseline when everything works well." if not args.compare else ""))
        try:
            base = (cpath, *load_report(cpath))
        except (OSError, ValueError, csv.Error) as e:
            sys.exit(bad(f"Can't read {cpath}: {e}"))

    print(bold("mihomo server speed test\n"))
    if not check_prereqs():
        sys.exit(bad("\nPlease install what is missing above, then run again."))

    def stop(*_):
        raise KeyboardInterrupt
    for sig in STOP_SIGNALS[1:]:  # SIGINT already raises KeyboardInterrupt
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), stop)
            except (OSError, ValueError):
                pass
    close_handler = stop_on_console_close() if IS_WIN else None  # noqa: F841 - must stay referenced
    if args.gui:
        from .gui import serve
        return serve(args)

    ctl, version = connect(args)
    print(f"\nConnected to mihomo {version} at {ctl}")

    t0 = time.time()
    try:
        servers = list_servers(ctl)
        names_now = {s["name"] for s in servers}
        if args.filter:
            rx = re.compile(args.filter, re.I)
            servers = [s for s in servers if rx.search(s["name"])]
        if not servers:
            sys.exit(bad("mihomo reports no servers to test."))
        results, _ = run_tests(ctl, servers, args.size, args.timeout, on_event, trace=args.trace)
    except MihomoError as e:
        sys.exit(bad(str(e)))

    if not results:
        return
    print_table(results)
    n = counts(results)
    runtime = mmss(time.time() - t0)
    print(f"\n{bold(summary(n, len(results)))} in {runtime}")
    wrap = min(shutil.get_terminal_size((100, 20)).columns - 1, 110)
    print(dim(textwrap.fill(LEGEND, wrap)))
    meanings = notes(n, RESULTS)
    if meanings:
        print("\nWhat the results mean:")
        for note in meanings:
            print(warn(textwrap.fill(note, wrap, initial_indent="  ", subsequent_indent="    ")))
    fast = [r for r in results if r["down"]]
    if fast:
        b = max(fast, key=lambda r: r["down"])
        print(f"Fastest download: {bold(b['name'])} ({b['down']:.1f} Mbps)")
    meta = {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "version": version, "api": str(ctl),
            "size": args.size, "runtime": runtime, "total": len(results), "counts": n, "meanings": RESULTS}
    done = finish(results, meta, args.out, base, names_now, args.save_baseline)
    if done["comparison"]:
        print("\n" + bold(done["comparison"][0]))
        for line in done["comparison"][1:]:
            print(bad(line) if line.startswith("Stopped") else line)
    print(f"\nReports saved:\n  {done['md']}\n  {done['csv']}")
    if done["baseline"]:
        print(f"Saved as the baseline: {done['baseline']}")
    elif done["tip"]:
        print(dim("Tip: run with --save-baseline when everything works well, so you can compare later "
                  "with --compare."))
