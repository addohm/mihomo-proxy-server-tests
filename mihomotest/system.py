"""Platform helpers: OS flags, running commands, prerequisites."""
import os
import shutil
import subprocess
import sys

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
NULL_DEV = "NUL" if IS_WIN else "/dev/null"
CURL = "curl.exe" if IS_WIN else "curl"


def ps_exe():
    return shutil.which("pwsh") or shutil.which("powershell")


def run(args, timeout=60):
    """Run a command directly (no shell). Returns (exit_code, stdout, stderr)."""
    try:
        p = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout,
                           errors="replace")
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 127, "", str(e)


def prereqs():
    """[(name, state, detail, hint)]; state True = ok, None = optional and missing, False = required and missing."""
    rows = [("Python 3.8+", sys.version_info >= (3, 8), sys.version.split()[0], "Install Python 3.8 or newer")]
    if IS_WIN:
        rows += [("curl.exe", bool(shutil.which(CURL)), shutil.which(CURL) or "not found",
                  "Included in Windows 10+; or: winget install cURL.cURL"),
                 ("PowerShell", True if ps_exe() else None,
                  ps_exe() or "not found (optional: helps find the running mihomo)", "")]
    else:
        rows += [("curl", bool(shutil.which(CURL)), shutil.which(CURL) or "not found",
                  "sudo apt install curl | sudo dnf install curl | brew install curl")]
    rows.append(("ping", bool(shutil.which("ping")), shutil.which("ping") or "not found",
                 "sudo apt install iputils-ping | sudo dnf install iputils"))
    try:
        import yaml
        rows.append(("PyYAML", True, yaml.__version__, ""))
    except ImportError:
        rows.append(("PyYAML", None, "not installed (optional: helps find the controller secret and "
                     "ping servers that never connect)", f"{sys.executable} -m pip install pyyaml"))
    return rows
