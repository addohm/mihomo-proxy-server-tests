# Roadmap

Planned work and the decisions taken about it. Every item has been discussed with the user (2026-10-15);
nothing here is started until the user says so. Done items are removed (see git history).

## Implementation order

1. ~~Config variations matrix~~ — done (`testserver/variant.py`, `matrix.py`)
2. ~~Diagnosis: mihomo's failure reason, direct TCP check (+ provider file lookup), slow-start flag~~ — done
3. ~~Accuracy: connection probes + both jitters, 10 pings at the end, transfer-only speed, other-traffic
   flag, CDN labels, table layout rework~~ — done
4. ~~Remote tester inside China (reverse SSH tunnel; see the section below)~~ — done 2026-10-17 (Windows 11 VM
   through revtunnel; full run from China, all tests run on it, survives reboots)
5. ~~Windows paths: test and fix on the China tester / the Windows 11 VM~~ — done 2026-10-17 (Clash Verge
   Rev, Clash Party, FlClash, Clash Nyanpasu, plain mihomo)
6. ~~`--trace` (path trace, TCP trace with root, hop owners, CDN-by-owner)~~ — done 2026-10-17
7. ~~Per-user reports folder + baseline / `--compare`~~ — done 2026-10-17
8. ~~Clearer result labels~~ — done 2026-10-17
9. GUI (local web page) — first version 2026-10-17 (`--gui`); packaged "double-click → GUI" comes with step 10
10. Packaging + releases

Order decided 2026-10-16: `--trace` waits until the China tester and the Windows work are done.

## 6. Traceroute on failure (`--trace`)

Done 2026-10-17 as decided below (`mihomotest/trace.py`; details in CLAUDE.md, "How it works" 6b). Checked on
the Linux test server as root (path trace, TCP port traces with `mtr`, owners through mihomo's DNS) and
without root ("run with sudo"), and on the Windows VM from mainland China (`tracert`, owners through Clash
Verge Rev's DNS, "not available on Windows" for port traces). Also: servers whose direct check says "no
route" aren't traced; a trace that runs out of hops says so instead of "stops". GUI (per-server button) is
step 9.

- An ICMP trace is useless for servers that answer ping (ICMP already gets through; now `blocked` /
  `refused` / `rejected`); they need a TCP trace to the proxy port. An ICMP trace is still useful for those
  that don't (`no answer`).
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

Done 2026-10-17 as decided below (details in CLAUDE.md, "How it works" 8). Checked on the Linux test server
(no baseline → clear stop; `--save-baseline`; a server blocked with nftables → "Stopped working") and on the
Windows VM (reports in `Documents\mihomo-speedtest`, tip, baseline, compare). Also decided while doing it:
a server missing from the saved report is listed as "not in the saved report (new, or not tested then)",
since a filtered baseline can't tell the two apart; small swings need at least 2 Mbps / 20 ms as well as 30 %.

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

- The user wanted clearer answers than ok / partial / unreachable / failed. Every failure has a cause
  (mihomo's error) and a direct check, so the label is derived from those plus the ping.
- Done 2026-10-17 (proposed and agreed with the user): `ok`, `partial`, `blocked` (pings, port no answer),
  `refused`, `rejected` (port open, connection fails), `no answer` (neither), `no route`, `bad config`,
  `DNS failed`, `firewall`, `no selector`, fallback `failed`; one plain sentence under the table for each
  label that appears; summary "N ok, N blocked, … of N servers". Details in CLAUDE.md, "How it works" 7.

## 9–10. GUI and packaging (decided 2026-10-15)

First GUI version done 2026-10-17 (`mihomotest/gui.py` + `gui.html`; CLAUDE.md "How it works" 9): server
checkboxes with search, trace / baseline / compare up front, size and time limit under Advanced, the secret
as a page field when needed, live sortable table with per-row details, status with a "restoring your
settings" phase, Stop (restores at once), report history, Quit. Still to do for step 9 after the user has
tried it: feedback; the packaged program opening it on a double-click is part of step 10.

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
- A Windows 11 VM (in mainland China, reached through the reverse tunnel) is the Windows tester; see
  "4–5" below and `testserver/README.md`.

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
- Tunnel set up 2026-10-16 with a first `testserver/setup-windows.ps1` + `tunnel-host.sh`; the VM's tunnel
  has worked since. Decided along the way: the PC **turns off VPNs** so the tunnel goes direct (user,
  replaces excluding the VPS from TUN routing); reconnects **indefinitely by default or N attempts in a
  row** (user); the server user may only hold its own port (no shell, no `-L` into the server).
- Windows paths (step 5), done before the VM is reachable (from reading the code, checked on Linux only):
  restore runs before the log stream is closed (closing a pipe can block); busy-pipe retry; closing the
  console window restores like Ctrl+C; output can't crash on characters the console encoding lacks.
  First runs on the VM (2026-10-16): discovery over Clash Verge Rev's pipe, API, log stream, `curl.exe`,
  ping, reports, restore and the encoding fix all work (details in CLAUDE.md). Ctrl+C and closing the
  window restore everything (`testserver/win_interrupt.py`). The Windows matrix
  (`testserver/win_matrix.py`) passes 6 of 6; TUN on at rest and the PyYAML path for failed servers pass
  too. A TCP controller can't be switched on through the API (only at start-up); it's covered by the
  Linux matrix. **Step 5 done 2026-10-16** for Clash Verge Rev; other Windows clients (Mihomo Party,
  Nyanpasu, FlClash) and a non-English Windows haven't run (ping parsing checked offline for zh / de /
  ja).
- **Tester setup made clear and generic: done 2026-10-16, as its own project, revtunnel**
  (`addohm/revtunnel`). After the first VM run, where the user couldn't tell what was going on or what to
  do, the user asked for guided steps that say what, why and **where** (PC / server), and for a tool that
  works for any reverse tunnel. Decided with the user: the server step stays manual but guided (banner,
  one-line command, also saved to a Desktop file; the server script asks for the key if missing); re-runs
  show the status and a menu; a double-click `.cmd`, downloaded from GitHub with Raw; Windows and Linux
  PCs; any port on the PC; named tunnels; every answer also an option. This repo keeps only the preset
  `testserver/tester-windows.cmd` (keep awake, VPNs off, Python 3.13 with PyYAML). Pending: move the VM
  to the preset (see CLAUDE.md).
- Still open:
  - Tests still run detached and are polled from a log, as with the matrix.
  - SSH from China to a foreign VPS can be throttled at times; check how stable it is before relying on it.
  - Safety: the agent may only run this project's script and tooling, updates come only from this repo,
    and the machine's subscription and config files are never sent out, as with the test server.
