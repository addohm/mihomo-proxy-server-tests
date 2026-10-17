"""--trace: where the path to a server that didn't work stops, and who owns each hop."""
import ipaddress
import os
import re
import shutil
import socket
import urllib.parse

from .system import IS_MAC, IS_WIN, run

MAX_HOPS = 20
# network owners (AS numbers) of CDNs other than Cloudflare (that one is known by its address ranges)
CDN_ASNS = {54113: "Fastly", 20940: "Akamai", 16625: "Akamai", 21342: "Akamai", 60068: "CDN77",
            199524: "Gcore", 200325: "Bunny", 15133: "Edgio", 22822: "Edgio"}


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def port_trace_tool():
    """A command that traces with TCP SYNs to a port (Linux, as root), or None."""
    if IS_WIN or IS_MAC or not is_root():
        return None
    if shutil.which("traceroute"):
        return lambda ip, port: ["traceroute", "-T", "-p", str(port), "-n", "-m", str(MAX_HOPS), "-w", "1", "-q",
                                 "1", ip]
    if shutil.which("mtr"):
        return lambda ip, port: ["mtr", "--tcp", "--port", str(port), "-n", "-r", "-c", "1", "-m", str(MAX_HOPS), ip]
    return None


def path_trace_cmd(ip):
    if IS_WIN:
        return ["tracert", "-d", "-h", str(MAX_HOPS), "-w", "1000", ip]
    if IS_MAC:
        return ["traceroute6" if ":" in ip else "traceroute", "-n", "-m", str(MAX_HOPS), "-w", "1", "-q", "1", ip]
    if shutil.which("tracepath"):
        return ["tracepath", "-n", "-m", str(MAX_HOPS), ip]
    if shutil.which("traceroute"):
        return ["traceroute", "-n", "-m", str(MAX_HOPS), "-w", "1", "-q", "1", ip]
    return None


def parse_hops(out):
    """[(hop, ip | None)] from tracert / tracepath / traceroute / mtr output: a line that starts with the hop
    number, then the first IP address on it (none = no answer). Works in any language."""
    hops = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)[\s.:?|]", line)
        if not m:
            continue
        n = int(m.group(1))
        ip = None
        for tok in re.split(r"[\s()\[\]]+", line[m.end():]):
            try:
                ip = str(ipaddress.ip_address(tok.strip(",")))
                break
            except ValueError:
                pass
        if hops.get(n) is None:
            hops[n] = ip
    return sorted(hops.items())


def trace(ip, port=None):
    """Trace to ip (a TCP trace to port if given and possible). Returns {"kind", "hops", "reached"} or
    {"error"}."""
    if port:
        tool = port_trace_tool()
        if not tool:
            return {"error": "port trace: not available on Windows" if IS_WIN else
                    "port trace: run with sudo (Linux, with traceroute or mtr) to see where the port is blocked"}
        args, kind = tool(ip, port), f"port {port}"
    else:
        args, kind = path_trace_cmd(ip), "path"
        if not args:
            return {"error": "no trace tool found (install tracepath or traceroute)"}
    _, out, err = run(args, timeout=MAX_HOPS * 4 + 15)
    hops = parse_hops(out)
    if not hops:
        return {"error": f"trace failed: {(err or out)[:80]}"}
    return {"kind": kind, "hops": hops, "reached": any(h == ip for _, h in hops), "target": ip}


def is_private(ip):
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local or a in ipaddress.ip_network("100.64.0.0/10")


def _txt(ctl, name):
    st, body = ctl.call("GET", "/dns/query?" + urllib.parse.urlencode({"name": name, "type": "TXT"}), timeout=8)
    for a in ((body or {}).get("Answer") or []) if st == 200 else []:
        if a.get("type") == 16:
            return str(a.get("data", "")).strip('"')
    return ""


def _reverse(ip):
    a = ipaddress.ip_address(ip)
    if a.version == 4:
        return ".".join(reversed(ip.split("."))) + ".origin.asn.cymru.com"
    return ".".join(reversed(a.exploded.replace(":", ""))) + ".origin6.asn.cymru.com"


def owners(ctl, ips, pool):
    """ip -> (asn | None, short name) for public IPs, from Team Cymru's ASN service: TXT records through
    mihomo's own DNS (works wherever the user's config does), else whois.cymru.com on TCP 43."""
    ips = sorted({ip for ip in ips if ip and not is_private(ip)})
    origin = dict(zip(ips, pool.map(lambda ip: _txt(ctl, _reverse(ip)), ips)))
    asns = {int(v.split("|")[0].split()[0]) for v in origin.values() if v and v.split("|")[0].strip().isdigit()}
    names = dict(zip(asns, pool.map(lambda asn: _txt(ctl, f"AS{asn}.asn.cymru.com"), asns)))
    res = {}
    for ip, v in origin.items():
        asn = int(v.split("|")[0]) if v and v.split("|")[0].strip().isdigit() else None
        if asn:
            res[ip] = (asn, short_name(names.get(asn, "").split("|")[-1]))
    missing = [ip for ip in ips if ip not in res]
    if missing:
        res.update(_whois(missing))
    return res


def _whois(ips):
    try:
        with socket.create_connection(("whois.cymru.com", 43), timeout=10) as s:
            s.sendall(("begin\nverbose\n" + "\n".join(ips) + "\nend\n").encode())
            out = b""
            while True:
                b = s.recv(65536)
                if not b:
                    break
                out += b
    except OSError:
        return {}
    res = {}
    for line in out.decode("utf-8", "replace").splitlines():
        f = [x.strip() for x in line.split("|")]
        if len(f) >= 7 and f[0].isdigit():
            res[f[1]] = (int(f[0]), short_name(f[6]))
    return res


def short_name(name):
    """'CHINANET-BACKBONE No.31,Jin-rong Street, CN' / 'GOOGLE - Google LLC, US' -> 'CHINANET-BACKBONE' / 'GOOGLE'"""
    return name.strip().split(" - ")[0].split(",")[0].split(" ")[0][:30]


def label(ip, own):
    if not ip:
        return "*"
    if is_private(ip):
        return f"{ip} (private)"
    o = own.get(ip)
    return f"{ip} (AS{o[0]} {o[1]})" if o else ip


def summary(t, own):
    """One line for the Note, e.g. 'path trace: stops after hop 9: 1.2.3.4 (AS4134 CHINANET-BACKBONE)'."""
    if "error" in t:
        return t["error"]
    if t["reached"]:
        n = next(i for i, h in t["hops"] if h == t["target"])
        return f"{t['kind']} trace: reaches the server ({n} hops)"
    answered = [(i, h) for i, h in t["hops"] if h]
    if not answered:
        return f"{t['kind']} trace: no hop answered"
    i, h = answered[-1]
    if i >= MAX_HOPS:  # still on its way when the hop limit ran out
        return f"{t['kind']} trace: no answer from the server within {MAX_HOPS} hops; last hop: {label(h, own)}"
    return f"{t['kind']} trace: stops after hop {i}: {label(h, own)}"


def hop_list(t, own):
    """The whole trace, for the Markdown report; a run of hops without an answer is one item ("12-20 *")."""
    out, gap = [], []
    for i, h in t.get("hops", []) + [(None, "end")]:
        if h is None:
            gap.append(i)
            continue
        if gap:
            out.append(f"{gap[0]}-{gap[-1]} *" if len(gap) > 1 else f"{gap[0]} *")
            gap = []
        if i is not None:
            out.append(f"{i} {label(h, own)}")
    return " · ".join(out)
