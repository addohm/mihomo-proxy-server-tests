#!/usr/bin/env python3
"""Switch the test server's mihomo into a config variant, restart it and wait until its API answers.

    python3 variant.py                         back to the default setup
    python3 variant.py controller=unix tun=on  defaults + these changes

Changes (anything not given keeps the default):
    controller=tcp|unix          API on 127.0.0.1:9090, or only on the Unix socket /etc/mihomo/mihomo.sock
    secret=yes|no                API secret as in the default config, or none
    port=mixed|socks|http|none   local proxy port: mixed-port 7890, socks-port 7891, port 7892, or none
    tun=off|on                   TUN at rest; the SSH client's IP ($SSH_CLIENT / $SSH_IP) is excluded from
                                 TUN routing so the session survives
    source=profile|provider      servers listed in the config, or loaded from a file proxy provider

Builds /etc/mihomo/config.yaml from /etc/mihomo/config.default.yaml (created from the current config.yaml
the first time). Run as root on the test server. Prints the API address (not the secret) as JSON.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import yaml

DIR = "/etc/mihomo"
DEFAULT = os.path.join(DIR, "config.default.yaml")
CONFIG = os.path.join(DIR, "config.yaml")
SOCKET = os.path.join(DIR, "mihomo.sock")
CHOICES = {"controller": ("tcp", "unix"), "secret": ("yes", "no"), "port": ("mixed", "socks", "http", "none"),
           "tun": ("off", "on"), "source": ("profile", "provider")}
GROUP_TYPES = {"selector", "urltest", "fallback", "loadbalance", "relay", "direct", "reject",
               "rejectdrop", "pass", "passrule", "compatible", "dns", "smart"}
PORTS = {"mixed": ("mixed-port", 7890), "socks": ("socks-port", 7891), "http": ("port", 7892)}


def parse(argv):
    v = {k: c[0] for k, c in CHOICES.items()}
    for a in argv:
        k, _, val = a.partition("=")
        if k not in CHOICES or val not in CHOICES[k]:
            sys.exit(f"bad change {a!r}; choose from: " +
                     "  ".join(f"{k}={'|'.join(c)}" for k, c in CHOICES.items()))
        v[k] = val
    return v


def build(v, ssh_ip=""):
    if not os.path.exists(DEFAULT):
        shutil.copy2(CONFIG, DEFAULT)
    cfg = yaml.safe_load(open(DEFAULT, encoding="utf-8"))
    for key in ("mixed-port", "socks-port", "port", "external-controller", "external-controller-unix"):
        cfg.pop(key, None)
    if v["controller"] == "tcp":
        cfg["external-controller"] = "127.0.0.1:9090"
    else:
        cfg["external-controller-unix"] = SOCKET
    if v["secret"] == "no":
        cfg.pop("secret", None)
    if v["port"] != "none":
        key, port = PORTS[v["port"]]
        cfg[key] = port
    tun = cfg.setdefault("tun", {})
    tun["enable"] = v["tun"] == "on"
    if ssh_ip:
        excl = ssh_ip + ("/128" if ":" in ssh_ip else "/32")
        tun["route-exclude-address"] = sorted(set(tun.get("route-exclude-address") or []) | {excl})
    if v["source"] == "provider":
        os.makedirs(os.path.join(DIR, "providers"), exist_ok=True)
        with open(os.path.join(DIR, "providers", "local.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"proxies": cfg.pop("proxies", [])}, f, allow_unicode=True, sort_keys=False)
        cfg["proxy-providers"] = {"local": {"type": "file", "path": "./providers/local.yaml",
                                           "health-check": {"enable": False}}}
        cfg["proxy-groups"] = [{"name": "PROXY", "type": "select", "use": ["local"]}]
        cfg["rules"] = ["MATCH,PROXY"]
    with open(CONFIG, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    subprocess.run(["chown", "-R", "mihomo:mihomo", DIR], check=True)
    os.chmod(CONFIG, 0o640)
    return {"unix": SOCKET if v["controller"] == "unix" else "",
            "tcp": "127.0.0.1:9090" if v["controller"] == "tcp" else "",
            "secret": str(cfg.get("secret") or "")}


def api(info, method, path, body=None, timeout=10):
    """Call the mihomo API with curl (independent of the code under test). Returns parsed JSON or None."""
    cmd = ["curl", "-s", "-m", str(timeout), "-X", method]
    if info["unix"]:
        cmd += ["--unix-socket", info["unix"]]
        url = "http://localhost" + path
    else:
        url = "http://" + info["tcp"] + path
    if info["secret"]:
        cmd += ["-H", "Authorization: Bearer " + info["secret"]]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd + [url], capture_output=True, text=True).stdout
    try:
        return json.loads(out) if out.strip() else None
    except ValueError:
        return None


def apply(v, ssh_ip=""):
    info = build(v, ssh_ip)
    subprocess.run(["systemctl", "restart", "mihomo"], check=True)
    for _ in range(60):
        if (api(info, "GET", "/version", timeout=2) or {}).get("version"):
            return info
        time.sleep(1)
    subprocess.run(["journalctl", "-u", "mihomo", "-n", "20", "--no-pager"])
    raise SystemExit(f"mihomo did not come up with {v}")


if __name__ == "__main__":
    ssh = os.environ.get("SSH_IP") or os.environ.get("SSH_CLIENT", "").split(" ")[0]
    variant = parse(sys.argv[1:])
    info = apply(variant, ssh)
    print(json.dumps({**info, "secret": "set" if info["secret"] else "none"}))
