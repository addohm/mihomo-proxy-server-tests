# CLAUDE.md

Single-file tool: `mihomo_speedtest.py` tests every server in a **running** mihomo, exactly as
mihomo uses it, and writes a Markdown + CSV report. `context.md` is the original handoff (2026-10-09), kept
as a frozen snapshot for background; this file is the living summary. Keep this file, README and the
script's docstring in sync when behaviour or options change.

## Hard rules

- **Never run or test the script locally, on any machine (not even in the background), unless the user
  explicitly says to.** It turns TUN off and switches mode/selection on a live client. By default, give the
  exact command; the user runs it and reports back.
- When the user does ask for a test, run it **only on the remote test server over SSH** — never locally.
  See "Remote test server" below.
- Static checks are fine: `python3 -m py_compile mihomo_speedtest.py`.
- Keep changes small and inside the design below. The user has twice asked for simplicity after over-engineering.
- **Public tool.** Stay universal: Linux, Windows, macOS and **any mihomo-based client** (Clash Verge Rev,
  Mihomo Party, Nyanpasu, FlClash, ClashX Meta, plain mihomo) and any configuration (TCP/Unix/pipe controller,
  with or without secret, mixed/socks/http port or none, TUN on or off, profiles or providers). Never tune it
  to one particular machine, client or network.
- Standard library only. PyYAML stays optional. No mihomo binary needed.

## What it must be (settled — do not redesign)

A simple, true test of what mihomo does with each server in its running config. Run → test → table →
auto-save `.md` + `.csv`. No prompts (the only one allowed: the controller secret when it can't be found).
Failed servers are simply reported.

Explicitly rejected — do not reintroduce:
- An "isolated" temporary mihomo core built from config files (had to replicate DNS / `nameserver-policy`).
- Relay / `dialer-proxy` re-tests, binding to the physical interface, auto mode selection, confirmation prompts.
- Any routing "shenanigans".

## Usage

```bash
python3 mihomo_speedtest.py                                   # everything, no prompts
python3 mihomo_speedtest.py --filter "Japan|Hong Kong"     # subset by regex
```

Options: `--filter REGEX`, `--size MB` (default 10; upload is half), `--timeout SEC` (25),
`--api ADDR` (`127.0.0.1:9090`, `unix:/path.sock`, `\\.\pipe\name`), `--secret` (or `MIHOMO_SECRET`),
`--out DIR` (default: next to the script).

## How it works (map of the script)

1. `check_prereqs`: Python 3.8+, curl (`curl.exe` on Windows), ping. PowerShell is optional (Windows process
   discovery only). External commands run directly via `run` (argv list, no shell).
2. `find_controller`: process command line (`-ext-ctl`, `-ext-ctl-unix`, `-ext-ctl-pipe`, `-secret`) →
   `external-controller*` / `secret` in `config_files()` → default ports/pipes. `Controller` speaks raw
   HTTP/1.0 over TCP, Unix socket or Windows named pipe (pipe I/O runs in a daemon thread so it can time out).
3. `MihomoState`: records mode, TUN, `GLOBAL` selection; turns TUN off, sets mode `global`; opens a temporary
   `mixed-port` only if there's no local proxy port. `restore()` runs in `finally` (also Ctrl+C,
   SIGTERM/HUP/BREAK); those signals are ignored while it runs so a second Ctrl+C can't cut it short.
4. `list_servers`: `/proxies` entries that aren't groups (`GROUP_TYPES`), in `GLOBAL` order.
5. `test_server`, one server at a time: `PUT /proxies/GLOBAL` → warm-up probes (≤10 s; first connections are
   slow) → download via curl through mihomo → upload (one retry if warm-up worked). Timed-out transfers
   (curl rc 28) keep their speed with a "slow: … hit the time limit" note.
6. Ping the IP mihomo **actually dialed** (`remote_ip`: `/connections` → `metadata.remoteDestination`, polled
   during the download). Servers that never connect fall back to the config's `server` host resolved through
   mihomo's DNS (`config_hosts` + `mihomo_resolve` via `/dns/query`). Pings run in a thread pool.
7. Results: `ok`, `partial`, `unreachable` (no proxy connection but answers ping), `failed` (neither).
   `UNREACHABLE_NOTE` / `FAILED_NOTE` are shown when relevant (terminal + Markdown): unreachable can also mean
   the server refused the connection (expired/wrong credentials), and failed may just mean ICMP is blocked.
8. `save_reports`: `mihomo-speedtest-YYYYMMDD-HHMMSS.{md,csv}` (CSV has a UTF-8 BOM for Excel). Falls back
   to cwd, then the temp dir, if `--out` isn't writable. Reports are git-ignored.

Estimate ≈ `servers × (6 + 1.4 × size)` s.
Test endpoints: `http://www.gstatic.com/generate_204` (warm-up), `https://speed.cloudflare.com/__down` / `__up`.

## Remote test server

A Fedora VPS running a plain mihomo core as a systemd service (`mihomo.service`, config `/etc/mihomo/`,
controller `127.0.0.1:9090` with the secret in the config, mixed-port 7890, everything on localhost only).
Set up (or re-set up with a new subscription) by `testserver/setup.sh`; see its header for usage and
options (`TUN=1` enables TUN at rest, with the SSH client's IP excluded from TUN routing).

- SSH key: `~/.ssh/mihomo-testserver` (dedicated to this server). Host: not set up yet.
- **Never commit the subscription URL** or anything from `/etc/mihomo/`; it contains credentials.
- To test: copy the current `mihomo_speedtest.py` over with `scp` and run it there, as root.
- Use it to check that the script works (discovery, state restore, labels, reports), not to judge servers:
  per-server results depend on the network the test runs from.

## Open items

Planned features and decisions live in `ROADMAP.md` — keep it updated; don't start roadmap items
until the user says so.


- First real run on **Windows** (`curl.exe`, named-pipe API, localised `ping` parsing,
  `Get-CimInstance` discovery) and **macOS** — those paths are written but have never run.
- A full run with the latest changes (unreachable/failed notes, restore signal handling, direct
  curl/ping calls) hasn't been done yet.
