# mihomo-proxy-server-tests

`mihomo_speedtest.py` tests every server in your **running** mihomo, exactly as mihomo uses it:
download/upload speed through mihomo and ping to the address it actually dialed. For servers that don't
work it shows why: mihomo's own error (e.g. "connection timed out", "config error") plus a direct check of
the server's port (blocked, refused, or reachable). It also flags servers that are slow to start. Works with
any mihomo-based client (Clash Verge Rev, Mihomo Party, Nyanpasu, FlClash, ClashX Meta, plain mihomo), with
servers listed in the config or loaded from proxy providers.

```bash
python3 mihomo_speedtest.py                      # test everything
python3 mihomo_speedtest.py --filter "Japan"     # only matching servers
```

While it runs, TUN is turned off and mihomo is put in global mode; your settings are restored at the end
(also on Ctrl+C). Results are printed and saved next to the script as
`mihomo-speedtest-YYYYMMDD-HHMMSS.md` and `.csv`.

## Reading the results

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

"slow start: first response after N s" means the server works but takes a while to answer its first
connection. The Markdown report also lists mihomo's exact error message for each server that didn't work.

## Requirements

Python 3.8+, `curl` and `ping`. PyYAML is optional. Run with `--help` for all options.

Keep `mihomo_speedtest.py` and the `mihomotest/` folder together; the script runs the code in that folder.

Status: in use on Linux. Windows and macOS support is written but not yet tested.

Planned features: see [ROADMAP.md](ROADMAP.md).
