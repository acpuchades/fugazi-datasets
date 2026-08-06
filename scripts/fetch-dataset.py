#!/usr/bin/env python3
"""Fetch a dataset CSV, dropping bars the provider has not settled yet.

Usage:
    python3 scripts/fetch-dataset.py <dataset.yml> <out.csv>

Every fetch downloads the dataset whole — there is no incremental path. That
costs bandwidth on unchanged history, but it keeps the CSV a pure function of
the YAML: rotate a symbol in `crypto/*.yml` and the next fetch reflects it
exactly, with no resume point to rebase and no stale rows to evict.

The one thing a raw `fugazi get` still can't be trusted with is the newest bar.
A fetch during an open session returns it in progress, stamped with the wall
clock (`2026-08-06T07:39:57Z`) rather than the session boundary. Every settled
bar lands on a whole minute, so a non-zero seconds field marks one — and left in
the file it reads as a closed session at a price that was never a close.

Duplicate `(symbol, freq, time)` rows inside a single response — Yahoo FX
summer-time bars that landed in two adjacent fetch chunks — were a second such
defect, fixed upstream in fugazi 0.32.1. Requires that version.
"""

import argparse
import csv
import subprocess
import sys
import tempfile
from pathlib import Path

Row = dict[str, str]


def _is_settled(row: Row) -> bool:
    """Reject in-progress bars, which carry a wall-clock timestamp.

    Every settled bar sits on a whole minute — `00:00`/`23:00` for FX, `13:30`/
    `14:30` for US equities — so non-zero seconds means the provider handed back
    a live quote rather than a closed session.
    """
    time = row.get("time", "")
    return len(time) >= 19 and time[17:19] == "00"


def _read_csv(path: Path) -> tuple[list[str], list[Row]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[Row]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def fetch(yml: Path, csv_path: Path) -> int:
    print(f"  fetch  {yml}")
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "fetch.csv"
        cmd = ["fugazi", "get", "--quiet", f"@{yml}", "-o", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            message = (proc.stderr or proc.stdout).strip()
            if message:
                print(message, file=sys.stderr)
            return 1
        fields, rows = _read_csv(out)

    settled = [r for r in rows if _is_settled(r)]
    dropped = len(rows) - len(settled)

    # Only overwrite once the fetch has produced something. An empty response is
    # a provider hiccup, not a dataset that legitimately has no bars, and the
    # existing file is better than a header-only one.
    if not settled:
        print(f"  error  {yml}: fetch returned no settled bars", file=sys.stderr)
        return 1

    _write_csv(csv_path, fields, settled)
    suffix = f" ({dropped} in-progress dropped)" if dropped else ""
    print(f"         {len(settled)} rows{suffix}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("yml", type=Path, help="dataset descriptor")
    parser.add_argument("csv", type=Path, help="CSV to write")
    args = parser.parse_args()

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    sys.exit(fetch(args.yml, args.csv))


if __name__ == "__main__":
    main()
