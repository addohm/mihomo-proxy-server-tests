#!/usr/bin/env python3
"""Run the speed test against each config variant, one change at a time from the default, and check it.

    python3 matrix.py /root/mst/mihomo_speedtest.py [--servers N] [--filter REGEX]

For each variant (see variant.py): switch mihomo into it, put mihomo in a known "before" state (mode rule,
GLOBAL on a real server), test N servers (default 2: the first ones in GLOBAL order), then check:
    found     the script found and connected to mihomo on its own
    tested    it tested every chosen server and at least one was ok
    restored  mode, TUN, GLOBAL selection and local proxy ports are exactly as before
Always switches back to the default setup at the end. Exit code 1 if any variant failed.

It toggles TUN, so run it detached from the SSH session:
    setsid nohup python3 /root/testserver/matrix.py /root/mst/mihomo_speedtest.py > /root/matrix.log 2>&1 &
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from variant import GROUP_TYPES, api, apply, parse  # noqa: E402

VARIANTS = [[], ["controller=unix"], ["secret=no"], ["port=socks"], ["port=http"], ["port=none"],
            ["tun=on"], ["source=provider"]]


def snapshot(info):
    c = api(info, "GET", "/configs") or {}
    g = api(info, "GET", "/proxies/GLOBAL") or {}
    return {"mode": c.get("mode"), "tun": bool((c.get("tun") or {}).get("enable")),
            "mixed-port": c.get("mixed-port", 0), "socks-port": c.get("socks-port", 0),
            "port": c.get("port", 0), "global": g.get("now")}


def server_names(info):
    p = (api(info, "GET", "/proxies", timeout=20) or {}).get("proxies", {})
    return [n for n in (p.get("GLOBAL") or {}).get("all", [])
            if str(p.get(n, {}).get("type", "")).lower() not in GROUP_TYPES]


def run_variant(changes, args, ssh_ip, out_dir):
    info = apply(parse(changes), ssh_ip)
    names = server_names(info)
    pick = [n for n in names if re.search(args.filter, n, re.I)] if args.filter else names[:args.servers]
    if not pick:
        return False, "no servers to test", ""
    api(info, "PATCH", "/configs", {"mode": "rule"})
    api(info, "PUT", "/proxies/GLOBAL", {"name": pick[0]})
    before = snapshot(info)
    rx = "|".join("^" + re.escape(n) + "$" for n in pick)
    p = subprocess.run([sys.executable, args.launcher, "--size", "2", "--out", out_dir, "--filter", rx],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    after = snapshot(info)
    out = p.stdout + p.stderr
    m = re.search(r"(\d+) ok, (\d+) partial, (\d+) unreachable, (\d+) failed of (\d+) servers", out)
    checks = {"found": "Connected to mihomo" in out,
              "tested": bool(m) and int(m[5]) == len(pick) and int(m[1]) >= 1,
              "restored": before == after,
              "exit 0": p.returncode == 0}
    detail = "  ".join(f"{k} {'✔' if v else '✘'}" for k, v in checks.items())
    if m:
        detail += f"   ({m[1]} ok of {m[5]})"
    if before != after:
        detail += "\n      before: " + str(before) + "\n      after:  " + str(after)
    return all(checks.values()), detail, out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("launcher", help="path to mihomo_speedtest.py (with mihomotest/ next to it)")
    ap.add_argument("--servers", type=int, default=2, help="how many servers per variant (default 2)")
    ap.add_argument("--filter", help="regex choosing the servers instead of the first N")
    args = ap.parse_args()
    ssh_ip = os.environ.get("SSH_IP") or os.environ.get("SSH_CLIENT", "").split(" ")[0]
    out_dir = tempfile.mkdtemp(prefix="mst-matrix-")
    failed = 0
    try:
        for changes in VARIANTS:
            name = " ".join(changes) or "default"
            print(f"--- {name}", flush=True)
            try:
                good, detail, out = run_variant(changes, args, ssh_ip, out_dir)
            except (SystemExit, subprocess.SubprocessError, OSError) as e:
                good, detail, out = False, f"error: {e}", ""
            print(f"{'PASS' if good else 'FAIL'}  {name:<18} {detail}", flush=True)
            if not good:
                failed += 1
                print("      " + "\n      ".join(out.strip().splitlines()[-15:]), flush=True)
    finally:
        apply(parse([]), ssh_ip)
        print("--- back to the default setup", flush=True)
    print(f"\n{len(VARIANTS) - failed} of {len(VARIANTS)} variants passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
