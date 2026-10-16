@echo off
rem mihomo tester preset for revtunnel. Put this file next to revtunnel-windows.ps1 (from the revtunnel
rem repository) on the Windows tester PC and double-click it. It answers the extras for a tester:
rem keep awake, VPNs off, Python 3.13 with PyYAML; everything else is asked as usual.
if not exist "%~dp0revtunnel-windows.ps1" (
    echo Put revtunnel-windows.ps1 from the revtunnel repository in this folder first: %~dp0
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0revtunnel-windows.ps1" -Name mihomo-tester -KeepAwake -NoVpn -Python 3.13 -PythonPackages pyyaml %*
pause
