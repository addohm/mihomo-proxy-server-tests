#!/usr/bin/env bash
# Set up a Fedora VPS as the remote test server for mihomo_speedtest.py.
#
# Installs curl/ping/python3/PyYAML and a plain mihomo core (no GUI client) as a systemd service, with
# its config built from a subscription URL. Safe to re-run (e.g. with a new subscription URL).
#
#   scp testserver/setup.sh root@HOST:/root/
#   ssh root@HOST "SUB_URL='https://...' bash /root/setup.sh"
#
# Settings (environment variables):
#   SUB_URL         required. Subscription URL (Clash/mihomo YAML profile, or a list of share links)
#   MIHOMO_VERSION  e.g. v1.19.32. Default: the latest release
#   TUN             1 = enable TUN at rest, as many clients do (the speed test turns it off and back on).
#                   The SSH client's IP is excluded from TUN routing so the session survives. Default 0.
#   SUB_UA          User-Agent for fetching the subscription (default looks like a mihomo client, so
#                   providers return a full mihomo profile)
#
# Everything listens on 127.0.0.1 only: controller 127.0.0.1:9090 (random secret), mixed-port 7890.
set -euo pipefail

SUB_URL="${SUB_URL:?set SUB_URL to the subscription URL}"
MIHOMO_VERSION="${MIHOMO_VERSION:-latest}"
TUN="${TUN:-0}"
DIR=/etc/mihomo

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
grep -qi fedora /etc/os-release || echo "Warning: not Fedora; continuing anyway." >&2

echo "==> Packages"
dnf -y -q install curl iputils python3 python3-pyyaml gzip </dev/null

if [ "$MIHOMO_VERSION" = latest ]; then
    MIHOMO_VERSION="$(curl -fsSL https://api.github.com/repos/MetaCubeX/mihomo/releases/latest \
        | python3 -I -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')"
fi
SUB_UA="${SUB_UA:-clash.meta mihomo/${MIHOMO_VERSION#v}}"
echo "==> mihomo $MIHOMO_VERSION"
case "$(uname -m)" in
    x86_64)  asset="mihomo-linux-amd64-compatible-$MIHOMO_VERSION.gz" ;;  # runs on any x86-64 CPU
    aarch64) asset="mihomo-linux-arm64-$MIHOMO_VERSION.gz" ;;
    *) echo "Unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac
installed="$(/usr/local/bin/mihomo -v 2>/dev/null || true)"  # read it all: an early-closed pipe fails under pipefail
if [[ "$installed" != *"$MIHOMO_VERSION"* ]]; then
    curl -fsSL "https://github.com/MetaCubeX/mihomo/releases/download/$MIHOMO_VERSION/$asset" \
        | gunzip > /usr/local/bin/mihomo.new
    chmod 755 /usr/local/bin/mihomo.new
    mv /usr/local/bin/mihomo.new /usr/local/bin/mihomo
    restorecon /usr/local/bin/mihomo 2>/dev/null || true
fi
/usr/local/bin/mihomo -v | sed -n 1p

echo "==> Config from subscription"
id mihomo &>/dev/null || useradd --system --home-dir "$DIR" --shell /sbin/nologin mihomo
mkdir -p "$DIR"
SSH_IP="${SSH_CLIENT:-}"; SSH_IP="${SSH_IP%% *}"
curl -fsSL -A "$SUB_UA" "$SUB_URL" -o "$DIR/subscription.raw"
SUB_URL="$SUB_URL" TUN="$TUN" SSH_IP="$SSH_IP" python3 -I - "$DIR" <<'PY'
import os, secrets, sys, yaml

d = sys.argv[1]
raw = open(os.path.join(d, "subscription.raw"), "rb").read().decode("utf-8", "replace")
try:
    cfg = yaml.safe_load(raw)
except yaml.YAMLError:
    cfg = None
if isinstance(cfg, dict) and (cfg.get("proxies") or cfg.get("proxy-providers")):
    print(f"   full profile: {len(cfg.get('proxies') or [])} servers, "
          f"{len(cfg.get('proxy-providers') or {})} providers")
else:  # share links / base64: let mihomo fetch and convert them as a provider
    print("   not a mihomo profile: using it as a proxy provider")
    cfg = {"proxy-providers": {"sub": {"type": "http", "url": os.environ["SUB_URL"], "interval": 86400,
                                       "path": "./providers/sub.yaml"}},
           "proxy-groups": [{"name": "PROXY", "type": "select", "use": ["sub"]}],
           "rules": ["MATCH,PROXY"]}

for k in ("port", "socks-port", "redir-port", "tproxy-port", "external-controller-tls",
          "external-controller-unix", "external-controller-pipe", "external-ui", "authentication"):
    cfg.pop(k, None)
old = {}
p = os.path.join(d, "config.yaml")
if os.path.exists(p):
    old = yaml.safe_load(open(p)) or {}
cfg.update({"mixed-port": 7890, "allow-lan": False, "bind-address": "127.0.0.1",
            "external-controller": "127.0.0.1:9090",
            "secret": old.get("secret") or secrets.token_hex(16),
            "mode": "rule", "log-level": "info"})
tun = {"enable": os.environ["TUN"] == "1", "stack": "mixed", "auto-route": True,
       "auto-detect-interface": True, "dns-hijack": ["any:53"]}
if os.environ.get("SSH_IP"):
    tun["route-exclude-address"] = [os.environ["SSH_IP"] + ("/128" if ":" in os.environ["SSH_IP"] else "/32")]
cfg["tun"] = tun
if isinstance(cfg.get("dns"), dict) and cfg["dns"].get("listen"):
    cfg["dns"]["listen"] = "127.0.0.1:1053"  # port 53 is taken by systemd-resolved
with open(p, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
PY
cp -p "$DIR/config.yaml" "$DIR/config.default.yaml"  # the default setup that variant.py builds on
chown -R mihomo:mihomo "$DIR"
chmod 750 "$DIR"
chmod 640 "$DIR/config.yaml" "$DIR/config.default.yaml" "$DIR/subscription.raw"

echo "==> systemd service"
cat > /etc/systemd/system/mihomo.service <<'UNIT'
[Unit]
Description=mihomo (speed-test target)
After=network-online.target
Wants=network-online.target

[Service]
User=mihomo
ExecStart=/usr/local/bin/mihomo -d /etc/mihomo
Restart=on-failure
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q mihomo
systemctl restart mihomo

echo "==> Waiting for the controller"
SECRET="$(python3 -I -c 'import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))["secret"])' "$DIR/config.yaml")"
for _ in $(seq 60); do
    curl -fs -H "Authorization: Bearer $SECRET" http://127.0.0.1:9090/version >/dev/null && break
    sleep 1
done
curl -fs -H "Authorization: Bearer $SECRET" http://127.0.0.1:9090/proxies | python3 -I -c '
import json, sys
groups = {"selector", "urltest", "fallback", "loadbalance", "relay", "direct", "reject", "rejectdrop",
          "pass", "passrule", "compatible", "dns", "smart"}
p = json.load(sys.stdin)["proxies"]
n = sum(str(v.get("type", "")).lower() not in groups for v in p.values())
print(f"   mihomo is up: {n} servers")' \
    || { echo "mihomo did not come up. Logs:" >&2; journalctl -u mihomo -n 30 --no-pager >&2; exit 1; }

echo
echo "Done. Controller 127.0.0.1:9090, secret in $DIR/config.yaml, TUN $([ "$TUN" = 1 ] && echo on || echo off)."
