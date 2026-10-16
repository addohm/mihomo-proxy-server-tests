"""Windows tester: check that the speed test restores mihomo when it is stopped with Ctrl+C or its console
window is closed, without anyone at the desktop. Run on the Windows PC (e.g. over the tunnel):

    py -3 win_interrupt.py C:\\path\\to\\mihomo_speedtest.py ctrlc|close [--filter REGEX]

It starts the speed test in its own console window, waits until the second server is being tested, then
sends Ctrl+C to that console or closes the window (WM_CLOSE, like clicking X), and compares mihomo's mode,
TUN, port and every selector group's selection before and after. Prints PASS or FAIL.

Processes started from an SSH session inherit "ignore Ctrl+C", so a plain Start-Process never sees the
key; this script clears that flag before starting the test.
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time


def snapshot(mst):
    sys.path.insert(0, os.path.dirname(os.path.abspath(mst)))
    from mihomotest import mihomo
    ctl, _ = mihomo.find_controller()
    _, conf = ctl.call("GET", "/configs")
    _, p = ctl.call("GET", "/proxies")
    groups = {k: v.get("now") for k, v in (p or {}).get("proxies", {}).items() if v.get("all")}
    return json.dumps({"mode": conf.get("mode"), "tun": (conf.get("tun") or {}).get("enable"),
                       "mixed-port": conf.get("mixed-port"), "groups": groups}, sort_keys=True)


def send(pid, what):
    """Runs in its own process: attaches to the test's console (a closed window ends this process too)."""
    k = ctypes.windll.kernel32
    k.FreeConsole()
    if not k.AttachConsole(pid):
        sys.exit(f"could not attach to the console of {pid}")
    if what == "ctrlc":
        k.SetConsoleCtrlHandler(None, True)  # don't stop ourselves
        k.GenerateConsoleCtrlEvent(0, 0)
    else:
        ctypes.windll.user32.PostMessageW(k.GetConsoleWindow(), 0x0010, 0, 0)  # WM_CLOSE


def main():
    if sys.argv[1] == "--send":
        return send(int(sys.argv[2]), sys.argv[3])
    mst, what = os.path.abspath(sys.argv[1]), sys.argv[2]
    extra = sys.argv[3:] or ["--filter", "."]
    before = snapshot(mst)
    out_dir = tempfile.mkdtemp(prefix="mst-interrupt-")
    log = os.path.join(out_dir, "output.txt")
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)  # inherited by the test: normal Ctrl+C
    with open(log, "w") as out:
        p = subprocess.Popen([sys.executable, mst, "--out", out_dir, *extra], cwd=os.path.dirname(mst),
                             stdout=out, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_CONSOLE)
    t0 = time.time()
    while p.poll() is None and time.time() - t0 < 180 and "[2/" not in open(log, errors="replace").read():
        time.sleep(0.5)
    if p.poll() is not None:
        sys.exit(f"FAIL: the test ended before its second server (exit {p.returncode}); test more servers")
    print(f"second server after {time.time() - t0:.0f} s; sending {what}")
    subprocess.run([sys.executable, os.path.abspath(__file__), "--send", str(p.pid), what])
    try:
        p.wait(90)
    except subprocess.TimeoutExpired:
        p.kill()
        print("FAIL: still running 90 s later")
    print(f"ended with exit code {p.returncode:#x}; output from the start of the test:")
    lines = open(log, encoding="utf-8", errors="replace").read().splitlines()
    print("\n".join(l for l in lines if l.strip() and not l.startswith(("  ", "Checking"))))
    after = snapshot(mst)
    print("PASS: restored" if after == before else f"FAIL: not restored\nbefore: {before}\nafter:  {after}")


if __name__ == "__main__":
    main()
