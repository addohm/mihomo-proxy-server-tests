"""The per-server test (curl, ping) and the run loop that tests every server."""
import collections
import concurrent.futures as cf
import errno
import os
import re
import shutil
import socket
import statistics
import tempfile
import time

from .mihomo import LogWatcher, MihomoState, config_servers, mihomo_resolve, remote_ip
from .system import CURL, IS_MAC, IS_WIN, NULL_DEV, run

PROBE_URL = "http://www.gstatic.com/generate_204"
DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
RESULTS = ("ok", "partial", "unreachable", "failed")
SLOW_START = 3  # seconds to the first response through a newly selected server before it's flagged
UDP_TYPES = {"hysteria", "hysteria2", "tuic", "wireguard"}  # no TCP port to check directly
# mihomo's connection error -> plain meaning; first match wins
CAUSES = [
    (r"missing port in address|too many colons|invalid (address|port)|unknown network",
     "config error: invalid server address"),
    (r"no such host|lookup .*(fail|refused|timeout)|resolve|dns", "server name doesn't resolve"),
    (r"network is unreachable|no route to host", "no route to the server (e.g. no IPv6)"),
    (r"operation not permitted|permission denied", "blocked by a firewall on this computer"),
    (r"connection refused", "server refused the connection"),
    (r"auth|password|unauthori[sz]ed|invalid user|uuid", "server rejected the credentials"),
    (r"tls|x509|certificate", "TLS/certificate problem"),
    (r"i/o timeout|timed out|deadline exceeded|no recent network activity", "connection timed out"),
    (r"reset by peer|eof|broken pipe|closed", "connection cut (by the server or the network)"),
]


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


def classify(error):
    for pattern, meaning in CAUSES:
        if re.search(pattern, error, re.I):
            return meaning
    return "mihomo could not connect through this server"


def dial_error(messages):
    """The most common server-connection error in mihomo's log messages, or ''."""
    errors = [m.split(" error: ", 1)[1].strip() for m in messages
              if m.startswith(("[TCP] dial", "[UDP] dial")) and " error: " in m]
    return collections.Counter(errors).most_common(1)[0][0] if errors else ""


def tcp_check(host, port, timeout=5):
    """Plain TCP connect, outside mihomo: 'open', 'refused', 'no answer', 'no route' or another error."""
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
        return "open"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "no answer"
    except OSError as e:
        return "no route" if e.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH) else str(e)


def add_note(r, text):
    r["note"] = f"{r['note']}; {text}" if r["note"] else text


def test_server(ctl, state, proxy, srv, size, timeout, up_file, logs):
    r = {"name": srv["name"], "type": srv["type"], "ip": None, "ping": None, "loss": None,
         "down": None, "up": None, "first": None, "result": "", "note": "", "error": "", "check": ""}
    mark = logs.mark()
    if not state.select(srv):
        r["result"], r["note"] = "failed", ("mihomo would not select this server" if srv.get("path")
                                            else "no selector group contains this server, so it can't be selected")
        return r
    # warm-up: the first connection through a newly selected server can take a few seconds
    t0 = time.time()
    deadline, rc = t0 + 10, -1
    while time.time() < deadline:
        rc, code, _, _ = curl(proxy, PROBE_URL, max_time=8)
        if code in (200, 204):
            r["first"] = time.time() - t0
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
    worked = r["down"] is not None or r["up"] is not None
    if r["first"] is not None and r["first"] > SLOW_START:
        add_note(r, f"slow start: first response after {r['first']:.0f} s")
    elif r["first"] is None and worked:
        add_note(r, "slow start: no response in the first 10 s")
    if r["down"] is not None and r["up"] is not None:
        r["result"] = "ok"
        return r
    r["error"] = dial_error(logs.since(mark))
    if not worked:
        r["result"] = "failed"
        cause = (classify(r["error"]) if r["error"] or drc not in (0, 28)
                 else "timed out" if drc == 28 else f"HTTP {dcode}")
        r["note"] = f"{cause}; {r['note']}" if r["note"] else cause
    else:
        r["result"] = "partial"
        failed = "upload failed" if r["up"] is None else "download failed"
        if r["error"]:
            failed += f" ({classify(r['error'])})"
        r["note"] = f"{failed}; {r['note']}" if r["note"] else failed
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
    pings, checks, logs = {}, {}, None
    try:
        proxy = state.proxy_url()
        state.apply()
        logs = LogWatcher(ctl)
        for i, srv in enumerate(servers, 1):
            left = (time.time() - t0) / (i - 1) * (len(servers) - i + 1) if i > 1 else est
            emit("server", {"index": i, "total": len(servers), "server": srv, "left": left})
            r = test_server(ctl, state, proxy, srv, size, timeout, up_file, logs)
            results.append(r)
            if r["ip"]:
                pings[r["name"]] = pinger.submit(ping, r["ip"])
        # servers mihomo never connected to: ping the address from the config, resolved by mihomo;
        # failed servers also get a direct TCP check of their port, outside mihomo
        need = [r for r in results if r["name"] not in pings or r["result"] == "failed"]
        conf = config_servers([r["name"] for r in need]) if need else {}
        for r in need:
            c = conf.get(r["name"]) or {}
            ip = r["ip"] or (mihomo_resolve(ctl, c["server"]) if c.get("server") else None)
            if ip and r["name"] not in pings:
                pings[r["name"]] = pinger.submit(ping, ip)
            if r["result"] == "failed":
                if r["type"] in UDP_TYPES:
                    r["check"] = "not possible (UDP)"
                elif ip and c.get("port"):
                    checks[r["name"]] = (c["port"], pinger.submit(tcp_check, ip, c["port"]))
        emit("pinging", {})
        for r in results:
            if r["name"] in pings:
                r["ping"], r["loss"] = pings[r["name"]].result()
                if r["result"] == "failed" and r["ping"] is not None:
                    r["result"] = "unreachable"
                    if not r["error"]:
                        r["note"] = "answers ping, but no working proxy connection from this network"
            if r["name"] in checks:
                port, fut = checks[r["name"]]
                r["check"] = f"port {port} {fut.result()}"
            if r["check"]:
                add_note(r, "direct check: " + r["check"])
    except KeyboardInterrupt:
        stopped = True
        emit("stopped", {})
    finally:
        if logs:
            logs.stop()
        state.restore()
        for f in list(pings.values()) + [fut for _, fut in checks.values()]:
            f.cancel()  # when stopped, don't wait for queued pings/checks at exit
        pinger.shutdown(wait=False)
        shutil.rmtree(tmp, ignore_errors=True)
    emit("restored", {"state": state})
    return results, stopped
