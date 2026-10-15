# mihomo_speedtest.py — handoff context

Status (2026-10-09): **working and in use** on Linux (Fedora/Nobara, Clash Verge Rev, mihomo v1.19.32).
Windows and macOS code paths are written but **have never been run**.

```bash
python /srv/aidev/mihomo_speedtest.py            # whole test, no prompts
python /srv/aidev/mihomo_speedtest.py --filter "Daily Usage 1|^Japan$"   # subset
```

Options: `--filter REGEX`, `--size MB` (default 10; upload is half), `--timeout SEC` (25),
`--api ADDR` (`127.0.0.1:9090`, `unix:/path.sock`, `\\.\pipe\name`), `--secret`, `--out DIR`.

## What the user wants (settled — do not redesign)

A simple, true test of **exactly what mihomo does with each server in its running config**. No more, no less.
Run it → it tests → prints a table → auto-saves `.md` + `.csv` next to the script. No prompts
(the only possible prompt is the controller secret when it can't be found).

Must stay universal: Linux + Windows (+ macOS), **any mihomo-based client** (Clash Verge Rev, Mihomo Party,
Nyanpasu, FlClash, ClashX Meta, plain mihomo) — never Clash-Verge-specific.

### Explicitly rejected — do not reintroduce
- An "isolated" temporary mihomo core built from config files (had to replicate DNS/`nameserver-policy`, fragile).
- Relay / `dialer-proxy` re-tests, binding to the physical interface, auto mode selection, confirmation prompts.
- Any routing "shenanigans". Failed servers are simply reported.

## How it works

1. Prerequisite check: Python 3.8+, curl, ping, bash (Linux/macOS) or PowerShell + `curl.exe` (Windows).
   PyYAML is optional (only for finding the secret in config files and the ping fallback). No mihomo binary needed.
2. Finds the running mihomo's external-controller API (`find_controller`): running process command line
   (`-ext-ctl`, `-ext-ctl-unix`, `-ext-ctl-pipe`, `-secret`), then `external-controller*` / `secret` in config
   files (`config_files`), then default ports/pipes. Talks HTTP/1.0 over TCP, Unix socket or Windows named pipe
   with no extra libraries (`Controller`).
3. `MihomoState`: records mode, TUN, `GLOBAL` selection; **turns TUN off, sets mode `global`**; opens a temporary
   `mixed-port` only if the client has no local proxy port. `restore()` runs in `finally` (also Ctrl+C, SIGTERM/HUP/BREAK).
4. Servers = `/proxies` entries that aren't groups, in `GLOBAL` order (includes subscription/provider servers).
5. Per server (`test_server`), one at a time: `PUT /proxies/GLOBAL` → warm-up requests (≤10 s, the user observed
   first connections are slow) → download via curl through mihomo → upload (one retry if warm-up worked).
   Timed-out transfers keep their measured speed with a "slow: … hit the time limit" note.
6. Ping: the IP mihomo **actually dialed**, read from `/connections` → `metadata.remoteDestination` while the
   download runs. Servers that never connect fall back to the config's `server` host resolved by mihomo's own
   DNS (`/dns/query`). Pings run in a thread pool alongside the next server's test.
7. Results: `ok`, `partial`, `unreachable` (no proxy connection but **answers ping**), `failed` (no connection,
   no ping). When any are unreachable, a note says that unreachable does NOT mean the server is down
   (terminal + Markdown). Estimate ≈ `servers × (6 + 1.4 × size)` s — matched 14m40 est. vs 14m17 actual for 44 servers.

Test endpoints: `http://www.gstatic.com/generate_204` (warm-up), `https://speed.cloudflare.com/__down` / `__up`.

## Findings on the user's setup (so they aren't re-investigated)

- User is in mainland China. Client: Clash Verge Rev, API at
  `unix:/run/clash-verge-service/users/1000/verge-mihomo.sock`, mixed-port 7897, normally TUN on + rule mode,
  GLOBAL `DIRECT`. Runtime config is root-owned (unreadable); profiles live in
  `~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/`.
- The provider's server hostnames only resolve through private DoH servers in the config's `nameserver-policy`.
  Using mihomo itself (not a copy) avoids that whole problem.
- **Most plain `trojan` servers are unreachable from this network** (blocked port, e.g. 43000): they answer ICMP,
  direct TCP to the port times out, Clash Verge's own delay test times out, websites fail. They DO carry traffic
  when chained through a vless server — that's why some early runs looked fine (proved via `/connections`:
  `chain: [RLT][CN] Daily Usage 1 <- Wannaflix`). This is a real result, not a script bug.
  Working: Canada, Hong Kong, TORRENTING SERVER (France) trojans, all `[RLT][CN]` vless, trixu-hy2.
  `[TEST][IPV6]` servers fail (no IPv4 / no IPv6 route). Turkey fails entirely.
- Last full run: 24 ok, 2 partial (since fixed: slow-upload handling), 18 failed — now shown as
  15 unreachable + 3 failed.

## Working agreements with the user

- **Do not run the script (or any test) yourself or in the background.** It toggles TUN/mode on the user's live
  client. Give the exact command; the user runs it and reports back. Static checks (`python3 -m py_compile`) are fine.
- Keep changes small and within the design above; the user has twice asked for simplicity after over-engineering.
- Reports land next to the script: `mihomo-speedtest-YYYYMMDD-HHMMSS.{md,csv}` (CSV has a UTF-8 BOM for Excel).

## Open items

- First real run on **Windows** (PowerShell quoting, `curl.exe`, named-pipe API, `ping` output parsing,
  `Get-CimInstance` process discovery) and **macOS**.
- Full 44-server run with the latest changes (unreachable label, slow-upload handling, progress-line trimming)
  hasn't been done yet; a 3-server run confirmed the labels and note.
- Previous versions (isolated/relay modes) are backed up only in a temporary scratchpad; not needed.
