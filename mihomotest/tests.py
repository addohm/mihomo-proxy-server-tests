"""The per-server test (curl, ping) and the run loop that tests every server."""
import concurrent.futures as cf
import os
import re
import shutil
import statistics
import tempfile
import time

from .mihomo import MihomoState, config_hosts, mihomo_resolve, remote_ip
from .system import CURL, IS_MAC, IS_WIN, NULL_DEV, run

PROBE_URL = "http://www.gstatic.com/generate_204"
DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
RESULTS = ("ok", "partial", "unreachable", "failed")


def curl(proxy, url, extra=(), max_time=20):
    """curl through mihomo. Returns (exit_code, http_code, bytes/s down, bytes/s up)."""
    args = [CURL, "-sS", "-o", NULL_DEV, "-x", proxy,
            "--connect-timeout", "10", "-m", str(max_time),
            "-w", "%{http_code} %{speed_download} %{speed_upload}", *extra, url]
    rc, out, _ = run(args, timeout=max_time + 15)
    p = out.split()
    try:
        return rc, int(p[0]), float(p[1]), float(p[2])
    except (IndexError, ValueError):
        return rc, 0, 0.0, 0.0


def ping(ip, count=4):
    """ICMP ping. Returns (avg_ms | None, loss_pct | None)."""
    if IS_WIN:
        args = ["ping", "-n", str(count), "-w", "2000", ip]
    elif IS_MAC:  # macOS: -W is in milliseconds, and IPv6 needs ping6 (which has no -W)
        args = ["ping6", "-c", str(count), ip] if ":" in ip else ["ping", "-c", str(count), "-W", "2000", ip]
    else:
        args = ["ping", "-c", str(count), "-W", "2", ip]
    _, out, _ = run(args, timeout=count * 3 + 5)
    # A reply line has exactly one "=12.3 ms" / "<1ms" value in any language; the Windows summary line
    # ("Minimum = 12ms, Maximum = ..., Average = ...") has three, and the Linux/macOS one uses "a/b/c ms".
    times = []
    for line in out.splitlines():
        found = re.findall(r"[=<]\s*([\d.]+)\s*ms", line)
        if len(found) == 1:
            times.append(float(found[0]))
    times = times[:count]
    if not times:
        return None, 100.0
    return statistics.mean(times), round(100 * (1 - len(times) / count))


def test_server(ctl, state, proxy, srv, size, timeout, up_file):
    r = {"name": srv["name"], "type": srv["type"], "ip": None, "ping": None, "loss": None,
         "down": None, "up": None, "result": "", "note": ""}
    if not state.select(srv):
        r["result"], r["note"] = "failed", ("mihomo would not select this server" if srv.get("path")
                                            else "no selector group contains this server, so it can't be selected")
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
        fut = ex.submit(curl, proxy, DOWN_URL.format(n=int(size * 1e6)), (), timeout)
        while not fut.done():
            r["ip"] = r["ip"] or remote_ip(ctl, srv["name"])
            time.sleep(0.5)
        drc, dcode, dspeed, _ = fut.result()
    if dcode == 200 and dspeed > 0:
        r["down"] = dspeed * 8 / 1e6
        if drc == 28:
            r["note"] = "slow: download hit the time limit"
    for _ in range(2 if reached else 1):  # one retry for a working server
        urc, ucode, _, uspeed = curl(proxy, UP_URL, ["-H", "Expect:", "--data-binary", f"@{up_file}"], timeout)
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


def estimate(count, size):
    """Expected seconds for `count` servers at `size` MB (~20 s per server at 10 MB, incl. failing ones)."""
    return count * (6 + 1.4 * size)


def counts(results):
    return {k: sum(r["result"] == k for r in results) for k in RESULTS}


def run_tests(ctl, servers, size=10, timeout=25, on_event=None):
    """Test `servers` one at a time through mihomo; mihomo's state is always restored.
    Returns (results, stopped); stopped = interrupted (Ctrl+C / SIGTERM), results then hold what was tested.

    on_event(kind, data) is called with:
      "start"    {"total", "estimate", "state"}           before the first server (state: MihomoState)
      "server"   {"index", "total", "server", "left"}     a server's test begins (left: seconds, estimated)
      "pinging"  {}                                       all servers done, waiting for pings
      "stopped"  {}                                       interrupted
      "restored" {"state"}                                mihomo is back to its previous state
    """
    emit = on_event or (lambda kind, data: None)
    state = MihomoState(ctl)
    est = estimate(len(servers), size)
    emit("start", {"total": len(servers), "estimate": est, "state": state})
    results, stopped, t0 = [], False, time.time()
    tmp = tempfile.mkdtemp(prefix="mihomo-st-")
    up_file = os.path.join(tmp, "upload.bin")
    with open(up_file, "wb") as f:
        f.write(os.urandom(int(size * 500_000)))
    pinger = cf.ThreadPoolExecutor(max_workers=8)
    pings = {}
    try:
        proxy = state.proxy_url()
        state.apply()
        for i, srv in enumerate(servers, 1):
            left = (time.time() - t0) / (i - 1) * (len(servers) - i + 1) if i > 1 else est
            emit("server", {"index": i, "total": len(servers), "server": srv, "left": left})
            r = test_server(ctl, state, proxy, srv, size, timeout, up_file)
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
        emit("pinging", {})
        for r in results:
            if r["name"] in pings:
                r["ping"], r["loss"] = pings[r["name"]].result()
                if r["result"] == "failed" and r["ping"] is not None:
                    r["result"] = "unreachable"
                    r["note"] = "answers ping, but no working proxy connection from this network"
    except KeyboardInterrupt:
        stopped = True
        emit("stopped", {})
    finally:
        state.restore()
        for f in pings.values():  # when stopped, don't wait for queued pings at exit
            f.cancel()
        pinger.shutdown(wait=False)
        shutil.rmtree(tmp, ignore_errors=True)
    emit("restored", {"state": state})
    return results, stopped
