# mihomo-proxy-server-tests

`mihomo_speedtest.py` tests every server in your **running** mihomo, exactly as mihomo uses it:
download/upload speed through mihomo and ping to the address it actually dialed. For servers that
don't work it shows why: mihomo's own error (e.g. "connection timed out", "config error") plus a direct
check of the server's port (blocked, refused, or reachable). It also flags servers that are slow to start.
Works with any
mihomo-based client (Clash Verge Rev, Mihomo Party, Nyanpasu, FlClash, ClashX Meta, plain mihomo).

```bash
python3 mihomo_speedtest.py                      # test everything
python3 mihomo_speedtest.py --filter "Japan"     # only matching servers
```

While it runs, TUN is turned off and mihomo is put in global mode; your settings are restored at the end
(also on Ctrl+C). Results are printed and saved next to the script as
`mihomo-speedtest-YYYYMMDD-HHMMSS.md` and `.csv`.

Requirements: Python 3.8+, `curl` and `ping`. PyYAML is optional. Run with `--help` for all options.

Keep `mihomo_speedtest.py` and the `mihomotest/` folder together; the script runs the code in that folder.

Status: in use on Linux. Windows and macOS support is written but not yet tested.

Planned features: see [ROADMAP.md](ROADMAP.md).
