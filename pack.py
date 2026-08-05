#!/usr/bin/env python3
"""Package dataset CSVs into fugazi-web upload archives (ZIP + MANIFEST).

Usage:
    python pack.py [VERSION] [dataset.yml ...]

VERSION defaults to today (YYYYMMDD). If the first positional argument is
all-digits it is treated as VERSION; otherwise it and any further arguments
are treated as dataset paths.

For each dataset <category>/<name>.yml whose CSV exists under data/, this
script produces:

    dist/<category>/<name>-<VERSION>.zip

The ZIP contains:
    MANIFEST        — plain text: "version <VERSION>\\ndata <name>.csv"
    <name>.csv      — the raw candle CSV fetched by fugazi get @dataset.yml

Run `make fetch` first to populate data/.
"""
import sys
import zipfile
import datetime
from pathlib import Path


def _parse_args() -> tuple[str, list[Path]]:
    args = sys.argv[1:]
    version = datetime.date.today().strftime("%Y%m%d")
    paths: list[str] = []
    for i, a in enumerate(args):
        if i == 0 and a.isdigit():
            version = a
        else:
            paths.append(a)
    if paths:
        datasets = [Path(p) for p in paths]
    else:
        datasets = sorted(Path(".").glob("*/*.yml"))
    return version, datasets


def main() -> None:
    version, datasets = _parse_args()
    print(f"packing {len(datasets)} dataset(s) — version {version}")
    ok = 0
    for yml in datasets:
        slug = yml.with_suffix("")          # e.g. crypto/large-cap-1d
        name = slug.name                    # e.g. large-cap-1d
        csv = Path("data") / slug.with_suffix(".csv")
        out = Path("dist") / slug.parent / f"{name}-{version}.zip"

        if not csv.exists():
            print(f"  skip  {slug}: CSV not found (run 'make fetch' first)")
            continue

        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = f"version {version}\ndata {name}.csv\n"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("MANIFEST", manifest)
            zf.write(csv, f"{name}.csv")
        print(f"  pack  {out}")
        ok += 1

    skipped = len(datasets) - ok
    if skipped:
        print(f"\n{ok} packed, {skipped} skipped (missing CSVs)")
    else:
        print(f"\n{ok} archive(s) ready in dist/")


if __name__ == "__main__":
    main()
