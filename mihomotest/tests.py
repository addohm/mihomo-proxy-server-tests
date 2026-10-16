"""The per-server test (curl, ping) and the run loop that tests every server."""
import collections
import concurrent.futures as cf
import errno
import ipaddress
import os
import re
import shutil
import socket
import statistics
import tempfile
import time

from .mihomo import LogWatcher, MihomoState, config_servers, mihomo_resolve, remote_ip, traffic_total
from .system import CURL, IS_MAC, IS_WIN, NULL_DEV, run

PROBE_URL = "http://www.gstatic.com/generate_204"
DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
RESULTS = ("ok", "partial", "unreachable", "failed")
SLOW_START = 3  # seconds to the first response through a newly selected server before it's flagged
UDP_TYPES = {"hysteria", "hysteria2", "tuic", "wireguard"}  # no TCP port to check directly
PROBES = 5  # new connections through each working server, for Conn ms and its jitter
PING_COUNT = 10
PING_WORKERS = 50  # all servers are pinged together at the end of the run
MIN_TRANSFER = 0.2  # s; a shorter transfer phase can't be timed reliably, so it's flagged
BUSY_SHARE, BUSY_MIN = 0.05, 1e6  # other traffic flagged when > 5% of the test's own data and >= 1 MB
BUSY_RATE = 1e6  # bytes/s of other traffic at the start that's worth mentioning
# Cloudflare's published ranges (cloudflare.com/ips): servers behind its CDN answer ping from the nearest edge
CLOUDFLARE = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32")]
# mihomo's connection error -> plain meaning; first match wins
CAUSES = [
    (r"missing port in address|too many colons|invalid (address|port)|unknown network",
     "config error: invalid server address"),
    (r"no such host|lookup .*(fail|refused|timeout)|resolve|dns", "server name doesn't resolve"),
    # Windows (connectex) says the same in its own words, e.g. "attempted to an unreachable network"
    (r"network is unreachable|no route to host|unreachable (network|host)", "no route to the server (e.g. no IPv6)"),
    (r"operation not permitted|permission denied|forbidden by its access permissions",
     "blocked by a firewall on this computer"),
    (r"connection refused|actively refused", "server refused the connection"),
    (r"auth|password|unauthori[sz]ed|invalid user|uuid", "server rejected the credentials"),
    (r"tls|x509|certificate", "TLS/certificate problem"),
    (r"i/o timeout|timed out|deadline exceeded|no recent network activity", "connection timed out"),
    (r"reset by peer|forcibly closed|eof|broken pipe|closed", "connection cut (by the server or the network)"),
]


def curl(proxy, url, extra=(), max_time=20):
    """curl through mihomo. Returns (exit_code, info): info has the HTTP code, the times (s) at which the
    request starts being sent ("pre") and the first response byte arrives ("start"), the total time, and the
    bytes received / sent."""
    args = [CURL, "-sS", "-o", NULL_DEV, "-x", proxy,
            "--connect-timeout", "10", "-m", str(max_time),
            "-w", "%{http_code} %{time_pretransfer} %{time_starttransfer} %{time_total} %{size_download} "
                  "%{size_upload}", *extra, url]
    rc, out, _ = run(args, timeout=max_time + 15)
    try:
        code, pre, start, total, down, up = out.split()[:6]
        return rc, {"code": int(code), "pre": float(pre), "start": float(start), "total": float(total),
                    "down": int(float(down)), "up": int(float(up))}
    except ValueError:
        return rc, {"code": 0, "pre": 0.0, "start": 0.0, "total": 0.0, "down": 0, "up": 0}


def mbps(nbytes, seconds):
    """Speed of the transfer phase only (connection setup is measured separately, as Conn ms).
    Returns (Mbps | None, too_short): a phase under MIN_TRANSFER can't be timed reliably."""
    if nbytes <= 0:
        return None, False
    return nbytes * 8 / 1e6 / max(seconds, 1e-3), seconds < MIN_TRANSFER


def jitter(values):
    """Average change between consecutive measurements, or None with fewer than two."""
    return statistics.mean(abs(b - a) for a, b in zip(values, values[1:])) if len(values) > 1 else None


def is_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def cloudflare(ip):
    if not is_ip(ip):
        return False
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in CLOUDFLARE if net.version == addr.version)


def ping(ip, count=PING_COUNT):
    """ICMP ping. Returns (avg_ms | None, loss_pct, jitter_ms | None)."""
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
        return None, 100.0, None
    return statistics.mean(times), round(100 * (1 - len(times) / count)), jitter(times)


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
    r = {"name": srv["name"], "type": srv["type"], "ip": None, "ping": None, "loss": None, "ping_jitter": None,
         "conn": None, "conn_jitter": None, "down": None, "up": None, "first": None, "other": None, "short": [],
         "result": "", "note": "", "error": "", "check": ""}
    mark = logs.mark()
    if not state.select(srv):
        r["result"], r["note"] = "failed", ("mihomo would not select this server" if srv.get("path")
                                            else "no selector group contains this server, so it can't be selected")
        return r
    # warm-up: the first connection through a newly selected server can take a few seconds
    t0 = time.time()
    deadline, rc = t0 + 10, -1
    while time.time() < deadline:
        rc, t = curl(proxy, PROBE_URL, max_time=8)
        if t["code"] in (200, 204):
            r["first"] = time.time() - t0
            break
        time.sleep(1)
    reached = rc == 0
    # connection time: new connections through the server, time to the first response
    conns, lost = [], 0
    if r["first"] is not None:
        for _ in range(PROBES):
            _, t = curl(proxy, PROBE_URL, max_time=5)
            if t["code"] in (200, 204):
                conns.append(t["start"] * 1000)
            else:
                lost += 1
    if conns:
        r["conn"], r["conn_jitter"] = statistics.median(conns), jitter(conns)
    # download, while reading mihomo's connection list for the server address it dialed;
    # mihomo's byte counters before and after the transfers show other apps' traffic through it
    before, own = traffic_total(ctl), 0
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(curl, proxy, DOWN_URL.format(n=int(size * 1e6)), (), timeout)
        while not fut.done():
            r["ip"] = r["ip"] or remote_ip(ctl, srv["name"])
            time.sleep(0.5)
        drc, t = fut.result()
    own += t["down"] + t["up"]
    if t["code"] == 200 and t["down"] > 0:
        r["down"], short = mbps(t["down"], t["total"] - t["start"])
        if short:
            r["short"].append("download")
        if drc == 28:
            r["note"] = "slow: download hit the time limit"
    dcode = t["code"]
    for _ in range(2 if reached else 1):  # one retry for a working server
        urc, t = curl(proxy, UP_URL, ["-H", "Expect:", "--data-binary", f"@{up_file}"], timeout)
        own += t["down"] + t["up"]
        if (t["code"] in (200, 201, 204) or urc == 28) and t["up"] > 0:
            r["up"], short = mbps(t["up"], t["total"] - t["pre"])
            if short:
                r["short"].append("upload")
            if urc == 28:
                add_note(r, "slow: upload hit the time limit")
            break
    after = traffic_total(ctl)
    if before is not None and after is not None:
        r["other"] = max(after - before - own, 0)
        if r["other"] >= max(BUSY_MIN, BUSY_SHARE * own) and own:
            add_note(r, f"busy: other apps used ~{r['other'] / 1e6:.0f} MB during this test")
    if r["short"]:
        add_note(r, f"{' and '.join(r['short'])} too quick to time reliably: use a larger --size")
    if lost:
        add_note(r, f"{lost} of {PROBES} connections failed")
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


def other_traffic(ctl, seconds=2):
    """Bytes/s going through mihomo right now (other apps), or None."""
    before = traffic_total(ctl)
    time.sleep(seconds)
    after = traffic_total(ctl)
    return None if before is None or after is None else max(after - before, 0) / seconds


def run_tests(ctl, servers, size=10, timeout=25, on_event=None):
    """Test `servers` one at a time through mihomo; mihomo's state is always restored.
    Returns (results, stopped); stopped = interrupted (Ctrl+C / SIGTERM), results then hold what was tested.

    on_event(kind, data) is called with:
      "start"    {"total", "estimate", "state"}           before the first server (state: MihomoState)
      "busy"     {"rate"}                                 other apps send > BUSY_RATE bytes/s through mihomo
      "server"   {"index", "total", "server", "left"}     a server's test begins (left: seconds, estimated)
      "pinging"  {"count"}                                all servers done, pinging them all together
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
    pinger = cf.ThreadPoolExecutor(max_workers=PING_WORKERS)
    pings, checks, logs = {}, {}, None
    try:
        rate = other_traffic(ctl)
        if rate and rate > BUSY_RATE:
            emit("busy", {"rate": rate})
        proxy = state.proxy_url()
        state.apply()
        logs = LogWatcher(ctl)
        for i, srv in enumerate(servers, 1):
            left = (time.time() - t0) / (i - 1) * (len(servers) - i + 1) if i > 1 else est
            emit("server", {"index": i, "total": len(servers), "server": srv, "left": left})
            results.append(test_server(ctl, state, proxy, srv, size, timeout, up_file, logs))
        # ping only now, after every transfer, so the test's own traffic doesn't inflate ping and jitter.
        # Servers mihomo never connected to: ping the address from the config, resolved by mihomo;
        # failed servers also get a direct TCP check of their port, outside mihomo
        need = [r for r in results if not r["ip"] or r["result"] == "failed"]
        conf = config_servers([r["name"] for r in need]) if need else {}
        targets = {r["name"]: r["ip"] for r in results if r["ip"]}
        for r in need:
            c = conf.get(r["name"]) or {}
            ip = r["ip"] or (mihomo_resolve(ctl, c["server"]) if c.get("server") else None)
            if ip:
                targets[r["name"]] = ip
            if r["result"] == "failed":
                if r["type"] in UDP_TYPES:
                    r["check"] = "not possible (UDP)"
                elif ip and c.get("port"):
                    checks[r["name"]] = (c["port"], pinger.submit(tcp_check, ip, c["port"]))
        for name, ip in targets.items():  # mihomo sometimes reports the host name it dialed
            if not is_ip(ip):
                targets[name] = mihomo_resolve(ctl, ip) or ip
        pings = {name: pinger.submit(ping, ip) for name, ip in targets.items()}
        emit("pinging", {"count": len(pings)})
        for r in results:
            if r["name"] in pings:
                r["ping"], r["loss"], r["ping_jitter"] = pings[r["name"]].result()
                if r["result"] == "failed" and r["ping"] is not None:
                    r["result"] = "unreachable"
                    if not r["error"]:
                        r["note"] = "answers ping, but no working proxy connection from this network"
            if r["name"] in checks:
                port, fut = checks[r["name"]]
                r["check"] = f"port {port} {fut.result()}"
            if r["check"]:
                add_note(r, "direct check: " + r["check"])
            if cloudflare(targets.get(r["name"]) or ""):
                add_note(r, "via Cloudflare CDN: ping shows the CDN edge, not the server"
                         + ("; Conn ms is the real latency" if r["conn"] is not None else ""))
    except KeyboardInterrupt:
        stopped = True
        emit("stopped", {})
    finally:
        state.restore()  # first: nothing below may delay or prevent it
        if logs:
            logs.stop()
        for f in list(pings.values()) + [fut for _, fut in checks.values()]:
            f.cancel()  # when stopped, don't wait for queued pings/checks at exit
        pinger.shutdown(wait=False)
        shutil.rmtree(tmp, ignore_errors=True)
    emit("restored", {"state": state})
    return results, stopped
