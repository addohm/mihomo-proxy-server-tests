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
`--out DIR` (default `report.reports_dir()`: `Documents/mihomo-speedtest`, on Windows the real Documents
folder from the registry's User Shell Folders, else `~/mihomo-speedtest`), `--save-baseline`, `--compare
[REPORT.csv]` (see 8), `--trace` (off by default; see 6b), `--gui` (see 9) with `--no-browser`.

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
  trace.py    --trace: trace / parse_hops (tracert, tracepath, traceroute, mtr output), owners (Team
              Cymru via mihomo's DNS, whois fallback), summary / hop_list, CDN_ASNS
  report.py   cells/notes formatting (value ±jitter), LEGEND, SPEED_METHOD, save_reports (Markdown + CSV),
              reports_dir, load_report / compare / comparison_lines (--compare)
  cli.py      argparse, prerequisites output, secret prompt, terminal UI, table; --gui hands over to gui.py
  gui.py      --gui: App (state, runs the engine in a thread), Handler (stdlib HTTP API), serve()
  gui.html    the page (one self-contained file: no external resources; HTML/CSS/JS, polls the API)
testserver/   remote test server tooling: setup.sh, variant.py, matrix.py (see "Remote test server");
              tester-windows.cmd (revtunnel preset for a Windows tester PC, roadmap step 4);
              win_interrupt.py (Windows PC: Ctrl+C / close-window restore test, no desktop needed);
              win_matrix.py (Windows PC: config variants loaded through the API, like matrix.py);
              ci_smoke.py (any OS: packaged program vs. a stand-in mihomo; run by GitHub Actions);
              README.md (workflow and usage of all of these)
packaging/    build.py (PyInstaller build for the current OS -> dist/release/), requirements.txt (pinned)
.github/workflows/build.yml   build + smoke test on 4 runners; release on a v* tag (see 10)
ROADMAP.md    planned work and every decision taken about it
```

The engine (`mihomo`, `tests`, `report`) never prints, prompts or exits: it raises `MihomoError` /
`SecretNeeded` and reports progress through `run_tests(..., on_event)` events (`start`, `busy`, `server`,
`result`, `pinging`, `tracing`, `stopped`, `restored`); `should_stop()` (checked before each server) lets an
interface stop a run. `report.finish` does the end of a run for both interfaces (comparison, reports,
baseline). Interfaces (the CLI and the GUI) handle output. Keep it that way.

## How it works

1. `prereqs` / `check_prereqs`: Python 3.8+, curl (`curl.exe` on Windows), ping. PowerShell is optional
   (Windows process discovery only). External commands run directly via `run` (argv list, no shell).
2. `find_controller`: process command line (`-ext-ctl`, `-ext-ctl-unix`, `-ext-ctl-pipe`, `-secret`) →
   `external-controller*` / `secret` in `config_files()` → Windows: `mihomo_pipes()`, every named pipe with
   "mihomo" in its name (not `*-service`) → default ports/pipes. The pipe list matters: a **normal (not
   elevated) user can't read the command line of a core run as a service** (Clash Verge Rev's service mode
   runs `verge-mihomo.exe` as SYSTEM), so its `-ext-ctl-pipe` with the per-user id is invisible, but the
   pipe list isn't and the pipe opens fine (found 2026-10-17 by the user; my SSH tests ran elevated). `Controller` speaks raw
   HTTP/1.0 over TCP, Unix socket or Windows named pipe (pipe I/O runs in a daemon thread so it can time
   out; opening a busy pipe is retried for up to 2 s); `Controller.stream` opens long-lived GETs such as
   `/logs`.
3. `MihomoState`: records mode, TUN, `GLOBAL` selection (and, as they're touched, every other selector
   group's selection); turns TUN off, sets mode `global`; opens a temporary `mixed-port` only if there's no
   local proxy port. Right after `apply()`, `proxy_answers` (tests.py) sends one proxy handshake to that
   port: a paused core (FlClash when not started) accepts and drops it at once, and every server would look
   unusable, so the run stops with a `MihomoError` ("isn't passing connections … is your client
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
     both the Linux/macOS and the Windows (`connectex` / `wsarecv`) wordings. mihomo's Windows errors are
     **English on every Windows language** (Go asks Windows for English error texts): checked 2026-10-21 on
     a Chinese Windows, with mihomo both as a service (Verge) and in the user's session (plain mihomo).
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
     in) → "via Cloudflare CDN: ping shows the CDN edge, not the server". With `--trace`, other CDNs by network
     owner (`trace.CDN_ASNS`: Fastly, Akamai, CDN77, Gcore, Bunny, Edgio; AWS/CloudFront isn't, its AS is
     all of AWS).
   - Pings and checks run in a thread pool; queued ones are cancelled on Ctrl+C.
   - Process list, config-file list and parsed YAML are cached (`lru_cache`), so each is read once per run.
6b. **`--trace`** (`run_traces`, still in test mode with TUN off, after the pings): servers that didn't work
   and don't answer ping get a path trace (`tracert -d` / `tracepath -n` / macOS `traceroute -n`, ≤ 20 hops),
   those that do answer ping a TCP trace to the proxy port (Linux as root with `traceroute -T` or `mtr --tcp`; otherwise the Note says
   "run with sudo", on Windows "not available"; UDP protocols: not possible); failed servers whose direct
   check says "no route" aren't traced. `parse_hops` reads every tool's output in any language (a line
   starting with the hop number, then the first IP). Owners: Team Cymru TXT records (`origin.asn` /
   `AS<n>.asn`) through mihomo's `/dns/query` (works from China), else `whois.cymru.com:43` bulk; private
   / CGNAT hops are labelled, not looked up. Note: one-line `summary` ("stops after hop 11: IP (AS3257
   GTT-BACKBONE)", "reaches the server (13 hops)", "no answer from the server within 20 hops; last hop: …");
   Markdown "Traces" section: the whole `hop_list` (runs of `*` collapsed); CSV `trace` column. Traces run
   in parallel in the ping pool: about 1 min extra. Hop addresses go to Team Cymru (documented).
7. Results (roadmap step 8): during the run a server that isn't ok/partial is "failed", or "unreachable" once
   it's found to answer ping (internal only; `run_traces` uses `ping`, not these). After the run (also after
   Ctrl+C) `result_label` gives the final label from the cause (`classify`), the direct check and the ping,
   first match wins: `no selector` (no path), `bad config`, `DNS failed`, `firewall`, `no route` (check or
   cause), `refused` (check or cause), `rejected` (port open, or credentials/TLS cause), `blocked` (port no
   answer, address pings), `no answer` (port no answer, or no ping at all with 100 % loss), else `failed`.
   `tests.RESULTS` holds the labels in order with one sentence each; `report.notes` shows the sentence for
   each label that appears (terminal "What the results mean:", Markdown quotes); `report.summary` is "N ok,
   N blocked, … of N servers" (ok always first; `matrix.py` parses `N ok … of N servers`). Colours: ok
   green; partial / blocked / refused / rejected yellow (the server is there); the rest red. `--compare`
   still counts only ok/partial as working, so older reports (unreachable/failed) compare fine.
8. `save_reports`: `mihomo-speedtest-YYYYMMDD-HHMMSS.{md,csv}` (CSV has a UTF-8 BOM for Excel). The Markdown
   adds the speed method, a `LEGEND` and a "Failure details" section with mihomo's raw errors. Table cells
   show jitter as "182 ±12" (Conn ms, Ping ms); the CSV has separate `conn_ms`, `conn_jitter_ms`,
   `ping_ms`, `ping_jitter_ms` columns plus `first_response_s`, `direct_check`, `other_traffic_mb`,
   `mihomo_error`, `speed_method`, `trace`, `date`. The folder is created if missing; falls back to cwd,
   then the temp dir, if it isn't writable. Reports are git-ignored.
   - **Baseline / compare** (roadmap step 7; explicit only, never automatic): `--save-baseline` copies the
     run's CSV to `baseline.csv` next to it; `--compare` loads `baseline.csv` from `--out` (or the given
     CSV) **before** testing (missing → stop with a hint) and after the run `compare()` matches servers by
     name: stopped working / working again (`ok`/`partial` ↔ the others), swings over `SWING` (30 %) and
     `SWING_MIN` (2 Mbps, 20 ms) in download/upload/conn/ping, "not in the saved report", and "gone from
     the config" (only servers mihomo no longer has, so `--filter` doesn't fake it). Speeds are compared
     only when every row of the saved report has the current `speed_method`. The date comes from the CSV's
     `date` column, else its file name, else its mtime. Printed after the table and saved as "Changes since
     the saved report" in the Markdown. Without a baseline, one "Tip: run with --save-baseline …" line.

9. **GUI** (`--gui`, roadmap step 9, first version 2026-10-17): `gui.serve` starts a `ThreadingHTTPServer`
   on 127.0.0.1, random port, prints the address and opens it with `webbrowser` (not with `--no-browser`).
   Every request needs the launch's random token (`?t=` for the page, `X-Token` header for the API, so a
   form on another site can't post) and a Host of 127.0.0.1/localhost (DNS rebinding); report names are
   basenames of `mihomo-speedtest-*.md` only. API: `GET /api/state` (connection, servers, run: phase, live
   results, summary, meanings, comparison, report paths), `/api/reports`, `/api/report?name=`; `POST
   /api/connect` (address / secret: the secret prompt as a page field), `/api/run` (servers, size, timeout,
   trace, save_baseline, compare), `/api/stop`, `/api/quit`. The run goes in a worker thread (`App._run`),
   one at a time. **Stop** sets `should_stop` and calls `state.restore()` at once (it's idempotent; the
   current server's test finishes through the restored selection, then the loop stops). Quit, Ctrl+C or
   closing the console window do the same, then wait for the worker. `restore()` from a non-main thread
   skips its signal guard (ValueError is caught). The page polls `/api/state` (1 s running, 4 s idle);
   table sortable, a click on a row shows Note, direct check, mihomo's error, first response and trace;
   report history opens the Markdown. Checked 2026-10-17: Linux test server (token / Host / path checks,
   run, busy refusal, baseline, history, quit, restore) and the Windows VM from China with Clash Verge Rev
   (run with trace, Stop after 7 s, restore, Quit; screenshots with headless Edge:
   `msedge --headless=new --screenshot=… --window-size=1400,1050 --virtual-time-budget=4000 URL`).

10. **Packaged program** (roadmap step 10, 2026-10-19): `packaging/build.py` runs PyInstaller (versions pinned
   in `packaging/requirements.txt`, PyYAML bundled; `gui.html` added as data, found next to `gui.py` as
   before). Windows: one console `.exe`; Linux: one file, built on Ubuntu 22.04 (glibc 2.35+); macOS: a
   `.app` (onedir, `--windowed`), `LSUIElement` set (no Dock icon; the page is the UI), re-signed ad-hoc,
   zipped with `ditto`. Names `mihomo-speedtest-VERSION-{windows-x64.exe,linux-x64,macos-arm64.zip,
   macos-x64.zip}`; version from `mihomotest.__version__` (`--version`). `cli.main`: when `sys.frozen` and no
   arguments (macOS `-psn_*` ignored) it runs as `--gui` with `args.idle_quit`: the server quits once no
   request came for `gui.IDLE_QUIT` (300 s) and no test runs (no console to close on macOS); on Windows an
   error exit in that mode waits for Enter so the window can be read. Any argument means the terminal;
   `--cli` exists for that. A frozen app without a console gets stdout/stderr on devnull. `/api/quit`
   replies before setting quit (it raced the shutdown: the page got a broken connection now and then).
   **CI** (`.github/workflows/build.yml`): ubuntu-22.04, windows-2022, macos-15 (arm64), macos-15-intel;
   each builds and runs `testserver/ci_smoke.py` on the result: two downloaded mihomo cores (the "server"
   renamed `relay-core` so discovery ignores it; the client with `local` = works and `dead` = closed port,
   PROXY left on `dead`), controller per OS (Linux Unix socket, macOS TCP + secret from the YAML, Windows
   named pipe), then a `--cli` run (exit 0, local ok, dead not, reports saved), restore check, and the
   double-click path (`BROWSER` = a no-op program; page and token served, API finds the core, Quit ends it).
   12 checks; on Linux also run by hand on `sgp-vps` with its mihomo service stopped (`systemctl stop
   mihomo`, start it again after). **Releases**: push a tag `vX.Y.Z` equal to `__version__`; the release job
   (the only one with `contents: write`) checks that, writes `SHA256SUMS.txt` and runs `gh release create`.
   Actions are pinned to commit SHAs (update them deliberately). The repo must be public for public releases.

Estimate ≈ `servers × (6 + 1.4 × size)` s.
Test endpoints: `http://www.gstatic.com/generate_204` (warm-up, connection probes), `https://speed.cloudflare.com/__down` / `__up`.

## Remote test server

An Ubuntu 24.04 VPS in Singapore (`sgp-vps`, set up 2026-10-17; the earlier Fedora server was deleted, and
the `mihomo-testserver` alias with it) running a plain mihomo core as a systemd service (`mihomo.service`,
config `/etc/mihomo/`, controller `127.0.0.1:9090` with the secret in the config, mixed-port 7890,
everything on localhost only). It is also the revtunnel relay for the Windows tester VM (tunnel `main`,
port 2222). Set up (or re-set up with a new subscription) by `testserver/setup.sh` (Fedora or
Debian/Ubuntu); see its header for usage and options (`TUN=1` enables TUN at rest, with the SSH client's
IP excluded from TUN routing; `TUNNEL_KEY=…` also lets a tester PC's tunnel in via revtunnel's
`revtunnel-relay.sh`, copied next to it).

- Access: the workspace's `/claude ssh` host, `/srv/claude/data/ssh/mihomo-proxy-server-tests/ssh sgp-vps
  '...'` (root; also `scp -S <that ssh>`). x86_64, ~460 MB RAM, IPv4 + IPv6 (the IPv6 test servers work from
  here; from the VM in China, which has no IPv6, they get `no route`). Keep the IP out of the repo.
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
  "variants passed" (don't `pgrep` for it — the check matches itself). The `tun=on` variant must exclude
  every SSH peer from TUN routing, including the Windows VM's tunnel (its address in China changes), so
  collect them at start: `IPS=$(ss -tn state established "( sport = :22 )" | awk 'NR>1{print $4}' | sed -E
  's/^\[?([^]]*)\]?:[0-9]+$/\1/' | sort -u | paste -sd,); SSH_IP="$IPS" setsid nohup python3
  /root/testserver/matrix.py /root/mst/mihomo_speedtest.py > /root/matrix.log 2>&1 < /dev/null &` (run on
  the VPS). 2026-10-17 on `sgp-vps`: 8 of 8 passed, the VM's tunnel stayed up.
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
  - The VM (in mainland China, Windows 11, user `addohm`) is reachable as `ssh mihomo-winvm` through its
    revtunnel tunnel on `sgp-vps`; see "Windows tester VM" below. **TUN stays off at rest on the tester PC** (user, 2026-10-16), so the
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
  - **Also test as a normal user**: `ssh mihomo-winvm` runs elevated and sees more than a user's own
    PowerShell window (e.g. a service's command line). Run a check in the user's desktop session with a
    normal token through a scheduled task: `New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType
    Interactive -RunLevel Limited`, run once, wait while its State is Running, unregister; have the script
    write its results to a file. (Don't name a PowerShell script parameter `$Args`: it's an automatic
    variable, and the value silently disappears.)
  - Anything that may cut the tunnel (TUN on/off, long runs): start it detached with `Invoke-CimMethod
    Win32_Process -MethodName Create` (processes started normally die with the SSH session) and have it
    write a log with `Out-File -Encoding utf8`: Windows PowerShell's `>>` / `*>>` write UTF-16, which a
    plain `grep` on the fetched text never matches. **Fetch logs with `scp`**, not `Get-Content` over SSH:
    a whole log with the results table (box-drawing characters) silently comes back empty that way, so a
    waiter never sees its end marker (missed a finished full run for 40 min on 2026-10-21). Check the
    waiting condition on a finished log before relying on it, and report as soon as the run ends.
- Use it to check that the script works (discovery, state restore, labels, reports), not to judge servers:
  per-server results depend on the network the test runs from.

### Windows tester VM: what's on it (2026-10-17, snapshot taken by the user after this)

`DESKTOP-BJUBIRD` (mainland China), reached as `ssh mihomo-winvm`. **Since 2026-10-21 in Chinese** (zh-CN
display language, format and system locale; the user asked for it; English is still installed). The VM's host key isn't kept in the
workspace's `/claude ssh` known_hosts (that file is rewritten): pass `-o UserKnownHostsFile=~/.ssh/
known_hosts_revtunnel_vm -o StrictHostKeyChecking=yes` (key recorded there). GitHub and winget's sources
aren't reachable from the VM without a proxy: download installers on `sgp-vps`, check them against the
winget manifest's SHA-256 (`microsoft/winget-pkgs`), copy them in with scp.

- Python 3.13.16 + PyYAML (`py -V:3.13`); test code in `C:\Users\addohm\mst` (copy the current code over
  before testing).
- Windows signs in automatically (Sysinternals Autologon) and **Clash Verge Rev starts at login**
  (minimized, TUN off); checked after a reboot on 2026-10-17: tunnel back, Verge found, short run ok.
- Clients, all with the same subscription (`trixu`), TUN off, only Verge starting at login; **run one at
  a time** (they share port 7890), and close the others first:
  - **Clash Verge Rev** (the normal one, starts at login): service `clash_verge_service` (Auto) + GUI;
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

- **2026-10-21, user's decisions:** the repo **stays private** for now: no release, and **don't push** unless
  the user asks (a push runs the 4 CI builds; macOS minutes count 10x on private repos). **macOS isn't a
  priority**: keep its code and CI build, but no extra macOS work or testing.
- **Resumed 2026-10-19** (the user, after testing on various platforms: "it tested well"): step 10 built
  (see "How it works" 10); first release pending (see ROADMAP: repo public, CI green on all four systems).
  **Tested 2026-10-21 without CI** (the step 10 commit isn't pushed: the user hasn't yet made the repo
  public): Linux build on `sgp-vps` (PyInstaller in `/root/mstvenv`, build in `/root/mstbuild`): ci_smoke
  12/12 (mihomo service stopped and started again), the binary against the real mihomo with `--trace`
  (mtr, owners), Linux matrix 8/8. Windows build on the VM (venv `C:\Users\addohm\mstvenv`, build in
  `C:\Users\addohm\mstbuild`, 8.8 MB): ci_smoke 12/12 with `MIHOMO_EXE=C:\mihomo\mihomo.exe` (Verge stopped,
  then restarted); as a **normal user** in the desktop session: `--cli` and the double-click page with Clash
  Verge Rev (found through the pipe list; run with trace, restore, screenshot, Quit ends it within 1 s); the
  double-click page with plain mihomo and Clash Nyanpasu (TCP + secret from their config; runs ok) and
  FlClash (the page asked for the secret, connected after it was entered, then showed "is your client
  started?" because FlClash comes back paused after a restart); full run of all 45 servers from China with
  the .exe: 16m55s, exit 0, restored, 27 ok / 14 blocked / 1 rejected / 1 no answer / 1 no route / 1 bad
  config. **Chinese Windows** (2026-10-21, the user's OK): VM
  switched to zh-CN (language pack via `Install-Language zh-CN`, ~45 min; display language, format, home
  location China, system locale zh-CN → code page 936; reboot): `ping` / `tracert` print Chinese and are read
  correctly (Python decodes cp936), the .exe as a normal user with Clash Verge Rev and with plain mihomo:
  every label right, traces with owners, restored; mihomo's errors stay English (see "Failure cause").
  Capturing the frozen .exe's output from a pipe: it writes the system encoding (cp936) and ignores
  `PYTHONIOENCODING`, so `…`/`±` look garbled if read as UTF-8; a real console shows them fine. Still not
  covered: the GUI on a Linux desktop; macOS beyond the CI smoke test (not a priority). Double-click test harness: run a `.cmd` that
  sets `BROWSER=<python> <script that saves the URL> %s` with **forward slashes** (Python's webbrowser splits
  BROWSER like a Unix shell and eats backslashes), start it with the Limited-token scheduled task, drive
  `/api/*` with the saved URL.
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
  Ctrl+C, close and win_matrix all pass. Clash Party re-tested on the new VM: all pass, matrix 6/6. A
  Chinese Windows was tested on 2026-10-21 (see above). **macOS**: only the CI smoke test (no Mac; not a
  priority for the user).
- Step 3 thresholds (`BUSY_SHARE`, `BUSY_MIN`, `BUSY_RATE`, `PROBES`, `PING_COUNT`, `MIN_TRANSFER`) are
  first choices; tune them after real runs on desktop clients.
- Runs so far: the test server (full runs of all 45 servers on 2026-10-16 before and after step 3, 42 ok
  both times, restore verified, ~5.5 → ~8 min with the connection probes; the matrix, last 8 of 8 on
  2026-10-17 after the GUI changes), and the Windows VM with Clash Verge Rev from mainland China: full run
  of all 43 servers on 2026-10-16 (16.5 min, 25 ok, 15 unreachable, 3 failed — before step 8's labels, so
  today mostly `blocked`; restored; the trojan servers on port 43000 answer ping but their port gets no
  answer, through mihomo and directly), no "busy" flags, and the short runs above (incl. `--trace`,
  `--compare`, the new labels, the GUI elevated and the CLI as a normal user).
