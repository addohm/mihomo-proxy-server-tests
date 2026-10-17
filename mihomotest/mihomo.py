"""Finding the running mihomo, talking to its API, and putting it into test mode and back."""
import functools
import glob
import ipaddress
import json
import os
import re
import signal
import socket
import threading
import time
import urllib.parse

from .system import IS_WIN, ps_exe, run

GROUP_TYPES = {"selector", "urltest", "fallback", "loadbalance", "relay", "direct", "reject",
               "rejectdrop", "pass", "passrule", "compatible", "dns", "smart"}
STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK")


class MihomoError(Exception):
    """mihomo could not be reached or gave an unusable answer."""


class SecretNeeded(MihomoError):
    """mihomo was found, but its API needs a secret. `.controllers` are the locked candidates."""

    def __init__(self, controllers):
        super().__init__("mihomo's API needs the secret")
        self.controllers = controllers


class Controller:
    """mihomo external-controller REST API over TCP, a Unix socket or a Windows named pipe."""

    def __init__(self, kind, addr, secret="", source=""):
        self.kind, self.addr, self.secret, self.source = kind, addr, secret or "", source

    def __str__(self):
        return {"tcp": f"http://{self.addr}", "unix": f"unix:{self.addr}", "pipe": self.addr}[self.kind]

    def _open_pipe(self):
        deadline = time.time() + 2
        while True:
            try:
                return open(self.addr, "r+b", buffering=0)
            except OSError as e:  # all instances busy (e.g. the log stream holds one): wait for the next
                if getattr(e, "winerror", None) != 231 or time.time() > deadline:  # ERROR_PIPE_BUSY
                    raise
                time.sleep(0.05)

    def _pipe_exchange(self, raw):
        with self._open_pipe() as f:
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

    def _connect(self, timeout):
        if self.kind == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self.addr)
            return sock
        host, port = self.addr.rsplit(":", 1)
        return socket.create_connection((host.strip("[]"), int(port)), timeout=timeout)

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        head = (f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\n")
        if self.secret:
            head += f"Authorization: Bearer {self.secret}\r\n"
        return head.encode() + b"\r\n" + data

    def _exchange(self, raw, timeout):
        if self.kind == "pipe":
            # pipe reads can't time out by themselves: run in a daemon thread and give up after `timeout`
            box = {}

            def work():
                try:
                    box["data"] = self._pipe_exchange(raw)
                except OSError as e:
                    box["err"] = e
            t = threading.Thread(target=work, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                raise OSError(f"no answer from {self.addr} within {timeout} s")
            if "err" in box:
                raise box["err"]
            return box["data"]
        with self._connect(timeout) as sock:
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
        try:
            resp = self._exchange(self._request(method, path, body), timeout)
            hdr, _, payload = resp.partition(b"\r\n\r\n")
            status = int(hdr.split(b" ", 2)[1])
        except (OSError, ValueError, IndexError):
            return 0, None
        try:
            return status, json.loads(payload) if payload.strip() else None
        except ValueError:
            return status, None

    def stream(self, path):
        """Open a long-lived GET (e.g. /logs). Returns (closer, binary file positioned after the headers);
        raises OSError if it can't be opened or isn't HTTP 200."""
        if self.kind == "pipe":
            f = self._open_pipe()
            f.write(self._request("GET", path))
            closer = f
        else:
            sock = self._connect(10)
            sock.settimeout(None)
            sock.sendall(self._request("GET", path))
            f, closer = sock.makefile("rb"), sock
        status = f.readline().split(b" ", 2)
        if len(status) < 2 or status[1] != b"200":
            closer.close()
            raise OSError(f"{path}: HTTP {status[1:2]}")
        while f.readline().strip():  # skip the headers
            pass
        return closer, f

    def version(self, timeout=3):
        """(http_status, mihomo version or None)."""
        status, body = self.call("GET", "/version", timeout=timeout)
        ok = status == 200 and isinstance(body, dict) and "version" in body
        return status, body["version"] if ok else None


class LogWatcher:
    """Collects mihomo's warning/error log messages in the background. The API streams them even when the
    client's own log level is quieter (checked with log-level silent)."""

    def __init__(self, ctl):
        self.ctl, self.messages, self._stopped, self._closer = ctl, [], False, None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stopped:
            try:
                self._closer, f = self.ctl.stream("/logs?level=warning")
                for line in f:
                    try:
                        self.messages.append(str(json.loads(line).get("payload", "")))
                    except ValueError:
                        pass
            except OSError:
                pass
            if not self._stopped:
                time.sleep(1)  # reconnect (e.g. mihomo restarted its API)

    def mark(self):
        return len(self.messages)

    def since(self, mark):
        return self.messages[mark:]

    def stop(self):
        self._stopped = True
        closer = self._closer
        if isinstance(closer, socket.socket):
            try:
                closer.shutdown(socket.SHUT_RDWR)  # wakes the blocked read
                closer.close()
            except OSError:
                pass
        elif closer:  # a Windows pipe: closing it while the read is blocked can hang, so don't wait for it
            threading.Thread(target=closer.close, daemon=True).start()


@functools.lru_cache(maxsize=None)
def running_cores():
    """Command lines of running mihomo / clash processes."""
    if IS_WIN:
        if not ps_exe():
            return []
        cmd = [ps_exe(), "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'mihomo|clash' } | "
               "ForEach-Object { $_.CommandLine }"]
    else:
        cmd = ["ps", "-eo", "args"]
    _, out, _ = run(cmd, timeout=15)
    return [l.strip() for l in out.splitlines() if re.search(r"mihomo|clash", l, re.I)]


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


@functools.lru_cache(maxsize=None)
def load_yaml(path):
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - missing PyYAML, unreadable or invalid file
        return None


@functools.lru_cache(maxsize=None)
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


def mihomo_pipes():
    """Windows: named pipes with "mihomo" in their name (e.g. Clash Verge Rev's
    verge-mihomo-production-<id>, Clash Party's MihomoParty\\mihomo-user-<user>-<n>). A normal user can't read
    the command line of a core that runs as a service (Clash Verge Rev's service mode), so -ext-ctl-pipe isn't
    visible there, but the list of pipes is. Other apps' pipes (e.g. a client's own service pipe) are left out."""
    if not IS_WIN:
        return []
    try:
        names = os.listdir("\\\\.\\pipe\\")
    except OSError:
        return []
    return [n for n in names if "mihomo" in n.lower() and not n.lower().endswith("-service")]


def find_controller(api=None, secret=""):
    """Locate the running mihomo's API. Returns (Controller, version).
    Raises SecretNeeded if it is only reachable with a secret, MihomoError if it isn't found at all."""
    secret = secret or os.environ.get("MIHOMO_SECRET", "")
    cands = []

    def add(kind, addr, sec, source):
        if addr and all(x.addr != addr for x in cands):
            cands.append(Controller(kind, addr, secret or sec, source))

    if api:
        if api.lower().startswith("unix:"):
            add("unix", api[5:], "", "--api")
        elif api.startswith("\\\\.\\pipe\\"):
            add("pipe", api, "", "--api")
        else:
            add("tcp", tcp_addr(api), "", "--api")
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
        for name in mihomo_pipes():  # what a normal user can see of a core run as a service (see below)
            add("pipe", "\\\\.\\pipe\\" + name, "", "pipe list")
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
        status, version = ctl.version(timeout=1.5)
        if version:
            return ctl, version
        if status == 401:
            locked.append(ctl)
    if locked:
        raise SecretNeeded(locked)
    raise MihomoError("Could not reach a running mihomo.")


class MihomoState:
    """Puts mihomo into test state (TUN off, global mode, a local proxy port) and restores it."""

    def __init__(self, ctl):
        self.ctl = ctl
        st, conf = ctl.call("GET", "/configs")
        if st != 200 or not isinstance(conf, dict):
            raise MihomoError(f"mihomo API error reading /configs (HTTP {st}).")
        self.conf = conf
        self.mode = conf.get("mode", "rule")
        self.tun = bool((conf.get("tun") or {}).get("enable"))
        st, g = ctl.call("GET", "/proxies/GLOBAL")
        self.global_now = (g or {}).get("now") if st == 200 else None
        self.saved = {}  # selector group -> its selection before the test (filled as groups are touched)
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

    def select(self, srv):
        """Select a server by walking its path of selector groups (GLOBAL -> ... -> server)."""
        for group, choice in srv.get("path") or []:
            path = "/proxies/" + urllib.parse.quote(group, safe="")
            if group not in self.saved:
                st, g = self.ctl.call("GET", path)
                self.saved[group] = (g or {}).get("now") if st == 200 else None
            st, _ = self.ctl.call("PUT", path, {"name": choice})
            if st not in (200, 204):
                return False
        return bool(srv.get("path"))

    def restore(self):
        if not self.applied:
            return
        self.applied = False
        held = {}  # a second Ctrl+C (or SIGTERM/HUP) must not cut the restore short
        for sig in STOP_SIGNALS:
            try:
                held[sig] = signal.signal(getattr(signal, sig), signal.SIG_IGN)
            except (AttributeError, OSError, ValueError):  # ValueError: not the main thread
                pass
        try:
            saved = dict(self.saved, GLOBAL=self.global_now)  # inner groups first, GLOBAL last
            for group in sorted(saved, key=lambda g: g == "GLOBAL"):
                if saved[group]:
                    self.ctl.call("PUT", "/proxies/" + urllib.parse.quote(group, safe=""), {"name": saved[group]})
            self.ctl.call("PATCH", "/configs", {"mode": self.mode})
            if self.opened_port:
                self.ctl.call("PATCH", "/configs", {"mixed-port": 0})
            if self.tun:
                self.ctl.call("PATCH", "/configs", {"tun": {"enable": True}})
        finally:
            for sig, handler in held.items():
                signal.signal(getattr(signal, sig), handler)

    def describe(self):
        return (f"mode '{self.mode}', TUN {'on' if self.tun else 'off'}"
                + (f", GLOBAL '{self.global_now}'" if self.global_now else ""))


def list_servers(ctl):
    """[{name, type, path}] of every server (not group). Servers loaded from proxy providers aren't in
    GLOBAL's list, so each server gets the chain of selector groups that reaches it from GLOBAL:
    path = [("GLOBAL", "PROXY"), ("PROXY", server)]; None if no selector reaches it (it can't be selected).
    Order: as found walking from GLOBAL (so GLOBAL's own order first), then unreachable ones."""
    st, body = ctl.call("GET", "/proxies", timeout=20)
    if st != 200 or not body:
        raise MihomoError(f"mihomo API error reading /proxies (HTTP {st}).")
    allp = body.get("proxies", {})
    types = {n: str(v.get("type", "?")).lower() for n, v in allp.items()}
    st, body = ctl.call("GET", "/providers/proxies", timeout=20)
    for prov in ((body or {}).get("providers") or {}).values() if st == 200 else []:
        if str(prov.get("vehicleType", "")).lower() == "compatible":  # mihomo's own, mirrors /proxies
            continue
        for p in prov.get("proxies") or []:
            types.setdefault(p.get("name"), str(p.get("type", "?")).lower())
    is_server = lambda n: n in types and types[n] not in GROUP_TYPES
    paths, queue, seen = {}, [("GLOBAL", [])], {"GLOBAL"}
    while queue:
        group, path = queue.pop(0)
        for member in (allp.get(group) or {}).get("all", []):
            step = path + [(group, member)]
            if is_server(member):
                paths.setdefault(member, step)
            elif types.get(member) == "selector" and member not in seen:
                seen.add(member)
                queue.append((member, step))
    order = list(paths) + [n for n in types if is_server(n) and n not in paths]
    return [{"name": n, "type": types[n], "path": paths.get(n)} for n in order]


def _provider_files(cfg, config_path):
    """Local copies of a config's proxy providers (mihomo keeps them relative to its home directory)."""
    home = os.path.dirname(config_path)
    files = []
    for name, prov in (cfg.get("proxy-providers") or {}).items():
        if not isinstance(prov, dict):
            continue
        if prov.get("path"):
            files.append(os.path.normpath(os.path.join(home, str(prov["path"]))))
        else:  # no path: mihomo names the file after a hash of the URL
            files += glob.glob(os.path.join(home, "proxies", "*"))
    return files


def config_servers(names):
    """name -> {server, port, type} from the config (and its provider files) that matches the most
    server names. Used for the fallback ping and the direct TCP check."""
    best, want = {}, set(names)
    for path in config_files():
        cfg = load_yaml(path)
        if not cfg:
            continue
        proxies = list(cfg.get("proxies") or [])
        for f in _provider_files(cfg, path):
            proxies += (load_yaml(f) or {}).get("proxies") or [] if os.path.isfile(f) else []
        m = {p["name"]: {"server": str(p.get("server", "")), "port": p.get("port"),
                         "type": str(p.get("type", "")).lower()}
             for p in proxies if isinstance(p, dict) and p.get("name") in want and p.get("server")}
        if len(m) > len(best):
            best = m
    return best


def mihomo_resolve(ctl, host):
    """Resolve with mihomo's own DNS (its nameserver-policy etc.). IPv4 first, then IPv6."""
    host = host.strip("[]")  # some profiles write IPv6 servers as "[2001:db8::1]"
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    for qtype, rtype in (("A", 1), ("AAAA", 28)):
        st, body = ctl.call("GET", "/dns/query?" + urllib.parse.urlencode({"name": host, "type": qtype}),
                            timeout=8)
        for a in ((body or {}).get("Answer") or []) if st == 200 else []:
            if a.get("type") == rtype:
                return a.get("data")
    return None


def remote_ip(ctl, name):
    """IP (or, for some protocols, host name) mihomo actually dialed for this server, from its live
    connection list."""
    st, body = ctl.call("GET", "/connections", timeout=5)
    for conn in ((body or {}).get("connections") or []) if st == 200 else []:
        if name in (conn.get("chains") or []):
            rd = (conn.get("metadata") or {}).get("remoteDestination") or ""
            ip = rd.rsplit(":", 1)[0].strip("[]") if rd.count(":") == 1 or rd.startswith("[") else rd
            if ip:
                return ip
    return None


def traffic_total(ctl):
    """Bytes mihomo has carried since it started (every connection, both directions), or None."""
    st, body = ctl.call("GET", "/connections", timeout=5)
    if st != 200 or not isinstance(body, dict):
        return None
    return (body.get("downloadTotal") or 0) + (body.get("uploadTotal") or 0)
