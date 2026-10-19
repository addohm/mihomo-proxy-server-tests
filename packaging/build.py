#!/usr/bin/env python3
"""Build the packaged program for the current OS with PyInstaller (step 10 of the roadmap).

    python packaging/build.py            -> dist/release/<file>, plus the path to run printed last

Needs: pip install -r packaging/requirements.txt (PyInstaller, and PyYAML so the program can read controller
secrets from client configs). GitHub Actions runs this on each OS (.github/workflows/build.yml); it works the
same on any machine of that OS.

Output, named mihomo-speedtest-VERSION-OS-ARCH:
  Windows  a single console .exe (double-click: the GUI, with a window that says how to quit)
  Linux    a single executable file (glibc of the build machine or newer)
  macOS    a .app in a .zip, ad-hoc signed (free; Apple Silicon needs a signature to run at all); no Dock
           icon (LSUIElement), the page is the window; terminal use: the binary in Contents/MacOS
Started without arguments the program opens the GUI; any argument (e.g. --cli) means the terminal.
"""
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "mihomo-speedtest"


def version():
    ns = {}
    with open(os.path.join(ROOT, "mihomotest", "__init__.py"), encoding="utf-8") as f:
        exec(f.read(), ns)  # noqa: S102 - our own file: only a docstring and __version__
    return ns["__version__"]


def run(*cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def main():
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(),
                                                                                      platform.machine().lower())
    base = f"{NAME}-{version()}-{system}-{arch}"
    for d in ("build", "dist"):
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", NAME,
           "--add-data", f"mihomotest{os.sep}gui.html{os.pathsep}mihomotest", "--hidden-import", "yaml"]
    if system == "macos":
        cmd += ["--windowed", "--osx-bundle-identifier", "io.github.addohm.mihomo-speedtest"]
    else:
        cmd += ["--onefile", "--console"]
    run(*cmd, "mihomo_speedtest.py")

    out = os.path.join(ROOT, "dist", "release")
    os.makedirs(out)
    if system == "macos":
        app = os.path.join(ROOT, "dist", f"{NAME}.app")
        plist = os.path.join(app, "Contents", "Info.plist")
        run("plutil", "-replace", "LSUIElement", "-bool", "YES", plist)  # no Dock icon: the browser page is the UI
        run("codesign", "--force", "--deep", "--sign", "-", app)  # re-sign ad-hoc after editing Info.plist
        run("ditto", "-c", "-k", "--keepParent", app, os.path.join(out, base + ".zip"))
        binary = os.path.join(app, "Contents", "MacOS", NAME)
    else:
        ext = ".exe" if system == "windows" else ""
        binary = os.path.join(out, base + ext)
        shutil.copy2(os.path.join(ROOT, "dist", NAME + ext), binary)
    print(binary)


if __name__ == "__main__":
    main()
