#!/usr/bin/env python3
"""
CDR Report — Asterisk ke Master.csv se simple report banata hai:
kis number ki call uthi (ANSWERED), kis ki nahi (NO ANSWER / BUSY / FAILED),
aur kitni der chali. Result screen par + report.csv me.

Chalana:  python3 engine/report.py [--cdr PATH] [--out report.csv]
CDR path default: env SVD_CDR_PATH, warna /var/log/asterisk/cdr-csv/Master.csv
"""

import argparse
import csv
import os
from collections import Counter

DEFAULT_CDR = "/var/log/asterisk/cdr-csv/Master.csv"

# Master.csv ke fixed columns (index):
# 2=dst(number) 5=channel 9=start time 13=billsec(sec) 14=disposition
DST, CHANNEL, START, BILLSEC, DISP = 2, 5, 9, 13, 14


def get_cdr_path() -> str:
    return os.environ.get("SVD_CDR_PATH", DEFAULT_CDR)


def load_rows(cdr_path: str) -> list[list[str]]:
    """Master.csv se sirf Dongle channel wali rows lao."""
    if not os.path.exists(cdr_path):
        return []
    rows = []
    with open(cdr_path, newline="") as f:
        for row in csv.reader(f):
            if len(row) > DISP and "Dongle" in row[CHANNEL]:
                rows.append(row)
    return rows


def summarise(rows: list[list[str]]) -> tuple[dict[str, int], dict[str, tuple[str, str, str]]]:
    """Returns (disposition counts, {number: (last_time, disposition, billsec)})."""
    counts = dict(Counter(r[DISP] for r in rows))
    latest: dict[str, tuple[str, str, str]] = {}
    for r in rows:
        latest[r[DST]] = (r[START], r[DISP], r[BILLSEC])
    return counts, latest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Master.csv se dialer report banao")
    ap.add_argument("--cdr", default=None, help="Master.csv ka path (default: SVD_CDR_PATH ya system path)")
    ap.add_argument("--out", default="report.csv", help="Number-wise report kahan likhni hai")
    args = ap.parse_args(argv)

    cdr_path = args.cdr or get_cdr_path()
    if not os.path.exists(cdr_path):
        print(f"CDR file nahi mili: {cdr_path}")
        print("Asterisk me CDR on hai? configs/cdr.conf.snippet dekho — [csv] enabled=yes hona chahiye.")
        return 1

    rows = load_rows(cdr_path)
    if not rows:
        print("Dongle se abhi tak koi call record nahi hai.")
        return 0

    counts, latest = summarise(rows)
    print(f"Kul calls: {len(rows)}")
    for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status}: {n}")

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["number", "last_call_time", "result", "talk_seconds"])
        for number in sorted(latest):
            start, disp, bill = latest[number]
            w.writerow([number, start, disp, bill])

    print(f"\nNumber-wise report likh di: {args.out} ({len(latest)} numbers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
