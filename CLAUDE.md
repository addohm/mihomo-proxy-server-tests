# CLAUDE.md

`mihomo_speedtest.py` tests every server in a **running** mihomo, exactly as mihomo uses it, and writes a
Markdown + CSV report. The launcher only starts `mihomotest/cli.py`; the code lives in the `mihomotest/`
package. `context.md` is the original handoff (2026-10-09), kept as a frozen snapshot for background; this
file is the living summary. Keep this file, README, ROADMAP and the CLI docstring (`mihomotest/cli.py`, also
the `--help` text) in sync when behaviour or options change, in the same commit. At every milestone (e.g. a
roadmap step done) also read all of them end to end and fix anything stale (user's rule, 2026-10-16).

## Hard rules

- **Never run or test the script locally, on any machine (not even in the background), unless the user
  explicitly says to.** It turns TUN off and switches mode/selection on a live client. By default, give the
  exact command; the user runs it and reports back.
- When the user does ask for a test, run it **only on the remote test server over SSH** — never locally.
  See "Remote test server" below.
- Static checks are fine: `python3 -m py_compile mihomo_speedtest.py mihomotest/*.py`, and importing the
  package (it has no side effects on import).
- Keep changes small and inside the design below. The user has twice asked for simplicity after over-engineering.
- **Public tool.** Stay universal: Linux, Windows, macOS and **any mihomo-based client** (Clash Verge Rev,
  Mihomo Party, Nyanpasu, FlClash, ClashX Meta, plain mihomo) and any configuration (TCP/Unix/pipe controller,
  with or without secret, mixed/socks/http port or none, TUN on or off, profiles or providers). Never tune it
  to one particular machine, client or network.
- Standard library only. PyYAML stays optional. No mihomo binary needed.

## What it must be (settled — do not redesign)

A simple, true test of what mihomo does with each server in its running config. Run → test → table →
auto-save `.md` + `.csv`. No prompts (the only one allowed: the controller secret when it can't be found).
Failed servers are reported, with their cause; they are never skipped.

Explicitly rejected — do not reintroduce:
- An "isolated" temporary mihomo core built from config files (had to replicate DNS / `nameserver-policy`).
- Relay / `dialer-proxy` re-tests, binding to the physical interface, auto mode selection, confirmation prompts.
- Any routing "shenanigans".
- Skipping transfers for servers that look dead ("if it starts, it starts" — slow starters are flagged instead).

## Usage

```bash
python3 mihomo_speedtest.py                                # everything, no prompts
python3 mihomo_speedtest.py --filter "Japan|Hong Kong"     # subset by regex
```

Options: `--filter REGEX`, `--size MB` (default 10; upload is half; the only way to change the transfer
size), `--timeout SEC` (25) — both must be > 0,
`--api ADDR` (`127.0.0.1:9090`, `unix:/path.sock`, `\\.\pipe\name`), `--secret` (or `MIHOMO_SECRET`),
`--out DIR` (default: next to the script).

## Layout

```
mihomo_speedtest.py   launcher: checks Python 3.8+, calls mihomotest.cli.main()
mihomotest/
  system.py   OS flags, run() (commands without a shell), prereqs() list
  mihomo.py   Controller (API, incl. streams), find_controller, MihomoState (test mode + restore),
              list_servers, LogWatcher (mihomo's log), config_servers (address/port from config +
              provider files), DNS, connections (remote_ip, traffic_total)
  tests.py    curl (timings), mbps (transfer-only speed), jitter, ping, tcp_check, cloudflare (CDN
              ranges), test_server (warm-up, connection probes, transfers, other traffic), failure
              cause (dial_error, classify, CAUSES), run_tests (the run loop, end-of-run pings) +
              estimate/counts
  report.py   cells/notes formatting (value ±jitter), LEGEND, SPEED_METHOD, save_reports (Markdown + CSV)
  cli.py      argparse, prerequisites output, secret prompt, terminal UI, table
testserver/   remote test server tooling: setup.sh, variant.py, matrix.py (see "Remote test server");
              setup-windows.ps1 (Windows tester behind a reverse SSH tunnel, roadmap step 4),
              tunnel-host.sh (any Linux server's side of that tunnel)
ROADMAP.md    planned work and every decision taken about it
```

The engine (`mihomo`, `tests`, `report`) never prints, prompts or exits: it raises `MihomoError` /
`SecretNeeded` and reports progress through `run_tests(..., on_event)` events (`start`, `busy`, `server`,
`pinging`, `stopped`, `restored`). Interfaces (the CLI now, the GUI later) handle output. Keep it that way.

## How it works

1. `prereqs` / `check_prereqs`: Python 3.8+, curl (`curl.exe` on Windows), ping. PowerShell is optional
   (Windows process discovery only). External commands run directly via `run` (argv list, no shell).
2. `find_controller`: process command line (`-ext-ctl`, `-ext-ctl-unix`, `-ext-ctl-pipe`, `-secret`) →
   `external-controller*` / `secret` in `config_files()` → default ports/pipes. `Controller` speaks raw
   HTTP/1.0 over TCP, Unix socket or Windows named pipe (pipe I/O runs in a daemon thread so it can time
   out; opening a busy pipe is retried for up to 2 s); `Controller.stream` opens long-lived GETs such as
   `/logs`.
3. `MihomoState`: records mode, TUN, `GLOBAL` selection (and, as they're touched, every other selector
   group's selection); turns TUN off, sets mode `global`; opens a temporary `mixed-port` only if there's no
   local proxy port. `restore()` runs first in `finally` (also Ctrl+C, SIGTERM/HUP/BREAK), before anything
   that could delay it (e.g. closing the log stream, which on a Windows pipe is done in a daemon thread
   because it can block), and puts every touched group back (inner groups first, `GLOBAL` last); those
   signals are ignored while it runs so a second Ctrl+C can't cut it short. Windows: closing the console
   window, logoff or shutdown gives ~5 s and no signal, so `stop_on_console_close` (cli, ctypes
   `SetConsoleCtrlHandler`) turns it into Ctrl+C and holds the window until the `restored` event.
   stdout/stderr use `errors="replace"` so ✔/± or server names can't crash output redirected on Windows.
4. `list_servers`: servers from `/proxies` plus `/providers/proxies` (provider servers are **not** in
   `GLOBAL`'s list). Each gets a `path` of selector groups from `GLOBAL` (e.g. GLOBAL → PROXY → server),
   found by walking selectors from `GLOBAL`; `None` if no selector reaches it (reported as not selectable).
5. `test_server`, one server at a time: `state.select` walks the server's path (`PUT /proxies/<group>`) →
   warm-up probes (≤10 s; first connections are slow) → `PROBES` (5) connection probes if the warm-up got
   an answer → download via curl through mihomo → upload (one retry if warm-up worked). Timed-out transfers
   (curl rc 28) keep their speed with a "slow: … hit the time limit" note.
   - **Conn ms**: each probe is a new curl run to `generate_204`; its `time_starttransfer` is one sample.
     Median → `conn`, `jitter()` (mean absolute change between consecutive samples) → `conn_jitter`;
     failed probes → "N of 5 connections failed".
   - **Speed** is transfer-only (`mbps`): download = bytes ÷ (total − starttransfer), upload = bytes ÷
     (total − pretransfer). A transfer phase under `MIN_TRANSFER` (0.2 s) can't be timed reliably (10 MB at
     2 Gbps is ~40 ms): the speed is kept but the Note says "… too quick to time reliably: use a larger
     --size". No automatic fallback or size change — the user decides with `--size` (user's decision,
     2026-10-16). Reports state `SPEED_METHOD` ("transfer-only") so later comparisons don't mix methods.
   - **Other traffic**: `traffic_total` (`/connections` → `downloadTotal + uploadTotal`) before the download
     and after the upload; minus curl's own bytes = `other`. Note "busy: other apps used ~N MB" when it is
     ≥ `BUSY_MIN` (1 MB) and ≥ `BUSY_SHARE` (5 %) of the test's own data. Before the first server a 2 s
     sample (`other_traffic`) emits `busy` if over `BUSY_RATE` (1 MB/s). Apps that bypass mihomo are
     invisible; isolating the test from this traffic is the rejected isolated-core approach.
   - **Slow start**: time to the first warm-up response is recorded; over `SLOW_START` (3 s) → "slow start:
     first response after N s"; no warm-up response but the transfers worked → "slow start: no response in
     the first 10 s".
   - **Failure cause**: `LogWatcher` streams `/logs?level=warning` in the background for the whole run
     (mihomo streams warnings even at client log level `silent`). A server that isn't ok gets the most
     common `[TCP|UDP] dial … error:` message from its test window (`dial_error`) → raw text in
     `r["error"]` (reports only) and a plain meaning from `CAUSES` (`classify`) in the Note.
6. After **all** transfers (so the test's own traffic doesn't inflate ping or jitter), ping every server
   together (`PING_COUNT` 10, `PING_WORKERS` 50) at the address mihomo **actually dialed** (`remote_ip`:
   `/connections` → `metadata.remoteDestination`, polled during the download; some protocols, e.g.
   hysteria2, report a host name, which is resolved through mihomo's DNS). `ping` returns average, loss and
   `ping_jitter`. Servers that never connect fall back to the config's `server` host resolved
   through mihomo's DNS (`config_servers` + `mihomo_resolve` via `/dns/query`, A then AAAA; `[…]` around IPv6
   literals is stripped). macOS: `ping -W` is in ms, IPv6 uses `ping6`.
   - **Direct TCP check** (failed servers only): plain TCP connect to the dialed/resolved IP and the
     config's port, outside mihomo (TUN is off) → `open` / `refused` / `no answer` / `no route`; UDP
     protocols (`UDP_TYPES`) → "not possible (UDP)". Address and port come from `config_servers`: the
     config's `proxies` plus its proxy-provider files (`path`, relative to the config's directory).
   - **CDN label**: if the pinged IP is in Cloudflare's published ranges (`CLOUDFLARE`, 22 prefixes, built
     in) → "via Cloudflare CDN: ping shows the CDN edge, not the server". Other CDNs only by network owner
     once `--trace` does owner lookups (roadmap step 6).
   - Pings and checks run in a thread pool; queued ones are cancelled on Ctrl+C.
   - Process list, config-file list and parsed YAML are cached (`lru_cache`), so each is read once per run.
7. Results: `ok`, `partial`, `unreachable` (no proxy connection but answers ping), `failed` (neither).
   Labels stay these four for now (clearer labels are roadmap step 8); the cause and the direct check go in
   the Note (e.g. "connection timed out; direct check: port 43000 no answer"). `UNREACHABLE_NOTE` /
   `FAILED_NOTE` are shown when relevant (terminal + Markdown): unreachable can also mean the server refused
   the connection (expired/wrong credentials), and failed may just mean ICMP is blocked.
8. `save_reports`: `mihomo-speedtest-YYYYMMDD-HHMMSS.{md,csv}` (CSV has a UTF-8 BOM for Excel). The Markdown
   adds the speed method, a `LEGEND` and a "Failure details" section with mihomo's raw errors. Table cells
   show jitter as "182 ±12" (Conn ms, Ping ms); the CSV has separate `conn_ms`, `conn_jitter_ms`,
   `ping_ms`, `ping_jitter_ms` columns plus `first_response_s`, `direct_check`, `other_traffic_mb`,
   `mihomo_error`, `speed_method`. Falls back to cwd, then the temp dir, if `--out` isn't
   writable. Reports are git-ignored.

Estimate ≈ `servers × (6 + 1.4 × size)` s.
Test endpoints: `http://www.gstatic.com/generate_204` (warm-up, connection probes), `https://speed.cloudflare.com/__down` / `__up`.

## Remote test server

A Fedora VPS running a plain mihomo core as a systemd service (`mihomo.service`, config `/etc/mihomo/`,
controller `127.0.0.1:9090` with the secret in the config, mixed-port 7890, everything on localhost only).
Set up (or re-set up with a new subscription) by `testserver/setup.sh`; see its header for usage and
options (`TUN=1` enables TUN at rest, with the SSH client's IP excluded from TUN routing; `TUNNEL_KEY=…`
also prepares it for a tester PC's tunnel via `tunnel-host.sh`, copied next to it).

- Access: `ssh mihomo-testserver` (alias in `~/.ssh/config`, dedicated key `~/.ssh/mihomo-testserver`,
  host key pinned). Fedora 44, x86_64, ~450 MB RAM, IPv4 + IPv6 (IPv6 configured by hand with nmcli —
  DigitalOcean doesn't apply it to existing droplets). Keep the IP out of the repo.
- **Never commit the subscription URL** or anything from `/etc/mihomo/`; it contains credentials. Don't
  print provider/config files in tool output either.
- `/etc/mihomo/config.default.yaml` is the default setup (subscription profile with the IPv6 servers'
  `[…]` brackets removed by hand — a provider-side bug, fix requested). `/etc/mihomo/config.yaml.bak` is the
  original bracketed profile, handy for reproducing a config error. Re-running `setup.sh` re-downloads the
  subscription and brings the brackets back until the provider fixes it.
- To test: copy `mihomo_speedtest.py` and `mihomotest/` to `/root/mst/` with `scp -r` and run it there, as root.
- **Config variations**: `testserver/variant.py` switches mihomo into a variant built from
  `config.default.yaml` (controller tcp/unix, secret yes/no, port mixed/socks/http/none, tun off/on, source
  profile/provider); `testserver/matrix.py` runs the speed test on each variant one change at a time and
  checks found / tested / restored (every selector group) / exit code, then returns to the default. Copy
  both to `/root/testserver/`. **Run the matrix before every release and after changes to discovery,
  selection, state or restore.** It toggles TUN, so start it detached and poll the log for
  "variants passed" (don't `pgrep` for it — the check matches itself):
  `ssh mihomo-testserver 'SSH_IP=${SSH_CLIENT%% *} setsid nohup python3 /root/testserver/matrix.py
  /root/mst/mihomo_speedtest.py > /root/matrix.log 2>&1 < /dev/null &'`
- Simulating failures: a temporary nftables table on the output hook (`drop` = blocked port,
  `reject with tcp reset` = refused; a local UDP drop shows as "operation not permitted"), and
  `tc … netem delay` on a `prio bands 4` band filtered by destination IP for slow starters. Always delete
  them afterwards (`nft delete table inet <name>`, `tc qdisc del dev eth0 root`).
- **Windows tester** (roadmap step 4): `testserver/setup-windows.ps1` sets up a fresh Windows 11 PC (OpenSSH
  server on 127.0.0.1, Python, VPNs off) and keeps a reverse tunnel to a server; reach it from that server
  with `ssh -p PORT WINUSER@127.0.0.1`. The tunnel (SYSTEM scheduled task, `ssh -R` with 15 s keepalives)
  reconnects every 15 s, indefinitely by default or N attempts in a row (asked at setup; a connection
  that lasted a minute resets the count). Usage: the script's header and output. Keep it ASCII-only (Windows
  PowerShell 5.1 reads BOM-less files as ANSI). Not yet run on Windows.
  - Server side: `testserver/tunnel-host.sh 'PC KEY' [PORT] [USER]` on any Linux server with OpenSSH (no
    mihomo needed; one port per PC, default 2222; a new key for a port replaces the old one). User
    `tunnel`: no shell, no password; key limited to `permitlisten="127.0.0.1:PORT"`; an sshd `Match User`
    drop-in per user (`/etc/ssh/sshd_config.d/41-mihomo-tunnel-USER.conf`; keepalives in
    `40-mihomo-tunnel.conf`) allows it remote forwards only — the key
    option `port-forwarding` alone also allowed `-L` into the server (e.g. mihomo's unauthenticated proxy
    port). Verified on the test server: own port works; other ports, 0.0.0.0, `-L` and shell refused; root's
    sshd settings unchanged. The test server has the user (no keys yet). VM port: 2222.
  - PC side, checked by review (2026-10-16; parsed with PowerShell 7, tunnel `ssh` options run against
    the test server, not yet run on Windows): the tunnel key's ACL is reset to SYSTEM + Administrators
    (ssh.exe as SYSTEM refuses a key the setup user can also read); `known_hosts` is cleared on each run
    so a rebuilt server is trusted again; PyYAML is installed. For unattended use the PC also needs
    Windows auto sign-in and the client set to start at login (the tunnel task starts at boot, the
    client only at logon). Turning TUN off in the client before setup matters: the script disables
    adapters named Mihomo/Meta, which could get in the way when the client turns TUN on again.
  - Claude's login key for the VM: `~/.ssh/mihomo-winvm` (to add as `mihomo-winvm` in `~/.ssh/config`,
    `ProxyJump mihomo-testserver`, once the tunnel is up).
- Use it to check that the script works (discovery, state restore, labels, reports), not to judge servers:
  per-server results depend on the network the test runs from.

## Open items

Planned features and decisions live in `ROADMAP.md` — keep it updated; don't start roadmap items until the
user says so.

- **Windows** paths (`curl.exe`, named-pipe API, localised `ping` parsing — now also the per-reply times
  for jitter — `Get-CimInstance` discovery, the console-close handler, busy-pipe retry) have never run.
  A Windows 11 VM is available for this (roadmap step 5); still to check there: Schannel certificate
  revocation checks in `curl.exe` (they bypass the proxy and may fail, e.g. from China). **macOS** paths have never run and no Mac is available
  (GitHub Actions runners are the plan; see ROADMAP).
- Step 3 thresholds (`BUSY_SHARE`, `BUSY_MIN`, `BUSY_RATE`, `PROBES`, `PING_COUNT`, `MIN_TRANSFER`) are
  first choices; tune them after real runs on desktop clients.
- Only the test server has run the code: full runs of all 45 servers on 2026-10-16 before and after step 3
  (42 ok both times, restore verified; ~5.5 → ~8 min with the connection probes), and the matrix. No run on a real desktop client since the package
  split.
