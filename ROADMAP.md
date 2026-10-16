# Roadmap

Planned work and the decisions taken about it. Every item has been discussed with the user (2026-10-15);
nothing here is started until the user says so. Done items are removed (see git history).

## Implementation order

1. ~~Config variations matrix~~ — done (`testserver/variant.py`, `matrix.py`)
2. ~~Diagnosis: mihomo's failure reason, direct TCP check (+ provider file lookup), slow-start flag~~ — done
3. ~~Accuracy: connection probes + both jitters, 10 pings at the end, transfer-only speed, other-traffic
   flag, CDN labels, table layout rework~~ — done
4. Remote tester inside China (reverse SSH tunnel; see the section below)
5. Windows paths: test and fix on the China tester / the Windows 11 VM
6. `--trace` (path trace, TCP trace with root, hop owners, CDN-by-owner)
7. Per-user reports folder + baseline / `--compare`
8. Clearer result labels
9. GUI (local web page)
10. Packaging + releases

Order decided 2026-10-16: `--trace` waits until the China tester and the Windows work are done.

## 6. Traceroute on failure (`--trace`)

- An ICMP trace is useless for "unreachable" servers (ICMP already gets through); they need a TCP trace to
  the proxy port. An ICMP trace is still useful for "failed" servers.
- Decided: off by default; `--trace` traces every failed server automatically. GUI later: a per-server
  "trace this server" button plus a setting to trace automatically.
- Tools without root: `tracepath` (Linux), `traceroute` (macOS), `tracert` (Windows); limits ~20 hops, short
  waits (~10–30 s per server). Note: short summary ("path stops after hop 9 (IP)"); full trace in the
  Markdown report.
- Decided: TCP trace to the proxy port for "unreachable" servers when running as root/admin and a capable
  tool exists (Linux `traceroute -T -p PORT` / `tcptraceroute`; Windows has nothing built in); otherwise
  the Note says "run with sudo for a port trace".
- Decided: show hop owners (ASN / network name), best-effort. Must work from inside mainland China: query
  Team Cymru's ASN TXT records through mihomo's own DNS (`/dns/query`, the resolver the user's config
  already makes work on their network), fall back to whois.cymru.com on TCP 43, otherwise show the IP only.
  Only runs with `--trace`; document that hop IPs are sent to that lookup service.

## 7. Reports folder and compare runs

- Decided: reports and baseline go to one **per-user folder** for both script and app (e.g.
  `Documents/mihomo-speedtest`); `--out` still overrides. This changes the script's current default (next
  to the script).
- **Compare runs** — show what changed between runs: servers that stopped working / recovered, and big
  swings in speed, connection time or ping.
  - Decided: **explicit only, never automatic** (the tool is mostly used when problems are already seen).
  - Baseline: `--save-baseline` saves this run as `baseline.csv` in the reports folder; `--compare` runs the
    test and compares against the baseline; `--compare REPORT.csv` compares against a specific report.
    Marking an older report as baseline = copy it over `baseline.csv` (document in README); GUI later:
    "mark as baseline" button.
  - When no baseline exists, one line at the end of a run: "Tip: run with --save-baseline when everything
    works well, so you can compare later." No prompt.
  - Servers matched by name (renamed = removed + new). "Big swing" = more than ~30% change.
  - Reports record their speed method; speed changes aren't compared across methods (with a note).
  - The comparison header shows both dates and warns that a baseline from another network or time of day
    isn't a fair comparison.

## 8. Clearer result labels

- The user wants clearer answers than ok / partial / unreachable / failed. Every failure now has a cause
  (mihomo's error) and a direct check, so labels like "blocked", "refused", "config error" can be derived;
  step 3 is done, so this can be revisited.

## 9–10. GUI and packaging (decided 2026-10-15)

- **GUI as a local web page.** The program runs a small stdlib web server and opens a page in the user's
  normal browser: live results table, server checkboxes instead of `--filter`, sortable columns, a
  "restoring your settings" indicator, per-server diagnosis on click, run history.
  - Why: one language (Python) plus one HTML/JS page on every OS, no extra dependencies, no build
    toolchain. A regular browser renders the same everywhere, unlike Tauri-style system webviews
    (WebKitGTK on Linux is Clash Verge Rev's main source of UI bugs).
  - Rejected: Tkinter (dated, weak live tables/charts), Qt/PySide6 (~200 MB dependency), Tauri/Electron
    (second language and build toolchain).
- **One core, one program.** `mihomotest/` is the single core; the CLI and the GUI are thin layers on it.
  Two ways to get the *same* program:
  - from source: `python3 mihomo_speedtest.py` — terminal by default (headless machines, scripts, cron),
    `--gui` opens the page; no desktop auto-detection;
  - packaged executable: **no arguments (double-click) → GUI; any arguments (e.g. `--cli`, `--filter`) →
    terminal**. Windows build is a console program (its window shows "running in your browser; close this
    window to quit"); macOS ships a `.app` (terminal use = the binary inside it); Linux a single file.
- **Layman first.** The GUI is the main product, designed for people who never open a terminal. Their entry
  point is the packaged app (double-click; no Python, no flags), so packaging is a required part of the GUI
  deliverable, not optional.
- All options available in the GUI with defaults we establish: server checkboxes, trace and baseline
  controls up front; size, timeout, API address and secret under "Advanced". The secret prompt becomes a
  field on the page.
- Security: the page listens on localhost only, and each launch uses a random secret token in the URL, so
  other websites can't drive the local server (start tests / toggle TUN).
- Headless use of the GUI is still possible via an SSH tunnel (`ssh -L PORT:localhost:PORT host`).
- **Packaging:** builds for Windows x64, macOS (Apple Silicon + Intel) and Linux x86-64, made by **GitHub
  Actions** on each tagged release and published on the Releases page. Keep the workflow minimal: read-only
  by default, `contents: write` only on the release job, no other secrets, pinned action versions; review
  `.github/workflows/` before each release. **No paid signing**: Windows unsigned, macOS ad-hoc signed only
  (free; PyInstaller does it, and Apple Silicon needs it to run at all), Linux none. Publish SHA-256
  checksums with each release. README gets illustrated first-run steps for SmartScreen ("More info → Run
  anyway") and Gatekeeper (System Settings → Privacy & Security → "Open Anyway").

## Testing other platforms

- No Mac is available. macOS (and Windows) can be covered by GitHub Actions runners: a job that starts a
  second local mihomo as a stand-in "server" (no subscription needed) and runs the script's real platform
  paths (discovery, named pipe, ping parsing, curl, restore).
- A Windows 11 VM is available (2026-10-15) for testing the Windows paths by hand.

## 4–5. Remote tester inside China, Windows paths (added 2026-10-16)

- Goal: a Windows machine in mainland China, behind a dynamic IP (no inbound SSH), that receives our changes,
  runs the script (and later the matrix / packaged builds) against a real desktop client there, and reports
  the results back. This covers what the US test server can't: Chinese networks, GFW behaviour, the
  China-only paths (e.g. hop owners via mihomo's DNS in step 6) and Windows.
- Decided (2026-10-16): **reverse SSH tunnel**. The China machine keeps an outbound SSH connection to our
  test VPS with a remote forward (`ssh -R`) to its own SSH server; we reach it with plain SSH through the
  VPS, as with the test server today. Rejected for now: a second Discord bot (Discord is blocked in
  mainland China and would have to go through the mihomo under test), a job queue on the VPS, Tailscale,
  a GitHub Actions self-hosted runner (GitHub is unreliable from China).
- Started 2026-10-16: `testserver/setup-windows.ps1` (PowerShell, as a fresh Windows 11 has no Python).
  Not yet run on any Windows machine. What it does:
  - installs OpenSSH client + server and Python 3 (winget, all users); sshd listens on 127.0.0.1 only
    (reached only through the tunnel), keys only, PowerShell as the SSH shell; asks for the public keys
    allowed to log in; no sleep on mains power;
  - **turns off VPNs** (user's decision, 2026-10-16): disconnects Windows VPN connections and disables
    VPN/TUN adapters, so the tunnel goes direct; for a mihomo client's TUN it tells the user to turn TUN
    off in the client too. This replaces the earlier plan of excluding the VPS address from TUN routing;
  - makes the PC's own tunnel key, **asks for the server address** (it can change: re-run, enter the new
    one, Enter for the rest), shows the key plus the command to run on the server, then starts a
    scheduled task (SYSTEM, at startup) that keeps `ssh -R 127.0.0.1:PORT:127.0.0.1:22` up: keepalives
    every 15 s notice a dead tunnel within a minute, then it reconnects every 15 s, **indefinitely by
    default or a chosen number of attempts in a row** (asked at setup; a connection that lasted a minute
    resets the count; user's request, 2026-10-16); setup checks the tunnel came up and shows
    `tunnel.log` if not;
  - server side: `testserver/tunnel-host.sh` (any Linux server; also `TUNNEL_KEY=` in `setup.sh`, so
    other VPSes can be used); the PC's setup prints the one command to run. A user (`tunnel`) with no
    shell whose key is limited to `restrict,port-forwarding,permitlisten="127.0.0.1:PORT"`, an sshd rule
    allowing it remote forwards only (no `-L` into the server), and `ClientAliveInterval 15` so a dropped
    tunnel frees its port quickly.
- Windows paths (step 5), done before the VM is reachable (from reading the code, checked on Linux only):
  restore runs before the log stream is closed (closing a pipe can block); busy-pipe retry; closing the
  console window restores like Ctrl+C; output can't crash on characters the console encoding lacks. To
  check on Windows: all of these, discovery (PowerShell, `%APPDATA%`, default pipes) with Clash Verge Rev
  over its pipe and over TCP, localised ping, `curl.exe` revocation checks, and the matrix (needs a
  Windows version of `matrix.py`).
- Still open:
  - Tests still run detached and are polled from a log, as with the matrix.
  - SSH from China to a foreign VPS can be throttled at times; check how stable it is before relying on it.
  - Safety: the agent may only run this project's script and tooling, updates come only from this repo,
    and the machine's subscription and config files are never sent out, as with the test server.
