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

LEGEND = ("Conn ms: time to the first response over a new connection through the server (median of 5). "
          "Ping ms: direct ping to the server's address (10 pings, after all transfers). "
          "± is the jitter: the average change between consecutive measurements. "
          "Speeds count the transfer itself, not the connection setup.")
SPEED_METHOD = "transfer-only"  # bytes / time after the transfer starts (older reports: whole curl time)

HEADERS = ["#", "Server", "Type", "Conn ms", "Ping ms", "Loss %", "Down Mbps", "Up Mbps", "Result"]


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
             f"- Speed method: {SPEED_METHOD} (transfer phase only, without connection setup)",
             f"- Servers: {meta['total']}, ok: {meta['ok']}, partial: {meta['partial']}, "
             f"unreachable: {meta['unreachable']}, failed: {meta['failed']}",
             f"- Runtime: {meta['runtime']}", "",
             "| " + " | ".join(HEADERS + ["Note"]) + " |",
             "|---:|---|---|---:|---:|---:|---:|---:|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append("| " + " | ".join(v.replace("|", "\\|") for v in cells(i, r) + [r["note"]]) + " |")
    lines += ["", f"> {LEGEND}"]
    for note in notes(meta):
        lines += ["", f"> {note}"]
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
                    "other_traffic_mb", "mihomo_error", "speed_method", "trace"])
        num = lambda v, nd: "" if v is None else round(v, nd)
        for r in results:
            w.writerow([r["name"], r["type"], num(r["conn"], 1), num(r["conn_jitter"], 1), num(r["ping"], 1),
                        num(r["ping_jitter"], 1), "" if r["loss"] is None else r["loss"],
                        num(r["down"], 2), num(r["up"], 2), r["result"], r["note"], num(r["first"], 1),
                        r["check"], num(r["other"] and r["other"] / 1e6, 1), r["error"], SPEED_METHOD, r.get("trace", "")])
    return md_path, csv_path
