# Test tooling

Tools for testing `mihomo_speedtest.py` itself on machines other than your own: a Linux test server, and
Windows tester PCs reached through a reverse SSH tunnel (set up with
[revtunnel](https://github.com/addohm/revtunnel)). None of this is needed to *use* the speed test.

| File | Runs on | What it does |
|---|---|---|
| `setup.sh` | Fedora VPS, as root | Installs mihomo as a service from a subscription URL: the Linux test server |
| `variant.py` | test server | Switches mihomo into one config variant (controller, secret, port, TUN, source) |
| `matrix.py` | test server | Runs the speed test on every variant and checks it found, tested and restored |
| `tester-windows.cmd` | Windows tester PC | revtunnel preset for a tester PC (double-click) |
| `win_matrix.py` | Windows tester PC | Config variants of the PC's mihomo client, loaded through its API: found, tested, restored |
| `win_interrupt.py` | Windows tester PC | Stops a speed test with Ctrl+C or by closing its window, and checks mihomo was restored |

The scripts' headers have every option; this page is the workflow.

## Linux test server

```bash
scp testserver/setup.sh root@HOST:/root/
ssh root@HOST "SUB_URL='https://...' bash /root/setup.sh"     # TUN=1: TUN on at rest
```

mihomo then runs as `mihomo.service` with its config in `/etc/mihomo/`, and everything listens on
127.0.0.1 only. Re-run with a new `SUB_URL` to change the subscription. Never commit the subscription URL
or anything from `/etc/mihomo/`: they contain credentials.

**Run the speed test there:**

```bash
scp -r mihomo_speedtest.py mihomotest root@HOST:/root/mst/
ssh root@HOST "python3 /root/mst/mihomo_speedtest.py --filter 'Japan'"
```

**Config matrix:** run it before every release and after changes to discovery, selection, state or
restore. It toggles TUN, so start it detached and wait for "variants passed" in the log:

```bash
scp testserver/variant.py testserver/matrix.py root@HOST:/root/testserver/
ssh root@HOST 'SSH_IP=${SSH_CLIENT%% *} setsid nohup python3 /root/testserver/matrix.py \
    /root/mst/mihomo_speedtest.py > /root/matrix.log 2>&1 < /dev/null &'
ssh root@HOST 'tail -n 20 /root/matrix.log'
```

`python3 /root/testserver/variant.py` on its own puts the default config back.

## Windows tester PC

A Windows PC (e.g. in mainland China, behind a dynamic IP) that we reach over SSH even though nothing can
connect to it: it keeps a reverse SSH tunnel to the test server. The tunnel tooling is its own project,
**[revtunnel](https://github.com/addohm/revtunnel)**; its README has the full, step-by-step workflow and
troubleshooting. For a tester PC:

1. **On your computer:** download `revtunnel-windows.ps1` from the revtunnel repository and
   `testserver/tester-windows.cmd` from this one (GitHub: open the file, then **Raw**), into the same
   folder on the PC. Download `revtunnel-server.sh` too, for the server.
2. **On the PC:** turn TUN off in the mihomo client first, then double-click `tester-windows.cmd`. It is
   revtunnel's setup with the tester's extras already chosen (tunnel name `mihomo-tester`, keep awake,
   VPNs off, Python 3.13 with PyYAML). Answer the remaining questions: Enter for port 22; paste the login
   keys (e.g. Claude's, `~/.ssh/mihomo-winvm.pub` on the bot's host); the test server's address; its own
   port on the server (2222 for the first PC, 2223 for the next, ...).
3. **On the test server:** when the PC shows **ACTION NEEDED - on the SERVER**, run the line it shows,
   with `revtunnel-server.sh` copied there (or paste the PC's key here and Claude runs it).
4. **On the PC:** press Enter. Setup ends with **DONE**.

Then, from the test server: `ssh -p PORT WINDOWS_USER@127.0.0.1`. Re-run `tester-windows.cmd` any time
for the status, a new server address, more login keys or removal. `sudo bash revtunnel-server.sh --list`
on the server shows every tester PC and whether it's connected.

**Testing on the PC** goes through the tunnel; nobody needs to be at the PC:

```bash
scp -r mihomo_speedtest.py mihomotest testserver/win_interrupt.py mihomo-winvm:C:/Users/USER/mst/
ssh mihomo-winvm 'py -3 C:\Users\USER\mst\mihomo_speedtest.py --filter "Hong Kong"'
ssh mihomo-winvm 'py -3 C:\Users\USER\mst\win_interrupt.py C:\Users\USER\mst\mihomo_speedtest.py ctrlc'   # or: close
```

`win_matrix.py` is the Windows matrix: it loads variants of the client's config (secret, socks/http port,
no port, provider) through mihomo's API, tests two servers in each, checks the restore, and loads the
client's own config back at the end. It needs PyYAML (`py -V:3.13` on the tester PC); `--python` picks
the Python that runs the speed test:

```bash
ssh mihomo-winvm 'py -V:3.13 C:\Users\USER\mst\win_matrix.py C:\Users\USER\mst\mihomo_speedtest.py'
```

`win_interrupt.py` starts the speed test in its own console, stops it at the second server (Ctrl+C, or
closing the window like clicking X) and prints PASS if mode, TUN, port and every group's selection are
back as before.

For unattended runs, also set Windows to sign in automatically and the mihomo client to start at login
(the tunnel starts at boot; the client only when someone signs in). Start long test runs detached and read
their log afterwards, as with the matrix.
