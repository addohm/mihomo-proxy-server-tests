# mihomo-proxy-server-tests

`mihomo_speedtest.py` tests every server in your **running** mihomo, exactly as mihomo uses it:
connection time and download/upload speed through mihomo, and ping to the address it actually dialed, each
with its jitter. For servers that don't work it shows why: mihomo's own error (e.g. "connection timed out",
"config error") plus a direct check of the server's port (blocked, refused, or reachable). It also flags
servers that are slow to start, tests disturbed by other apps' traffic, and servers behind Cloudflare's CDN
(whose ping shows the CDN, not the server). Works with any mihomo-based client (Clash Verge Rev, Mihomo
Party, Nyanpasu, FlClash, ClashX Meta, plain mihomo), with servers listed in the config or loaded from
proxy providers.

```bash
python3 mihomo_speedtest.py                      # test everything
python3 mihomo_speedtest.py --filter "Japan"     # only matching servers
python3 mihomo_speedtest.py --size 50            # bigger transfers, for very fast servers/connections
```

On Windows, use `py` instead of `python3` (e.g. `py mihomo_speedtest.py`).

While it runs, TUN is turned off and mihomo is put in global mode; your settings are restored at the end
(also on Ctrl+C, or when the window is closed on Windows). Results are printed and saved next to the script
as `mihomo-speedtest-YYYYMMDD-HHMMSS.md` and `.csv`.

If more than one mihomo client is running, the first one found is tested; the line "Connected to mihomo …
at …" says which. Use `--api` to choose another (e.g. its pipe, `--api \\.\pipe\NAME`).

## Reading the results

- **Conn ms**: time until the first response over a new connection through the server (median of 5). This
  is the latency you feel when an app opens a connection.
- **Ping ms**: direct ping to the server's address, 10 pings, sent after all transfers have finished.
- **± (jitter)**: the average change between consecutive measurements. High jitter on Conn ms but not on
  Ping ms points at the server or its route; high jitter on both points at your own network.
- **Down / Up Mbps**: speed of the transfer itself, without connection setup (that's in Conn ms). On very
  fast servers a 10 MB transfer can finish too quickly to time reliably; the Note then says so. Run again
  with a larger `--size` (e.g. `--size 50`) for an accurate figure.
- **"slow: download hit the time limit"**: the transfer ran for the full `--timeout` (25 s); its speed is
  still measured from what arrived. On slow networks (e.g. from mainland China) use a smaller `--size`
  (e.g. `--size 2`) so transfers can finish, or a longer `--timeout`, which makes runs longer.

| Result | Meaning |
|---|---|
| `ok` | download and upload both worked |
| `partial` | one of them failed (the Note says which, and why) |
| `unreachable` | no proxy connection, but the server answers ping — it is online; something between you and its proxy port (or the server itself) refused |
| `failed` | no proxy connection and no ping reply — it may be down, or just block ping |

The **Note** column explains what went wrong, in two parts:

- **The cause**, from mihomo's own error: e.g. "connection timed out", "server refused the connection",
  "config error: invalid server address", "TLS/certificate problem".
- **A direct check** of the server's port, made outside mihomo: `no answer` (blocked on your network, or
  down), `refused` (nothing listening on that port), `open` (your network reaches it — the proxy connection
  itself was rejected, or the config is wrong). Not possible for UDP protocols such as Hysteria2 and TUIC.

Other Notes:

- "N of 5 connections failed": some of the Conn ms connections didn't get an answer, a sign of an unstable
  server.
- "busy: other apps used ~N MB during this test": during the test every app that uses mihomo goes through the
  server under test, so its speed may read low. Apps that bypass mihomo can't be seen.
- "via Cloudflare CDN": the server is reached through Cloudflare, so ping measures the nearest Cloudflare
  edge, not the server. Use Conn ms for its real latency.
- "slow start: first response after N s": the server works but takes a while to answer its first
  connection.

The Markdown report also lists mihomo's exact error message for each server that didn't work. Reports
without a "Speed method" line come from older versions, whose speeds included connection setup and so read
lower.

## Requirements

Python 3.8+, `curl` and `ping`. PyYAML is optional. Run with `--help` for all options.

Keep `mihomo_speedtest.py` and the `mihomotest/` folder together; the script runs the code in that folder.

Status: in use on Linux. Windows: tested with Clash Verge Rev (named pipe, TUN on or off, different
proxy ports, with or without PyYAML); other Windows clients not yet. macOS support is written but not yet
tested.

Planned features: see [ROADMAP.md](ROADMAP.md).
