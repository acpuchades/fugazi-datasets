#!/usr/bin/env python3
"""Idempotently bring a dataset CSV up to date, or repair one in place.

Usage:
    python3 scripts/merge-incremental.py <dataset.yml> <out.csv>
    python3 scripts/merge-incremental.py --repair <out.csv> [<out.csv> ...]

Why this exists instead of a plain `>>` append: upstream providers are not
append-only, so treating the CSV as a log corrupts it in three separate ways.

1. DST restamping. Yahoo stamps a daily bar for trading day D at `D-1T23:00Z`
   under European summer time but `DT00:00Z` under winter time. Deriving the
   next start date by truncating the newest timestamp to a date and adding a
   day therefore re-requests the bar that is already stored, and appending the
   response duplicates it.

2. In-progress bars. A fetch during an open session returns a partial bar
   stamped with the wall clock (`2026-08-06T07:39:57Z`) rather than the session
   boundary. Every complete bar lands on a whole minute, so a non-zero seconds
   field is a reliable marker for one.

3. Silent gaps — the damaging one. A partial bar's wall-clock stamp becomes the
   newest timestamp in the file, pushing the start date past the session that
   bar belonged to. That session's real bar is stamped *earlier* (see 1) and so
   is never requested again.

The fix is to stop deriving an exact resume point and make the update
idempotent instead: re-fetch a `LOOKBACK_DAYS` overlap window, drop in-progress
bars, and merge on `(symbol, freq, time)` with the fresh copy winning. That
absorbs duplicates, picks up upstream revisions to recent bars, and backfills
any session an earlier run skipped.

`--repair` runs the same cleanup and re-sort against existing files without
fetching, to retire damage left by the previous append-only implementation.
"""

import argparse
import csv
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: pip install pyyaml")

# How far back to re-fetch. Needs to cover a long weekend plus a DST shift; the
# cost is a few extra bars per symbol per run.
LOOKBACK_DAYS = 7

Row = dict[str, str]
Key = tuple[str, str, str]


def _key(row: Row) -> Key:
    return (row["symbol"], row["freq"], row["time"])


def _is_complete(row: Row) -> bool:
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
        return list(reader.fieldnames or []), [r for r in reader]


def _write_csv(path: Path, fields: list[str], rows: list[Row]) -> None:
    """Write sorted by (time, symbol) — the order fugazi itself emits.

    Incremental appends leave the file non-monotonic over time, so the merged
    result is always re-sorted rather than assumed ordered.
    """
    rows = sorted(rows, key=lambda r: (r["time"], r["symbol"]))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    tmp.replace(path)


def _clean(rows: list[Row]) -> tuple[dict[Key, Row], int, int]:
    """Drop in-progress bars and collapse duplicate keys (last occurrence wins)."""
    partial = sum(1 for r in rows if not _is_complete(r))
    merged: dict[Key, Row] = {}
    duplicate = 0
    for row in rows:
        if not _is_complete(row):
            continue
        if _key(row) in merged:
            duplicate += 1
        merged[_key(row)] = row
    return merged, partial, duplicate


def _emitted_symbol(spec: str) -> str:
    """Resolve a dataset `symbols:` entry to the symbol written into the CSV.

    Entries may carry an `EMITTED=FETCHED` remap, and a ticker containing `=`
    escapes it as `\\=` (Yahoo's `EURUSD\\=X`). Only the emitted side ends up in
    the data, so split on the first *unescaped* `=` and unescape what remains.
    """
    out, escaped = [], False
    for ch in spec:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "=":
            break  # start of the remap; the emitted side is what precedes it
        else:
            out.append(ch)
    return "".join(out).upper()


def _declared_symbols(spec: dict) -> set[str]:
    symbols: set[str] = set()
    for source in spec.get("sources") or []:
        if not isinstance(source, dict):
            continue
        for value in source.values():
            if isinstance(value, dict):
                for sym in value.get("symbols") or []:
                    symbols.add(_emitted_symbol(str(sym)))
    return symbols


def _fetch(yml: Path, since: str | None) -> tuple[list[str], list[Row]] | None:
    """Run `fugazi get` into a temp file. Returns None if the fetch failed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "fetch.csv"
        cmd = ["fugazi", "get", "--quiet", f"@{yml}", "-o", str(out)]
        if since:
            cmd += ["--since", since]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            message = (proc.stderr or proc.stdout).strip()
            if message:
                print(message, file=sys.stderr)
            return None
        return _read_csv(out)


def update(yml: Path, csv_path: Path) -> int:
    spec = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}

    existing_fields: list[str] = []
    existing: dict[Key, Row] = {}
    partial = duplicate = 0
    if csv_path.exists():
        existing_fields, rows = _read_csv(csv_path)
        existing, partial, duplicate = _clean(rows)

    # A symbol added to the YAML since the last fetch has no history in the CSV,
    # and the resume point is driven by the symbols that do — so it would only
    # ever be filled forward from today. Re-fetch the dataset whole instead.
    declared = _declared_symbols(spec)
    present = {k[0].upper() for k in existing}
    new_symbols = declared - present

    if not existing:
        since = None
        print(f"  fetch  {yml}")
    elif new_symbols:
        since = None
        print(f"  fetch  {yml} (full: new symbols {', '.join(sorted(new_symbols))})")
    else:
        newest = max(k[2] for k in existing)
        since = (date.fromisoformat(newest[:10]) - timedelta(days=LOOKBACK_DAYS)).isoformat()
        print(f"  update {yml} (since {since})")

    fetched = _fetch(yml, since)
    if fetched is None:
        if not existing:
            return 1  # nothing on disk and nothing fetched: let make fail
        # Keep the cleanup below; a transient fetch failure shouldn't also cost
        # the repair, and the next run will pick the bars up via the overlap.
        print(f"  warn   {yml}: fetch failed, keeping existing data", file=sys.stderr)
        fields, fresh = existing_fields, {}
    else:
        fields, fresh_rows = fetched
        fresh, fresh_partial, fresh_duplicate = _clean(fresh_rows)
        partial += fresh_partial
        duplicate += fresh_duplicate

    if since is None and fresh:
        merged = fresh  # full re-fetch replaces rather than merges
    else:
        merged = dict(existing)
        merged.update(fresh)  # freshly fetched bars win on conflict

    # Union the columns so a provider adding one doesn't drop the older rows'.
    for column in existing_fields:
        if column not in fields:
            fields.append(column)

    _write_csv(csv_path, fields, list(merged.values()))

    notes = []
    if duplicate:
        notes.append(f"{duplicate} duplicate")
    if partial:
        notes.append(f"{partial} in-progress")
    suffix = f" ({', '.join(notes)} dropped)" if notes else ""
    print(f"         {len(merged)} rows{suffix}")
    return 0


def repair(csv_path: Path) -> int:
    if not csv_path.exists():
        print(f"  skip   {csv_path}: not found", file=sys.stderr)
        return 0
    fields, rows = _read_csv(csv_path)
    merged, partial, duplicate = _clean(rows)
    if not duplicate and not partial:
        print(f"  ok     {csv_path} ({len(merged)} rows)")
        return 0
    _write_csv(csv_path, fields, list(merged.values()))
    notes = []
    if duplicate:
        notes.append(f"{duplicate} duplicate")
    if partial:
        notes.append(f"{partial} in-progress")
    print(f"  repair {csv_path}: dropped {', '.join(notes)} ({len(merged)} rows)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true",
                        help="clean existing CSVs in place without fetching")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    if args.repair:
        sys.exit(max(repair(p) for p in args.paths))

    if len(args.paths) != 2:
        parser.error("expected <dataset.yml> <out.csv>")
    yml, csv_path = args.paths
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    sys.exit(update(yml, csv_path))


if __name__ == "__main__":
    main()
