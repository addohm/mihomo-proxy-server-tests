# Roadmap

Planned work. Done items are removed (see git history).

## Decided

- **GUI as a local web page** (decided 2026-10-15). The script runs a small stdlib web server and opens a page
  in the user's normal browser: live results table, server checkboxes instead of `--filter`, sortable
  columns, a "restoring your settings" indicator, per-server diagnosis on click, run history.
  - Why: one language (Python) plus one HTML/JS page on every OS, no extra dependencies, no build
    toolchain. A regular browser renders the same everywhere, unlike Tauri-style system webviews
    (WebKitGTK on Linux is Clash Verge Rev's main source of UI bugs).
  - Rejected: Tkinter (dated, weak live tables/charts), Qt/PySide6 (~200 MB dependency), Tauri/Electron
    (second language and build toolchain).
  - **Layman first.** The GUI is the main product, designed for people who never open a terminal. Their
    entry point is a packaged app (double-click; no Python, no flags), which opens the GUI by default —
    so packaging is a required part of the GUI deliverable, not optional.
  - **The script is the explicit/technical entry**: `python3 mihomo_speedtest.py` stays terminal-only
    exactly as today (headless machines, scripts, cron); `--gui` opens the page from the script. No desktop
    auto-detection.
  - All options available in the GUI with defaults we establish: server checkboxes (replacing `--filter`),
    trace and baseline controls up front; size, timeout, API address and secret under "Advanced". The secret
    prompt becomes a field on the page.
  - Reports and baseline: one **per-user folder** for both script and app (e.g. `Documents/mihomo-speedtest`),
    `--out` still overrides. This changes the script's current default (next to the script).
  - Security: the page listens on localhost only, and each launch uses a random secret token in the URL,
    so other websites can't drive the local server (start tests / toggle TUN).
  - Headless use of the GUI is still possible via an SSH tunnel (`ssh -L PORT:localhost:PORT host`).

## Ideas (not yet decided)

- **mihomo's own failure reason** — read mihomo's log through the API during each test to show the exact
  error (dial timeout, connection refused, auth failure).
  - Decided: plain-language cause in the table; mihomo's raw message in the Markdown/CSV reports.
  - Decided for now: keep the four result labels, cause goes in the Note column. The user wants clearer
    labels eventually — revisit after the TCP connect check, when the classification is reliable.
  - Check first on the test server: does mihomo send warnings to API log subscribers when the client's
    log level is `error` / `silent`?
- **Direct TCP connect check** — plain TCP connect to `server:port` outside mihomo: a timeout means the port
  is blocked; it opens but mihomo fails means the server refuses (credentials/settings). TCP only, not
  hy2/tuic (UDP) — shown as "not checkable (UDP)". "Opens, then fails" can't tell a server rejecting from
  a censor cutting the connection.
  - Decided: only for failed servers (can change later).
  - Decided: also read mihomo's provider cache files to find server:port for provider-based setups; the
    same lookup improves the ping fallback.
- **Traceroute on failure** — ICMP traceroute is useless for "unreachable" servers (ICMP already gets
  through); they need a TCP traceroute to the proxy port (Linux: `traceroute -T -p PORT` needs root, or
  `tcptraceroute`; Windows has nothing built in). ICMP traceroute is still useful for "failed" servers.
  - Decided: off by default; `--trace` traces every failed server automatically. GUI later: a per-server
    "trace this server" button plus a setting to trace automatically.
  - Tools without root: `tracepath` (Linux), `traceroute` (macOS), `tracert` (Windows); limits ~20 hops,
    short waits (~10–30 s per server). Note column: short summary ("path stops after hop 9 (IP)");
    full trace in the Markdown report.
  - Decided: TCP trace to the proxy port for "unreachable" servers when running as root/admin and a capable
    tool exists; otherwise the note says "run with sudo for a port trace".
  - Decided: show hop owners (ASN / network name), best-effort. Must work from inside mainland China:
    query Team Cymru's ASN TXT records through mihomo's own DNS (`/dns/query`, the resolver the user's
    config already makes work on their network), fall back to whois.cymru.com on TCP 43, otherwise show
    the IP only. Only runs with `--trace`; document that hop IPs are sent to that lookup service.
- **Connection time through the server** (replaces "latency through the proxy") — right after the
  warm-up, 5 quick probes, each a new connection through the server to a tiny page (`generate_204`); time to
  first response from curl's `time_starttransfer`. Report the median as a "Conn ms" column, and probe
  failures in the Note column ("2 of 5 connections failed") as a through-the-server stability measure.
  About 2–5 s per working server. Decided: 5 probes. Jitter is folded in here (see next item).
- **Jitter** (folded into connection time) — average change between consecutive measurements, two kinds:
  through the server (from the 5 connection probes) and direct (from the pings). Comparing them separates
  "the server/its route wobbles" from "your own network wobbles".
  - Decided: show **both** in the terminal table and the reports (helps troubleshooting). The table is
    already ~100 characters wide, so rework column widths/headers when adding them.
  - Decided: 10 pings per server instead of 4 (pings run in the background; ~6 s extra at the end only).
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
- **Flag slow starters** (replaces "skip transfers for dead servers", rejected: never skip a server that
  might start — "if it starts, it starts"). Always run the transfers as today, and record how long the
  first response through a newly selected server took. Flag it in the Note column: "slow start: first
  response after N s", or "slow start: no response in the first 10 s" when the warm-up got nothing but the
  transfers worked. Threshold for "slow" to settle when implementing (proposal: 3 s). Pairs with
  "connection time through the server".
- **Speed excluding connection setup** — compute download/upload speed from the transfer phase only (curl's
  timings: bytes ÷ (total − time until data starts)), since connection time gets its own column.
  - Decided: the transfer-only speed **replaces** the current figure (one speed column + "Conn ms"). Reports
    must state the method, so "compare runs" doesn't mix old and new speeds.
- **Label CDN-fronted servers** — for servers reached through a CDN (ws/grpc via Cloudflare etc.) the direct
  ping measures the nearest CDN edge, not the server. Note: "via Cloudflare CDN: ping shows the CDN edge,
  not the server" (and point to "Conn ms" as the honest latency).
  - Decided: Cloudflare's published ranges built in (~22 prefixes, no network calls, works everywhere).
    Other CDNs (Fastly, Akamai, CloudFront…) recognised by network owner only when `--trace` already does
    owner lookups — no extra third-party lookups otherwise.
- **Other traffic during the test** — in global mode, the user's other proxied apps also go through the
  server under test, lowering its measured speed. The script already polls `/connections` during each
  download; use its per-connection byte counts to measure traffic that isn't the test's own.
  - Decided: per server, Note "busy: other apps used ~N MB during this test" when other traffic exceeds
    **5% of the test's own data and at least 1 MB**. At the start, one informational line (no prompt) if
    other traffic exceeds ~1 MB/s in a 2-second sample. All three numbers are single constants; tune after
    real runs.
  - Limit (document it): apps that don't use the proxy bypass mihomo and can't be seen.
  - Isolating the test from this traffic was the rejected "isolated core" / routing approach — don't
    reintroduce.
- **Ping timing** — pings currently run while the next server's download fills the connection, inflating
  ping and (now) direct jitter, which would wrongly blame the user's own network.
  - Decided: ping **all servers together at the end**, after every transfer has finished (addresses still
    collected during the run). ~15–20 s once per run; raise the ping pool size accordingly.

- **Config variations on the test server** — `testserver/setup.sh` options to switch the core between
  the setups the script must handle (TCP / Unix-socket controller, with and without secret, mixed / socks /
  http / no proxy port, TUN on / off, full profile vs provider-only), so each discovery and restore path
  can be checked.
