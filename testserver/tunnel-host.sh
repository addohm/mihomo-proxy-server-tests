#!/usr/bin/env bash
# Prepare a Linux server (any distro with OpenSSH; mihomo not needed) to receive a tester PC's reverse SSH
# tunnel, as set up on the PC by setup-windows.ps1. Safe to re-run.
#
#   scp testserver/tunnel-host.sh root@HOST:/root/
#   ssh root@HOST "bash /root/tunnel-host.sh 'ssh-ed25519 AAAA... mihomo-tester-PC' 2222"
#
# Arguments: the PC's public key (setup-windows.ps1 shows it), the port on this server that leads to the
# PC (default 2222; one port per PC), and the user (default tunnel).
#
# The user has no shell and no password; its keys may only open their own reverse port forward, on
# 127.0.0.1 only, so the PC is reachable from this server alone:  ssh -p PORT WINDOWS_USER@127.0.0.1
# (no local forwards into this server, no agent/X11/socket forwarding, no terminal).
# A key added for a port replaces any earlier key for that port (e.g. a reinstalled PC). sshd keepalives
# (15 s x 4) free the port quickly when a tunnel drops.
set -euo pipefail

KEY="${1:?usage: tunnel-host.sh 'PUBLIC KEY' [PORT] [USER]}"
PORT="${2:-2222}"
TUSER="${3:-tunnel}"

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
[[ "$KEY" =~ ^(ssh-|ecdsa-|sk-)[^\ ]+\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo "Not an SSH public key: $KEY" >&2; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || { echo "Bad port: $PORT" >&2; exit 1; }
blob="$(cut -d' ' -f2 <<<"$KEY")"

id "$TUSER" &>/dev/null || useradd --create-home --shell "$(command -v nologin || echo /sbin/nologin)" "$TUSER"
usermod -p '*' "$TUSER"  # no password, but not "locked" (sshd refuses locked accounts even with a key)
home="$(getent passwd "$TUSER" | cut -d: -f6)"
f="$home/.ssh/authorized_keys"
mkdir -p "$home/.ssh"
touch "$f"
{ grep -vF -e "$blob" -e "permitlisten=\"127.0.0.1:$PORT\"" "$f" || true
  echo "restrict,port-forwarding,permitlisten=\"127.0.0.1:$PORT\" $KEY"; } > "$f.new"
mv "$f.new" "$f"
chown -R "$TUSER:" "$home/.ssh"
chmod 700 "$home/.ssh"
chmod 600 "$f"
restorecon -R "$home/.ssh" 2>/dev/null || true

if grep -qiE '^\s*Include\s+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config; then
    # The key option "port-forwarding" also allows local forwards (-L) into this server (e.g. mihomo's
    # proxy port), so sshd itself allows this user remote forwards only. The Match block ends with the file.
    cat > /etc/ssh/sshd_config.d/40-mihomo-tunnel.conf <<CONF
ClientAliveInterval 15
ClientAliveCountMax 4
Match User $TUSER
    AllowTcpForwarding remote
    X11Forwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitTTY no
CONF
else
    echo "sshd_config has no sshd_config.d include; add to its end by hand: 'Match User $TUSER' with" \
         "'AllowTcpForwarding remote', and 'ClientAliveInterval 15' / 'ClientAliveCountMax 4' before it." >&2
    exit 1
fi
sshd -t
systemctl reload sshd 2>/dev/null || systemctl reload ssh

echo "Ready: port $PORT -> the PC with key ...${blob: -12} (user $TUSER, $(grep -c . "$f") key(s) in total)."
echo "Once its tunnel is up, from this server:  ssh -p $PORT WINDOWS_USER@127.0.0.1"
