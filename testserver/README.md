# Test tooling

Tools for testing `mihomo_speedtest.py` itself on machines other than your own: a Linux test server, and
Windows tester PCs reached through a reverse SSH tunnel. None of this is needed to *use* the speed test.

| File | Runs on | What it does |
|---|---|---|
| `setup.sh` | Fedora VPS, as root | Installs mihomo as a service from a subscription URL: the Linux test server |
| `variant.py` | test server | Switches mihomo into one config variant (controller, secret, port, TUN, source) |
| `matrix.py` | test server | Runs the speed test on every variant and checks it found, tested and restored |
| `setup-windows.ps1` | Windows 11 PC | Sets up a tester PC and keeps a reverse SSH tunnel to a server |
| `tunnel-host.sh` | any Linux server, as root | Lets one tester PC's tunnel in, and nothing else |

The scripts' headers have every option; this page is the workflow.

## Linux test server

```bash
scp testserver/setup.sh testserver/tunnel-host.sh root@HOST:/root/
ssh root@HOST "SUB_URL='https://...' bash /root/setup.sh"     # TUN=1: TUN on at rest
```

mihomo then runs as `mihomo.service` with its config in `/etc/mihomo/`, and everything listens on
127.0.0.1 only. Re-run with a new `SUB_URL` to change the subscription. Never commit the subscription URL
or anything from `/etc/mihomo/`: they contain credentials.

**Run the speed test there:**

```bash
scp -r mihomo_speedtest.py mihomotest root@HOST:/root/mst/
ssh root@HOST "python3 /root/mst/mihomo_speedtest.py --filter 'Japan'"
```

**Config matrix:** run it before every release and after changes to discovery, selection, state or
restore. It toggles TUN, so start it detached and wait for "variants passed" in the log:

```bash
scp testserver/variant.py testserver/matrix.py root@HOST:/root/testserver/
ssh root@HOST 'SSH_IP=${SSH_CLIENT%% *} setsid nohup python3 /root/testserver/matrix.py \
    /root/mst/mihomo_speedtest.py > /root/matrix.log 2>&1 < /dev/null &'
ssh root@HOST 'tail -n 20 /root/matrix.log'
```

`python3 /root/testserver/variant.py` on its own puts the default config back.

## Windows tester PC

A Windows PC (e.g. in mainland China, behind a dynamic IP) that we reach over SSH even though nothing can
connect to it. The PC keeps an SSH connection open *out* to a server, and that connection carries a port
on the server back to the PC's own SSH server:

```
you --ssh--> server (127.0.0.1:2222) ==tunnel==> PC (127.0.0.1:22)
```

### 1. Get the script onto the PC

Download the file itself, not GitHub's web page about it. Open `testserver/setup-windows.ps1` on GitHub
and use **Raw** (or the download button). If PowerShell then reports `Unexpected token` errors with
`<script>` or `github.githubassets.com` in them, it saved the HTML page instead.

### 2. Before running it

- Turn **TUN off** in the mihomo client (Clash Verge Rev, Mihomo Party, ...), and stop or disconnect any
  other VPN. The script turns VPN adapters off anyway, because the tunnel must go direct; otherwise it
  would go through the mihomo the tests keep switching.
- Have the public key(s) ready for whoever will log in to the PC (one line, `ssh-ed25519 AAAA...`).

### 3. Run it

From PowerShell in the folder that contains the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
```

(or right-click → **Run with PowerShell**). `.\setup-windows.ps1` on its own fails on a fresh Windows
("running scripts is disabled"). The Bypass only applies to that one run.

The script asks for administrator rights, then:

1. installs OpenSSH and Python 3 (+ PyYAML). The PC's SSH server listens on 127.0.0.1 only and accepts
   keys only, so the tunnel is the only way in;
2. asks for the public keys allowed to log in (paste one per line, Enter when done);
3. stops the PC from sleeping on mains power;
4. disconnects VPN connections and disables VPN/TUN adapters, listing each with its undo command;
5. creates the PC's own tunnel key and asks:
   - **server address**: the server's IP or host name;
   - **server SSH port** (22) and **user on the server** (`tunnel`);
   - **port on the server that leads to this PC** (2222): one port per PC;
   - **reconnect attempts before giving up**: 0 (default) keeps the tunnel open indefinitely; N gives up
     after N failed attempts in a row, until the next restart or re-run;
6. shows the PC's public key and **one command for the server**, then waits for Enter.

### 4. Let the PC in on the server

Copy `tunnel-host.sh` to the server once, then run the command the PC showed, as root:

```bash
scp testserver/tunnel-host.sh root@SERVER:/root/
ssh root@SERVER "bash /root/tunnel-host.sh 'ssh-ed25519 AAAA... mihomo-tester-PC' 2222 tunnel"
```

This creates the user `tunnel` (no shell, no password). Its key may only open its own port on the
server's 127.0.0.1: no other ports, no forwards into the server, no terminal. A new key for the same port
replaces the old one (e.g. after reinstalling the PC). The server doesn't need mihomo.

Back on the PC, press Enter. The script starts the tunnel and says **"Tunnel is up"**, or shows the last
lines of the log if it isn't.

### 5. Log in to the PC

From the server:

```bash
ssh -p 2222 WINDOWS_USER@127.0.0.1
```

Or directly from your own machine with a jump through the server, e.g. in `~/.ssh/config`:

```
Host tester-pc
    HostName 127.0.0.1
    Port 2222
    User WINDOWS_USER
    IdentityFile ~/.ssh/YOUR_KEY
    ProxyJump root@SERVER
```

The shell is PowerShell. `WINDOWS_USER` is the name the script printed (the PC's local account name).

### Keeping it running

- The tunnel is a scheduled task, **mihomo-tester tunnel**, running as SYSTEM from startup; no one needs
  to be signed in. Keepalives every 15 s notice a dead tunnel within a minute, and it reconnects every
  15 s (indefinitely, or up to the set number of attempts).
- **The server's address changed?** Re-run the script on the PC, enter the new address, press Enter for
  everything else. The new server also needs step 4.
- **Unattended test runs**: the mihomo client only starts when someone signs in, so turn on Windows
  automatic sign-in and set the client to start at login. Start long test runs detached and read their
  log afterwards, as with the matrix.

### When it doesn't connect

The log is `C:\ProgramData\mihomo-tester\tunnel.log` (readable by administrators):

| Log says | Meaning |
|---|---|
| `Permission denied (publickey)` | step 4 not done, or done with another key or user |
| `remote port forwarding failed` | wrong port for this key, or the port is still held by a dropped tunnel (wait a minute) |
| `Connection timed out` / `refused` | wrong address or SSH port, or a firewall / the network blocks it |
| `gave up after N attempts` | the retry limit was reached; restart the PC or re-run the script |

Other checks: `Get-ScheduledTask 'mihomo-tester tunnel'` (state should be Running), and on the server
`ss -tlnp | grep 2222` (the port is listening while the tunnel is up).

### Removing it

```powershell
Unregister-ScheduledTask 'mihomo-tester tunnel' -Confirm:$false
Get-Process ssh -ErrorAction SilentlyContinue | Stop-Process
Remove-Item -Recurse C:\ProgramData\mihomo-tester
```

On the server, delete the PC's line from `~tunnel/.ssh/authorized_keys`. Re-enable any adapter the script
disabled with the `Enable-NetAdapter` command it printed.
