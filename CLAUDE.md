# CLAUDE.md

Single-file tool: `mihomo_speedtest.py` tests every server in the user's **running** mihomo, exactly as
mihomo uses it, and writes a Markdown + CSV report. `context.md` is the original handoff (2026-10-09) with
more background; this file is the living summary. Keep both, README and the script's docstring in sync
when behaviour or options change.

## Hard rules

- **Never run the script, or any live test, yourself (not even in the background).** It turns TUN off and
  switches mode/selection on the user's live client. Give the exact command; the user runs it and reports back.
- Static checks are fine: `python3 -m py_compile mihomo_speedtest.py`.
- Keep changes small and inside the design below. The user has twice asked for simplicity after over-engineering.
- Stay universal: Linux, Windows, macOS and **any mihomo-based client** (Clash Verge Rev, Mihomo Party,
  Nyanpasu, FlClash, ClashX Meta, plain mihomo). Nothing Clash-Verge-specific.
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
python3 mihomo_speedtest.py --filter "Daily Usage 1|^Japan$"  # subset by regex
```

Options: `--filter REGEX`, `--size MB` (default 10; upload is half), `--timeout SEC` (25),
`--api ADDR` (`127.0.0.1:9090`, `unix:/path.sock`, `\\.\pipe\name`), `--secret` (or `MIHOMO_SECRET`),
`--out DIR` (default: next to the script).

## How it works (map of the script)

1. `check_prereqs`: Python 3.8+, curl, ping, and bash (Linux/macOS) or PowerShell + `curl.exe` (Windows).
   External commands go through `run_shell` (bash or PowerShell, argv quoted).
2. `find_controller`: process command line (`-ext-ctl`, `-ext-ctl-unix`, `-ext-ctl-pipe`, `-secret`) →
   `external-controller*` / `secret` in `config_files()` → default ports/pipes. `Controller` speaks raw
   HTTP/1.0 over TCP, Unix socket or Windows named pipe.
3. `MihomoState`: records mode, TUN, `GLOBAL` selection; turns TUN off, sets mode `global`; opens a temporary
   `mixed-port` only if there's no local proxy port. `restore()` runs in `finally` (also Ctrl+C,
   SIGTERM/HUP/BREAK).
4. `list_servers`: `/proxies` entries that aren't groups (`GROUP_TYPES`), in `GLOBAL` order.
5. `test_server`, one server at a time: `PUT /proxies/GLOBAL` → warm-up probes (≤10 s; first connections are
   slow) → download via curl through mihomo → upload (one retry if warm-up worked). Timed-out transfers
   (curl rc 28) keep their speed with a "slow: … hit the time limit" note.
6. Ping the IP mihomo **actually dialed** (`remote_ip`: `/connections` → `metadata.remoteDestination`, polled
   during the download). Servers that never connect fall back to the config's `server` host resolved through
   mihomo's DNS (`config_hosts` + `mihomo_resolve` via `/dns/query`). Pings run in a thread pool.
7. Results: `ok`, `partial`, `unreachable` (no proxy connection but answers ping), `failed` (neither).
   When any are unreachable, `UNREACHABLE_NOTE` is shown (terminal + Markdown).
8. `save_reports`: `mihomo-speedtest-YYYYMMDD-HHMMSS.{md,csv}` (CSV has a UTF-8 BOM for Excel). Falls back
   to cwd, then the temp dir, if `--out` isn't writable. Reports are git-ignored.

Estimate ≈ `servers × (6 + 1.4 × size)` s (14m40 est. vs 14m17 actual for 44 servers).
Test endpoints: `http://www.gstatic.com/generate_204` (warm-up), `https://speed.cloudflare.com/__down` / `__up`.

## User's setup (known findings — don't re-investigate)

- Mainland China. Fedora/Nobara, Clash Verge Rev, mihomo v1.19.32. API at
  `unix:/run/clash-verge-service/users/1000/verge-mihomo.sock`, mixed-port 7897, normally TUN on + rule mode,
  GLOBAL `DIRECT`. Runtime config is root-owned; profiles in
  `~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/`.
- Provider hostnames only resolve via private DoH in `nameserver-policy` — using mihomo itself avoids this.
- Most plain `trojan` servers are **genuinely unreachable** from this network (blocked port, e.g. 43000):
  they answer ICMP, TCP to the port times out, the client's own delay test fails. They work only when chained
  through a vless server. Real result, not a script bug.
  Working: Canada, Hong Kong, TORRENTING SERVER (France) trojans, all `[RLT][CN]` vless, trixu-hy2.
  `[TEST][IPV6]` servers fail (no route). Turkey fails entirely.

## Open items

- First real run on **Windows** (PowerShell quoting, `curl.exe`, named-pipe API, `ping` parsing,
  `Get-CimInstance` discovery) and **macOS** — those paths are written but have never run.
- A full 44-server Linux run with the latest changes (unreachable label, slow-upload handling,
  progress-line trimming) hasn't been done; a 3-server run confirmed the labels and note.
