# mihomo-proxy-server-tests

`mihomo_speedtest.py` tests every server in your **running** mihomo, exactly as mihomo uses it:
connection time and download/upload speed through mihomo, and ping to the address it actually dialed, each
with its jitter. For servers that don't work it shows why: mihomo's own error (e.g. "connection timed out",
"config error") plus a direct check of the server's port (blocked, refused, or reachable). It also flags
servers that are slow to start, tests disturbed by other apps' traffic, and servers behind Cloudflare's CDN
(whose ping shows the CDN, not the server). With `--trace` it also shows where the path to a server that
doesn't work stops, and whose network that is. Works with any mihomo-based client (Clash Verge Rev, Clash
Party (formerly Mihomo Party), Clash Nyanpasu, FlClash, ClashX Meta, plain mihomo), with servers listed in the config or loaded from
proxy providers.

```bash
python3 mihomo_speedtest.py                      # test everything
python3 mihomo_speedtest.py --filter "Japan"     # only matching servers
python3 mihomo_speedtest.py --size 50            # bigger transfers, for very fast servers/connections
python3 mihomo_speedtest.py --trace              # also trace the servers that don't work
python3 mihomo_speedtest.py --save-baseline      # when everything works: keep this run to compare with
python3 mihomo_speedtest.py --compare            # later: what changed since then
python3 mihomo_speedtest.py --gui                # the same in your web browser
```

On Windows, use `py` instead of `python3` (e.g. `py mihomo_speedtest.py`).

No Python? Use the packaged program instead (next section).

While it runs, TUN is turned off and mihomo is put in global mode; your settings are restored at the end
(also on Ctrl+C, or when the window is closed on Windows). Results are printed and saved as
`mihomo-speedtest-YYYYMMDD-HHMMSS.md` and `.csv` in a `mihomo-speedtest` folder in your Documents folder
(or in your home folder if there is none, e.g. on a server); `--out DIR` saves them elsewhere.

If more than one mihomo client is running, the first one found is tested; the line "Connected to mihomo …
at …" says which. Use `--api` to choose another (e.g. its pipe, `--api \\.\pipe\NAME`).

## Packaged program (no Python needed)

Ready-made programs are on the [Releases page](../../releases): download the one for your system. They're
the same program; started with a **double-click (no options) they open the page in your browser** (`--gui`);
with any option, e.g. `--cli` or `--filter Japan`, they run in the terminal like the script.

| System | File | First start |
|---|---|---|
| Windows 10/11 (x64) | `mihomo-speedtest-VERSION-windows-x64.exe` | Double-click. Windows may say "Windows protected your PC": click **More info**, then **Run anyway** (the program isn't signed; that costs money). A black window opens and says the test runs in your browser: close it to quit. |
| macOS, Apple Silicon (M1 and later) | `mihomo-speedtest-VERSION-macos-arm64.zip` | Unzip, move `mihomo-speedtest.app` to Applications, double-click. macOS says it can't check the app: click **Done**, open **System Settings → Privacy & Security**, scroll down to the message about mihomo-speedtest and click **Open Anyway**, then confirm. Only needed once. |
| macOS, Intel | `mihomo-speedtest-VERSION-macos-x64.zip` | The same as Apple Silicon. |
| Linux (x86-64, glibc 2.35+, e.g. Ubuntu 22.04 or newer) | `mihomo-speedtest-VERSION-linux-x64` | `chmod +x mihomo-speedtest-*-linux-x64`, then run it: without options it opens the page in your browser, `--cli` runs in the terminal. |

On macOS the app has no window or Dock icon of its own: the page in your browser is the app. Quit with the
page's **Quit** button; it also quits by itself five minutes after the page was closed. In the terminal:
`/Applications/mihomo-speedtest.app/Contents/MacOS/mihomo-speedtest --cli` (or any other option).

Each release has a `SHA256SUMS.txt` with every file's checksum, to check a download: `sha256sum FILE`
(Linux), `shasum -a 256 FILE` (macOS), `Get-FileHash FILE` (Windows PowerShell). The programs are built
from this repository by GitHub Actions ([the workflow](.github/workflows/build.yml)), which also tests each
one on its system before a release.

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

| Result | What was found | What it usually means |
|---|---|---|
| `ok` | download and upload both worked | |
| `partial` | one of them didn't | the Note says which, and why |
| `blocked` | the address answers ping, its port gets no answer | the port is blocked on your network |
| `refused` | the port refuses connections | the proxy service is down, or the port is wrong |
| `rejected` | the port is reachable, the proxy connection fails | wrong or expired credentials, TLS/certificate, or the protocol is interfered with |
| `no answer` | neither ping nor the port answers | the server is down, or everything to it is blocked (some ignore ping) |
| `no route` | no route to the address | e.g. an IPv6 server on an IPv4-only connection |
| `bad config` | mihomo says the entry is invalid | fix the server's entry in the profile |
| `DNS failed` | the server's name doesn't resolve | the name is gone, or DNS is blocked |
| `firewall` | a firewall on this computer blocked it | local security software |
| `no selector` | no proxy group can select it | it can't be tested through mihomo |
| `failed` | none of the above fits | see the Note (e.g. UDP protocols, which have no port check) |

Under the table, the results that appear are explained in a sentence each.

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

## In your browser (`--gui`)

`--gui` opens the test as a page in your normal web browser: choose servers with checkboxes, set the options,
press **Start**, and watch the results come in. Click a column to sort, and a row for its details (the cause,
the port check, mihomo's own error, the trace). **Stop** ends the test after the current server and restores
your settings right away. Earlier reports open from the page too.

The page is served by the script itself, on your computer only (127.0.0.1), and its address holds a random
key, so other websites can't use it. On a server without a browser, add `--no-browser` and open the printed
address through an SSH tunnel (`ssh -L PORT:127.0.0.1:PORT server`). Close the window (or press Ctrl+C) to
quit; a running test is stopped and your settings restored.

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

- servers that **don't answer ping** (e.g. `no answer`) get a path trace (`tracert`, `tracepath` or
  `traceroute`).
- servers that **do** (e.g. `blocked`, `refused`, `rejected`) would tell nothing from a path trace; they get a
  trace to their proxy port instead, which needs Linux and sudo (`sudo python3 mihomo_speedtest.py --trace`) with `traceroute` or
  `mtr` installed. "reaches the server" means the port is reachable and the problem is in the connection
  itself (e.g. credentials, TLS); "stops after hop N" shows where it's blocked.
- Servers whose address belongs to a CDN such as Fastly or Akamai are labelled like Cloudflare ones.
- Takes about a minute more. **Privacy:** hop and server addresses are looked up in Team Cymru's public
  ASN service (through mihomo's DNS, or `whois.cymru.com`); without `--trace` nothing is sent.

## Client notes

- **FlClash**: turn on *External controller* in its settings and press *Start*. Its controller has a
  secret the script can't read from FlClash's settings, so it asks for it (or pass `--secret`). If the
  client isn't started, the script stops with "mihomo isn't passing connections … is your client
  started?" instead of reporting every server as not working.
- **Clash Verge Rev**, **Clash Party**, **Clash Nyanpasu**, **plain mihomo**: found on their own, no settings
  needed (Nyanpasu's and plain mihomo's controller secret is read from their config, which needs PyYAML;
  without it the script asks for it). On Windows this also works from a normal (not administrator) window,
  including Clash Verge Rev's service mode.

## Requirements

Python 3.8+, `curl` and `ping`. PyYAML is optional. `--gui` needs only a web browser. For `--trace`: `tracert` (built into Windows),
`tracepath` or `traceroute` (Linux; port traces: `traceroute` or `mtr`, and sudo), `traceroute` (macOS).
Run with `--help` for all options.

Keep `mihomo_speedtest.py` and the `mihomotest/` folder together; the script runs the code in that folder.
The packaged program needs nothing else (PyYAML is included); it uses the system's `curl` and `ping` too.

Status: version 0.1.0 is the first release. In use on Linux. Windows: tested with Clash Verge Rev, Clash
Party, FlClash, Clash Nyanpasu and a plain mihomo core (named pipe or TCP controller, TUN on or off,
different proxy ports, with or without PyYAML), from mainland China, on English and Chinese Windows. macOS:
the builds pass the automated test against a stand-in mihomo, but haven't been tried with a real client.

## Reporting a problem

Please include: the operating system, the client and its version (e.g. Clash Verge Rev 2.4), how it was
run (the command line, or `--gui`), everything the script printed, and the Markdown report (its "Failure
details" section has mihomo's own error messages). Server addresses in reports can be removed by hand.

Planned features: see [ROADMAP.md](ROADMAP.md).
