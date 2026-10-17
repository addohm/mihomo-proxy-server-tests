# mihomo-proxy-server-tests

`mihomo_speedtest.py` tests every server in your **running** mihomo, exactly as mihomo uses it:
connection time and download/upload speed through mihomo, and ping to the address it actually dialed, each
with its jitter. For servers that don't work it shows why: mihomo's own error (e.g. "connection timed out",
"config error") plus a direct check of the server's port (blocked, refused, or reachable). It also flags
servers that are slow to start, tests disturbed by other apps' traffic, and servers behind Cloudflare's CDN
(whose ping shows the CDN, not the server). With `--trace` it also shows where the path to a server that
doesn't work stops, and whose network that is. Works with any mihomo-based client (Clash Verge Rev, Mihomo
Party, Nyanpasu, FlClash, ClashX Meta, plain mihomo), with servers listed in the config or loaded from
proxy providers.

```bash
python3 mihomo_speedtest.py                      # test everything
python3 mihomo_speedtest.py --filter "Japan"     # only matching servers
python3 mihomo_speedtest.py --size 50            # bigger transfers, for very fast servers/connections
python3 mihomo_speedtest.py --trace              # also trace the servers that don't work
python3 mihomo_speedtest.py --save-baseline      # when everything works: keep this run to compare with
python3 mihomo_speedtest.py --compare            # later: what changed since then
```

On Windows, use `py` instead of `python3` (e.g. `py mihomo_speedtest.py`).

While it runs, TUN is turned off and mihomo is put in global mode; your settings are restored at the end
(also on Ctrl+C, or when the window is closed on Windows). Results are printed and saved as
`mihomo-speedtest-YYYYMMDD-HHMMSS.md` and `.csv` in a `mihomo-speedtest` folder in your Documents folder
(or in your home folder if there is none, e.g. on a server); `--out DIR` saves them elsewhere.

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

## Comparing runs (`--save-baseline`, `--compare`)

When everything works well, run once with `--save-baseline`: that run is also saved as `baseline.csv` in
the reports folder. When something seems wrong later, run with `--compare`: after the test it lists what
changed since the baseline, in the terminal and in the Markdown report:

- servers that **stopped working** (with the cause) or **work again**;
- **big changes**, more than 30%, in download, upload, Conn ms or ping (small ones, such as a ping going
  from 1 to 1.4 ms, don't count);
- servers **not in the saved report**, and servers **gone from the config**.

`--compare REPORT.csv` compares with any earlier report instead. To make an older report the baseline,
copy it over `baseline.csv`. Nothing is compared unless you ask. Compare runs made on the same network and
at a similar time of day; anything else isn't a fair comparison. Reports from versions before the current
speed method don't get their speeds compared (the output says so).

## Tracing servers that don't work (`--trace`)

With `--trace`, after the test the script traces every server that didn't work and adds where the path
stops to the Note, with the network that owns that hop (e.g. "path trace: stops after hop 11: 141.136.111.46
(AS3257 GTT-BACKBONE)"). The Markdown report lists every hop.

- **failed** servers get a path trace (`tracert`, `tracepath` or `traceroute`).
- **unreachable** servers answer ping, so a path trace would tell nothing; they get a trace to their proxy
  port instead, which needs Linux and sudo (`sudo python3 mihomo_speedtest.py --trace`) with `traceroute` or
  `mtr` installed. "reaches the server" means the port is reachable and the problem is in the connection
  itself (e.g. credentials, TLS); "stops after hop N" shows where it's blocked.
- Servers whose address belongs to a CDN such as Fastly or Akamai are labelled like Cloudflare ones.
- Takes about a minute more. **Privacy:** hop and server addresses are looked up in Team Cymru's public
  ASN service (through mihomo's DNS, or `whois.cymru.com`); without `--trace` nothing is sent.

## Client notes

- **FlClash**: turn on *External controller* in its settings and press *Start*. Its controller has a
  secret the script can't read from FlClash's settings, so it asks for it (or pass `--secret`). If the
  client isn't started, the script stops with "mihomo isn't passing connections … is your client
  started?" instead of reporting every server as unreachable.
- **Clash Verge Rev**, **Clash Party**, **Clash Nyanpasu**: found on their own, no settings needed (Nyanpasu's
  controller secret is read from its config, which needs PyYAML; without it the script asks for it).

## Requirements

Python 3.8+, `curl` and `ping`. PyYAML is optional. For `--trace`: `tracert` (built into Windows),
`tracepath` or `traceroute` (Linux; port traces: `traceroute` or `mtr`, and sudo), `traceroute` (macOS).
Run with `--help` for all options.

Keep `mihomo_speedtest.py` and the `mihomotest/` folder together; the script runs the code in that folder.

Status: in use on Linux. Windows: tested with Clash Verge Rev, Clash Party, FlClash, Clash Nyanpasu and a
plain mihomo core (named pipe or TCP controller, TUN on or off, different proxy ports, with or without
PyYAML), from mainland China. macOS support is written but not yet tested.

Planned features: see [ROADMAP.md](ROADMAP.md).
