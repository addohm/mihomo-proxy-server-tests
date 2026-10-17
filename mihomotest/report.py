"""Result formatting and the Markdown + CSV reports."""
import csv
import datetime
import os
import re
import shutil
import tempfile


LEGEND = ("Conn ms: time to the first response over a new connection through the server (median of 5). "
          "Ping ms: direct ping to the server's address (10 pings, after all transfers). "
          "± is the jitter: the average change between consecutive measurements. "
          "Speeds count the transfer itself, not the connection setup.")
SPEED_METHOD = "transfer-only"  # bytes / time after the transfer starts (older reports: whole curl time)

HEADERS = ["#", "Server", "Type", "Conn ms", "Ping ms", "Loss %", "Down Mbps", "Up Mbps", "Result"]
BASELINE = "baseline.csv"
SWING = 0.30  # a change counts as big above 30 %, and only when it's also at least:
SWING_MIN = {"download_mbps": 2, "upload_mbps": 2, "conn_ms": 20, "ping_ms": 20}
WORKING = ("ok", "partial")  # also how older reports' unreachable / failed compare: as not working


def reports_dir():
    """The per-user reports folder: Documents/mihomo-speedtest (on Windows the real Documents folder, also
    when it was moved, e.g. to OneDrive), or ~/mihomo-speedtest where there is no Documents folder."""
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                docs = os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0])
        except OSError:
            pass
    return os.path.join(docs if os.path.isdir(docs) else home, "mihomo-speedtest")


def mmss(sec):
    sec = int(sec)
    return f"{sec // 60}m{sec % 60:02d}s"


def fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def ms(v):
    return "-" if v is None else f"{v:.1f}" if v < 10 else f"{v:.0f}"


def with_jitter(v, j):
    return ms(v) + (f" ±{ms(j)}" if v is not None and j is not None else "")


def cells(i, r):
    """One result as display strings, in HEADERS order."""
    return [str(i), r["name"], r["type"], with_jitter(r["conn"], r["conn_jitter"]),
            with_jitter(r["ping"], r["ping_jitter"]), fmt(r["loss"], 0), fmt(r["down"]), fmt(r["up"]),
            r["result"]]


def notes(n, meanings):
    """One explanation per result label that appears (n: label -> count, meanings: label -> text)."""
    return [f"{label}: {meanings[label]}" for label, k in n.items() if k and meanings.get(label)]


def summary(n, total):
    """'25 ok, 14 blocked, 2 no answer of 43 servers' (ok always first, other labels only when present)."""
    parts = [f"{n.get('ok', 0)} ok"] + [f"{k} {label}" for label, k in n.items() if k and label != "ok"]
    return f"{', '.join(parts)} of {total} servers"


def load_report(path):
    """A saved CSV report: (date, {server name: row}). The date comes from the report's own date column,
    else its file name (mihomo-speedtest-YYYYMMDD-HHMMSS.csv), else the file's time."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = {r["server"]: r for r in csv.DictReader(f) if r.get("server")}
    date = next((r["date"] for r in rows.values() if r.get("date")), "")
    m = re.search(r"(\d{8})-(\d{6})", os.path.basename(path))
    if not date and m:
        date = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    if not date:
        date = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    return date, rows


def compare(results, base, names_now):
    """What changed since a saved report (base: {name: row}). names_now: every server mihomo has now, so a
    server left out by --filter isn't reported as gone. Returns {stopped, recovered, swings, new, gone,
    notes}: lists of text lines."""
    out = {k: [] for k in ("stopped", "recovered", "swings", "new", "gone", "notes")}
    num = lambda v: float(v) if v not in (None, "") else None
    same_method = all(b.get("speed_method") == SPEED_METHOD for b in base.values())
    if not same_method:
        out["notes"].append("The saved report measured speeds another way (an older version), so speed changes "
                            "aren't compared.")
    for r in results:
        b = base.get(r["name"])
        if b is None:
            out["new"].append(r["name"])
            continue
        was, now = b.get("result", ""), r["result"]
        if was in WORKING and now not in WORKING:
            cause = r["note"].split(";")[0]
            out["stopped"].append(f"{r['name']}: {was} → {now}" + (f" ({cause})" if cause else ""))
        elif was not in WORKING and now in WORKING:
            out["recovered"].append(f"{r['name']}: {was} → {now}")
        if was not in WORKING or now not in WORKING:
            continue
        for key, label, cur, unit in (("download_mbps", "download", r["down"], "Mbps"), ("upload_mbps", "upload", r["up"], "Mbps"),
                                      ("conn_ms", "Conn", r["conn"], "ms"), ("ping_ms", "ping", r["ping"], "ms")):
            old = num(b.get(key))
            if old is None or cur is None or (unit == "Mbps" and not same_method):
                continue
            if abs(cur - old) >= SWING_MIN[key] and abs(cur - old) >= SWING * old:
                out["swings"].append(f"{r['name']}: {label} {old:.0f} → {cur:.0f} {unit} ({(cur - old) / old:+.0%})"
                                     if old else f"{r['name']}: {label} {old:.0f} → {cur:.0f} {unit}")
    tested = {r["name"] for r in results}
    out["gone"] = [n for n in base if n not in tested and n not in names_now]
    return out


def comparison_lines(cmp, base_date, base_path, now_date):
    """The comparison as text lines (terminal and Markdown)."""
    lines = [f"This run ({now_date}) compared with {os.path.basename(base_path)} from {base_date} (a report from "
             "another network or time of day isn't a fair comparison):"]
    for key, title in (("stopped", "Stopped working"), ("recovered", "Working again"),
                       ("swings", f"Big changes (more than {SWING:.0%})"), ("new", "Not in the saved report (new, or not tested then)"),
                       ("gone", "Gone from the config")):
        if cmp[key]:
            lines += [f"{title}:"] + [f"  - {x}" for x in cmp[key]]
    if not any(cmp[k] for k in ("stopped", "recovered", "swings", "new", "gone")):
        lines.append("No big changes.")
    return lines + cmp["notes"]


def finish(results, meta, out_dir, base=None, names_now=(), save_baseline=False):
    """After a run: compare with a saved report (base: (path, date, rows) from load_report), save the
    Markdown + CSV reports and, if asked, the baseline. Returns {md, csv, comparison (lines or None),
    baseline (path or None), tip (bool: no baseline exists yet)}."""
    comparison = None
    if base:
        comparison = comparison_lines(compare(results, base[2], set(names_now)), base[1], base[0], meta["date"])
    md, cs = save_reports(results, meta, out_dir, comparison)
    baseline = None
    if save_baseline:
        baseline = os.path.join(os.path.dirname(cs), BASELINE)
        shutil.copyfile(cs, baseline)
    return {"md": md, "csv": cs, "comparison": comparison, "baseline": baseline,
            "tip": not save_baseline and not os.path.isfile(os.path.join(out_dir, BASELINE))}


def save_reports(results, meta, out_dir, comparison=None):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        pass
    for d in (out_dir, os.getcwd(), tempfile.gettempdir()):
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            out_dir = d
            break
    md_path = os.path.join(out_dir, f"mihomo-speedtest-{stamp}.md")
    csv_path = os.path.join(out_dir, f"mihomo-speedtest-{stamp}.csv")
    lines = ["# mihomo server test", "",
             f"- Date: {meta['date']}", f"- mihomo: {meta['version']} ({meta['api']})",
             f"- Download size: {meta['size']:g} MB, upload {meta['size'] / 2:g} MB",
             f"- Speed method: {SPEED_METHOD} (transfer phase only, without connection setup)",
             f"- Servers: {summary(meta['counts'], meta['total'])}",
             f"- Runtime: {meta['runtime']}", "",
             "| " + " | ".join(HEADERS + ["Note"]) + " |",
             "|---:|---|---|---:|---:|---:|---:|---:|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append("| " + " | ".join(v.replace("|", "\\|") for v in cells(i, r) + [r["note"]]) + " |")
    lines += ["", f"> {LEGEND}"]
    for note in notes(meta["counts"], meta["meanings"]):
        lines += ["", f"> **{note.split(':')[0]}**:{note.split(':', 1)[1]}"]
    if comparison:
        lines += ["", "## Changes since the saved report", "", comparison[0], ""]
        lines += [("- " + x[4:] if x.startswith("  - ") else f"**{x}**" if x.endswith(":") else x) for x in comparison[1:]]
    traced = [r for r in results if r.get("trace_hops")]
    if traced:
        lines += ["", "## Traces", "", "Hop by hop (`*` = no answer), with each hop's network owner:", ""]
        lines += [f"- **{r['name']}**: {r['trace']}  \n  {r['trace_hops']}" for r in traced]
    errors = [r for r in results if r.get("error")]
    if errors:
        lines += ["", "## Failure details", "", "mihomo's own error message for each server that didn't work:", ""]
        lines += [f"- **{r['name']}**: `{r['error'][:300]}`" for r in errors]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel opens UTF-8 names
        w = csv.writer(f)
        w.writerow(["server", "type", "conn_ms", "conn_jitter_ms", "ping_ms", "ping_jitter_ms", "loss_pct",
                    "download_mbps", "upload_mbps", "result", "note", "first_response_s", "direct_check",
                    "other_traffic_mb", "mihomo_error", "speed_method", "trace", "date"])
        num = lambda v, nd: "" if v is None else round(v, nd)
        for r in results:
            w.writerow([r["name"], r["type"], num(r["conn"], 1), num(r["conn_jitter"], 1), num(r["ping"], 1),
                        num(r["ping_jitter"], 1), "" if r["loss"] is None else r["loss"],
                        num(r["down"], 2), num(r["up"], 2), r["result"], r["note"], num(r["first"], 1),
                        r["check"], num(r["other"] and r["other"] / 1e6, 1), r["error"], SPEED_METHOD, r.get("trace", ""), meta["date"]])
    return md_path, csv_path
