# CLAUDE.md

`mihomo_speedtest.py` tests every server in a **running** mihomo, exactly as mihomo uses it, and writes a
Markdown + CSV report. The launcher only starts `mihomotest/cli.py`; the code lives in the `mihomotest/`
package. `context.md` is the original handoff (2026-10-09), kept as a frozen snapshot for background; this
file is the living summary. Keep this file, README, ROADMAP and the CLI docstring (`mihomotest/cli.py`, also
the `--help` text) in sync when behaviour or options change, in the same commit. At every milestone (e.g. a
roadmap step done) also read all of them end to end and fix anything stale (user's rule, 2026-10-16).

## Hard rules

- **Never run or test the script locally, or on any machine the user actually uses, unless the user
  explicitly says to.** It turns TUN off and switches mode/selection on a live client.
- Test it **yourself, on the test machines over SSH**: the Linux test server and the Windows tester PC
  (through the tunnel). Don't ask the user to run things there (user, 2026-10-16). See "Remote test
  server" below.
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
              tester-windows.cmd (revtunnel preset for a Windows tester PC, roadmap step 4);
              win_interrupt.py (Windows PC: Ctrl+C / close-window restore test, no desktop needed);
              win_matrix.py (Windows PC: config variants loaded through the API, like matrix.py);
              README.md (workflow and usage of all of these)
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
   local proxy port. Right after `apply()`, `proxy_answers` (tests.py) sends one proxy handshake to that
   port: a paused core (FlClash when not started) accepts and drops it at once, and every server would look
   unreachable, so the run stops with a `MihomoError` ("isn't passing connections … is your client
   started?"); a timeout counts as answering. `restore()` runs first in `finally` (also Ctrl+C, SIGTERM/HUP/BREAK), before anything
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
     `r["error"]` (reports only) and a plain meaning from `CAUSES` (`classify`) in the Note. `CAUSES` has
     both the Linux/macOS and the English Windows (`connectex` / `wsarecv`) wordings; on a non-English
     Windows these messages are localised and fall through to "mihomo could not connect through this
     server" (the direct check still tells what happened).
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

> **2026-10-16: the Fedora test server below is deleted** (user); don't connect to its old address (the
> `mihomo-testserver` alias is removed). Now: an Ubuntu 24.04 VPS in Singapore, `/claude ssh` host
> `sgp-vps` (`/srv/claude/data/ssh/mihomo-proxy-server-tests/ssh sgp-vps '...'`), **without mihomo yet**,
> holding the revtunnel tunnel (`main`, port 2222) of a **freshly installed** Windows 11 VM in mainland
> China (`DESKTOP-BJUBIRD`, user `addohm`, Python 3.13.16; no mihomo client yet). `ssh mihomo-winvm` now
> reaches that VM (ProxyCommand through `sgp-vps`, the workspace's `/claude ssh` key, which is the VM's
> only login key). The notes below describe the old setup until the new one is decided.

A Fedora VPS running a plain mihomo core as a systemd service (`mihomo.service`, config `/etc/mihomo/`,
controller `127.0.0.1:9090` with the secret in the config, mixed-port 7890, everything on localhost only).
Set up (or re-set up with a new subscription) by `testserver/setup.sh`; see its header for usage and
options (`TUN=1` enables TUN at rest, with the SSH client's IP excluded from TUN routing; `TUNNEL_KEY=…`
also lets a tester PC's tunnel in via revtunnel's `revtunnel-relay.sh`, copied next to it).

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
- **Windows tester** (roadmap step 4): the reverse-tunnel tooling is its own project, **revtunnel**
  (`addohm/revtunnel`, workspace `/srv/claude/workspaces/revtunnel`; generic: Windows + Linux PCs, any
  port, named tunnels, guided steps, status + re-run menu). This repo keeps only the preset
  `testserver/tester-windows.cmd` (runs `revtunnel-target-windows.ps1` next to it with `-Name mihomo-tester
  -KeepAwake -NoVpn -Python 3.13 -PythonPackages pyyaml`) and the workflow in `testserver/README.md`.
  revtunnel calls the tester PC the **target** and the test server the **relay**. Relay side:
  `revtunnel-relay.sh KEY PORT` on the test server (user `tunnel`, sshd drop-in `41-revtunnel-tunnel.conf`; `--list` shows every PC and whether it's connected).
  - The VM (in mainland China, Windows 11, user `addohm`) is reachable: `ssh mihomo-winvm` (in
    `~/.ssh/config`: 127.0.0.1:2222, key `~/.ssh/mihomo-winvm`, `ProxyJump mihomo-testserver`). It still
    runs the **pre-revtunnel** tunnel (task "mihomo-tester tunnel", `C:\ProgramData\mihomo-tester`,
    Python 3.15, plus 3.13.16 with PyYAML 6.0.3 since 2026-10-16: run `py -V:3.13`); moving it to the preset is pending (it cuts the SSH session the other
    VM work uses, so coordinate first: set up the preset on port 2223, check a login there, then remove
    the old task and switch `mihomo-winvm` to 2223). Its mihomo client's TUN adapter (`Meta`) was
    disabled by the first setup. **TUN stays off at rest on the tester PC** (user, 2026-10-16), so the
    tunnel never runs through the client under test; turn it on only for a TUN test, and turn it off
    again after.
  - For unattended use the PC also needs Windows auto sign-in and the client set to start at login (the
    tunnel starts at boot, the client only at logon).
  - **Test it yourself over the tunnel; don't ask the user to run things on the PC** (user, 2026-10-16:
    asking defeats the purpose of the tunnel). Copy the code with `scp -r mihomo_speedtest.py mihomotest
    mihomo-winvm:C:/Users/addohm/mst/` and run `py -3 …` over `ssh mihomo-winvm` (PowerShell is the
    shell; for anything with quotes, copy a small script over instead of a one-liner). Ctrl+C and closing
    the window: `testserver/win_interrupt.py` (starts the test in its own console, interrupts it, checks
    the restore). Processes started from an SSH session inherit "ignore Ctrl+C", so a plain
    `Start-Process` never sees a Ctrl+C. Put back any selection you change for a test.
  - Anything that may cut the tunnel (TUN on/off, long runs): start it detached with `Invoke-CimMethod
    Win32_Process -MethodName Create` (processes started normally die with the SSH session) and have it
    write a log with `Out-File -Encoding utf8`: Windows PowerShell's `>>` / `*>>` write UTF-16, which a
    plain `grep` on the fetched text never matches. Check the waiting condition on a finished log before
    relying on it, and report as soon as the run ends.
- Use it to check that the script works (discovery, state restore, labels, reports), not to judge servers:
  per-server results depend on the network the test runs from.

### Windows tester VM: what's on it (2026-10-17, snapshot taken by the user after this)

`DESKTOP-BJUBIRD` (mainland China), reached as `ssh mihomo-winvm`. The VM's host key isn't kept in the
workspace's `/claude ssh` known_hosts (that file is rewritten): pass `-o UserKnownHostsFile=~/.ssh/
known_hosts_revtunnel_vm -o StrictHostKeyChecking=yes` (key recorded there). GitHub and winget's sources
aren't reachable from the VM without a proxy: download installers on `sgp-vps`, check them against the
winget manifest's SHA-256 (`microsoft/winget-pkgs`), copy them in with scp.

- Python 3.13.16 + PyYAML (`py -V:3.13`); test code in `C:\Users\addohm\mst` (copy the current code over
  before testing).
- Clients, all with the same subscription (`trixu`), TUN off, none starting at login; **run one at a
  time** (they share port 7890), and close the others first:
  - **Clash Verge Rev** (the normal one, left running): service `clash_verge_service` (Auto) + GUI;
    pipe controller. TUN setting is `enable_tun_mode` in `%APPDATA%\io.github.clash-verge-rev…\verge.yaml`.
  - **Clash Party** 2.0.3: `C:\Program Files\Clash Party\Clash Party.exe`; pipe controller.
  - **FlClash** 0.9.0: External controller on (127.0.0.1:9090, secret in its `shared_preferences.json`);
    press Start in its window, else its core is paused. `FlClashHelperService` runs at boot (harmless).
  - **Clash Nyanpasu** 1.6.1 (mihomo v1.18.8): TCP controller 127.0.0.1:17650 with secret in its `-f`
    config. Stopping its window can leave or restart its core: stop `mihomo.exe` processes whose command
    line contains "Nyanpasu" too.
  - **Plain mihomo** v1.19.32: `C:\mihomo` (`start-mihomo.cmd`, `mihomo.exe -d C:\mihomo`), TCP controller
    127.0.0.1:9097 with a secret in `C:\mihomo\config.yaml`, mixed-port 7899.
- Start GUI clients in the user's session with a scheduled task (`LogonType Interactive`, run once,
  then unregister); they won't show if started from the SSH session.
- Results 2026-10-16/17, all from China: every client found on its own (FlClash: once its secret is
  given), short run, Ctrl+C and closing the window restore everything; win_matrix passes on Verge, Clash
  Party, Nyanpasu and plain mihomo (the last two skip "secret").

## Open items

Planned features and decisions live in `ROADMAP.md` — keep it updated; don't start roadmap items until the
user says so.

- **Windows** (roadmap step 5), first runs on the VM 2026-10-16 (Clash Verge Rev in service mode, mihomo
  v1.19.32, Python 3.15, no PyYAML, en-US, from mainland China): `Get-CimInstance` discovery finds the core
  in session 0 and its per-user pipe `\\.\pipe\verge-mihomo-production-<hash>` from `-ext-ctl-pipe` (the
  built-in default pipe names would miss it), no secret needed; API, `/logs` stream (mihomo's dial errors),
  `curl.exe` (Schannel; HTTPS worked, no revocation failure), ping parsing, reports and restore (every
  group) all work; output to a file with Python's legacy encoding (`-X utf8=0`) doesn't crash. Ping
  parsing also checked offline with zh-CN / de-DE / ja-JP / IPv6 samples. Ctrl+C and closing the console
  window restore everything (`win_interrupt.py`, both PASS; a closed window ends the process ~1 s later
  with 0xC000013A, after the restore). `testserver/win_matrix.py` (config variants loaded
  through `PUT /configs` from a copy of the core's `-f` file, original loaded back at the end; the client's
  settings untouched): default, secret, socks, http, noport, provider all PASS with Python 3.13 + PyYAML,
  default and secret also with 3.15 without PyYAML. Findings: mihomo doesn't check the secret on a named
  pipe (right, none or wrong all get 200), so on Windows the secret only matters for a TCP controller; and
  mihomo opens a TCP controller only at start-up, not when a config is loaded through the API (so the
  Windows matrix can't cover it; the code is OS-independent and `matrix.py` covers it on Linux). TUN on at
  rest (Verge's `Meta` adapter up, the tunnel itself through TUN): TUN off for the test and back on
  after, everything restored, the tunnel reconnected by itself. PyYAML path with Python 3.13: a failed
  server gets its ping and direct check from the config's address (→ "unreachable"). **Clash Party** (formerly
  Mihomo Party, 2.0.3; core `mihomo.exe` as a normal process in the user's session, no `-f`, only `-d
  …\mihomo-party\work`, pipe `\\.\pipe\MihomoParty\mihomo-user-<user>-<n>`, mixed 7890 + socks 7891 +
  http 7892): found on its own, win_matrix 6/6, Ctrl+C and close PASS. With two clients running the first
  core in the process list is tested (the output names its pipe; `--api` picks another). Installing a
  client for a test: `winget` over SSH works; start GUI apps in the user's session with a scheduled task
  (`LogonType Interactive`); Clash Party ignored a `clash://install-config` link passed on its command
  line, so its profile was written directly (`profile.yaml` + `profiles\<id>.yaml`, content copied from
  Verge's). Clash Verge Rev's service is `clash_verge_service`. **FlClash** (0.9.0): core
  `FlClashCore.exe` (its pipe is FlClash's own channel, not mihomo's API); no controller until the user
  turns on "External controller" (`127.0.0.1:9090`, with a secret kept in
  `%APPDATA%\com.follow\clash\shared_preferences.json`, not YAML, so the test asks for it or takes
  `--secret` / `MIHOMO_SECRET`); until its Start button is pressed the core is paused (mixed-port 0, proxy
  connections dropped: that's what `proxy_answers` catches). Its config lives in its own database, so
  win_matrix doesn't apply; short run, Ctrl+C and close PASS. Its first-run disclaimer blocks imports until
  clicked in its window. **Clash Nyanpasu** (1.6.1, bundles mihomo **v1.18.8**): `mihomo.exe -m -d
  …\Local\Clash Nyanpasu\data -f …\Roaming\Clash Nyanpasu\config\clash-config.yaml` in the user's session,
  TCP controller `127.0.0.1:17650` with a secret, both read from that `-f` file (PyYAML); short run,
  Ctrl+C, close and win_matrix (5 PASS, secret skipped) all pass. A profile written into its
  `profiles.yaml` was ignored; it had to be added in its window and the app restarted before it could be
  activated. Finding: a TCP controller also keeps the secret it started with through a config load
  (win_matrix now skips "secret" for TCP controllers and falls back to the old secret after a load).
  **Plain mihomo** (v1.19.32 in `C:\mihomo`, TCP controller with a secret from its `-d` config): short run,
  Ctrl+C, close and win_matrix all pass. Clash Party re-tested on the new VM: all pass, matrix 6/6. Not
  covered: a non-English Windows (ping parsing only checked offline). **macOS** paths have never run and no Mac is available (GitHub Actions runners are the plan;
  see ROADMAP).
- Step 3 thresholds (`BUSY_SHARE`, `BUSY_MIN`, `BUSY_RATE`, `PROBES`, `PING_COUNT`, `MIN_TRANSFER`) are
  first choices; tune them after real runs on desktop clients.
- Runs so far: the test server (full runs of all 45 servers on 2026-10-16 before and after step 3, 42 ok
  both times, restore verified, ~5.5 → ~8 min with the connection probes; the matrix), and the Windows VM
  with Clash Verge Rev from mainland China: full run of all 43 servers on 2026-10-16 (16.5 min, 25 ok,
  15 unreachable, 3 failed, restored; the trojan servers on port 43000 answer ping but their port gets no
  answer, through mihomo and directly: blocked on that network), no "busy" flags, and the short runs above.
