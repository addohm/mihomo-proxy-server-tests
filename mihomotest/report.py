"""Result formatting and the Markdown + CSV reports."""
import csv
import datetime
import os
import tempfile

UNREACHABLE_NOTE = ("Note: 'unreachable' does NOT mean the server is down. It answers ping, but mihomo could "
                    "not get a working proxy connection through it from this network. Common causes: the port or "
                    "protocol is blocked on your connection, or the server refused the connection (expired "
                    "subscription, wrong password, changed server settings).")
FAILED_NOTE = ("Note: 'failed' servers did not answer ping either (or their address could not be found). "
               "They may be down, or they may simply block ping.")

HEADERS = ["#", "Server", "Type", "Ping ms", "Loss %", "Down Mbps", "Up Mbps", "Result"]


def mmss(sec):
    sec = int(sec)
    return f"{sec // 60}m{sec % 60:02d}s"


def fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def cells(i, r):
    """One result as display strings, in HEADERS order."""
    return [str(i), r["name"], r["type"], fmt(r["ping"]), fmt(r["loss"], 0), fmt(r["down"]), fmt(r["up"]),
            r["result"]]


def notes(n):
    """Explanatory notes that apply, given result counts."""
    return ([UNREACHABLE_NOTE] if n["unreachable"] else []) + ([FAILED_NOTE] if n["failed"] else [])


def save_reports(results, meta, out_dir):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for d in (out_dir, os.getcwd(), tempfile.gettempdir()):
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            out_dir = d
            break
    md_path = os.path.join(out_dir, f"mihomo-speedtest-{stamp}.md")
    csv_path = os.path.join(out_dir, f"mihomo-speedtest-{stamp}.csv")
    lines = ["# mihomo server test", "",
             f"- Date: {meta['date']}", f"- mihomo: {meta['version']} ({meta['api']})",
             f"- Download size: {meta['size']:g} MB, upload {meta['size'] / 2:g} MB",
             f"- Servers: {meta['total']}, ok: {meta['ok']}, partial: {meta['partial']}, "
             f"unreachable: {meta['unreachable']}, failed: {meta['failed']}",
             f"- Runtime: {meta['runtime']}", "",
             "| " + " | ".join(HEADERS + ["Note"]) + " |",
             "|---:|---|---|---:|---:|---:|---:|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append("| " + " | ".join(v.replace("|", "\\|") for v in cells(i, r) + [r["note"]]) + " |")
    for note in notes(meta):
        lines += ["", f"> {note}"]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel opens UTF-8 names
        w = csv.writer(f)
        w.writerow(["server", "type", "ping_ms", "loss_pct", "download_mbps", "upload_mbps", "result", "note"])
        for r in results:
            w.writerow([r["name"], r["type"], "" if r["ping"] is None else round(r["ping"], 1),
                        "" if r["loss"] is None else r["loss"],
                        "" if r["down"] is None else round(r["down"], 2),
                        "" if r["up"] is None else round(r["up"], 2), r["result"], r["note"]])
    return md_path, csv_path
