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
  - Later, optionally: a single-file PyInstaller bundle per OS that opens the page, so users don't need Python.
  - **Terminal mode stays** for machines without a desktop (servers, SSH sessions). Proposed: detect it
    automatically (Linux with no `DISPLAY`/`WAYLAND_DISPLAY`, or an SSH session → terminal mode; Windows and
    macOS → GUI), with `--cli` / `--gui` to override (e.g. `--cli` for scripts and cron). The web server
    listens on localhost only, so `--gui` on a headless machine still works through an SSH tunnel
    (`ssh -L PORT:localhost:PORT host`). Default behaviour to be confirmed with the user.

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
- **Compare runs** — compare two dated CSV reports to show servers that got slower or stopped working.
- **Flag slow starters** (replaces "skip transfers for dead servers", rejected: never skip a server that
  might start — "if it starts, it starts"). Always run the transfers as today, and record how long the
  first response through a newly selected server took. Flag it in the Note column: "slow start: first
  response after N s", or "slow start: no response in the first 10 s" when the warm-up got nothing but the
  transfers worked. Threshold for "slow" to settle when implementing (proposal: 3 s). Pairs with
  "connection time through the server".
- **Speed excluding connection setup** — curl's average speed includes connecting and the TLS handshake,
  so far-away servers look slower on a 10 MB test. Could compute speed from the transfer phase only
  (curl's `time_starttransfer`), or just document it.
- **Label CDN-fronted servers** — for servers behind a CDN (e.g. ws+tls via Cloudflare) the ping measures
  the CDN edge, not the server. Detect it (IP belongs to a known CDN range) and say so in the results.
- **Other traffic during the test** — in global mode, the user's other apps also go through the server
  under test, adding noise to the speeds. Options: warn in the report, or check `/connections` for
  unrelated traffic during the transfer and flag noisy results. (Isolating the test from it was the
  rejected "isolated core" / routing approach — don't reintroduce.)
- **Ping timing** — pings run while the next server's download is going, which can add a little latency.
  Could ping between transfers instead, at the cost of a slower run.

- **Config variations on the test server** — `testserver/setup.sh` options to switch the core between
  the setups the script must handle (TCP / Unix-socket controller, with and without secret, mixed / socks /
  http / no proxy port, TUN on / off, full profile vs provider-only), so each discovery and restore path
  can be checked.
