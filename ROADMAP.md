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
- **Connection time through the server** (replaces "latency through the proxy") — report how long it takes
  to get a response through the server, from curl's own timing (`time_starttransfer` / `time_appconnect`) on
  the calls the script already makes. Shows servers that work but feel sluggish (many short connections
  while browsing). About 3 lines.
- **Jitter** — variation between the ping times the script already collects (mean difference between
  consecutive replies), as a column next to ping and loss. About 2 lines; consider a few more pings per
  server so the number means something.
- **Compare runs** — compare two dated CSV reports to show servers that got slower or stopped working.
- **Skip transfers for dead servers** — if the warm-up never got a response, skip download/upload (saves
  ~20 s per dead server; trade-off against the "first connection is slow" problem the warm-up handles).
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
