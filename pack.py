#!/usr/bin/env python3
"""Package dataset CSVs into fugazi-web upload archives (ZIP + MANIFEST).

Usage:
    python pack.py [VERSION] [dataset.yml ...]

VERSION defaults to today (YYYYMMDD). If the first positional argument is
all-digits it is treated as VERSION; otherwise it and any further arguments
are treated as dataset paths.

For each dataset <category>/<name>.yml whose CSV exists under data/, this
script produces:

    dist/fugazi-<category>-<name>-<VERSION>.zip

The ZIP contains:
    MANIFEST              — plain text directives: version, name, description,
                            data, overlay (if present)
    <name>.csv            — the raw candle CSV fetched by fugazi get @dataset.yml
    <category>-<name>.yml — overlay definitions (from overlays/<category>-<name>.yml), if present

Run `make fetch` first to populate data/.
"""
import sys
import zipfile
import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: pip install pyyaml")


def _read_meta(yml: Path) -> tuple[str | None, str | None]:
    """Pull the top-level `name` and `description` out of a dataset YAML.

    MANIFEST is line-based, so a folded/literal block description is collapsed
    onto a single line.
    """
    spec = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    name = spec.get("name")
    description = spec.get("description")
    if description:
        description = " ".join(str(description).split())
    return name, description


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
        datasets = sorted(p for p in Path(".").glob("*/*.yml")
                          if p.parent.name != "overlays")
    return version, datasets


def main() -> None:
    version, datasets = _parse_args()
    print(f"packing {len(datasets)} dataset(s) — version {version}")
    ok = 0
    for yml in datasets:
        slug = yml.with_suffix("")          # e.g. crypto/large-cap-1d
        name = slug.name                    # e.g. large-cap-1d
        category = slug.parent.name         # e.g. crypto
        csv = Path("data") / slug.with_suffix(".csv")
        out = Path("dist") / f"{category}-{name}-{version}.zip"

        if not csv.exists():
            print(f"  skip  {slug}: CSV not found (run 'make fetch' first)")
            continue

        overlay_name = f"{category}-{name}"  # e.g. crypto-large-cap-1d
        overlay_yml = Path("overlays") / f"{overlay_name}.yml"
        overlay_file = f"{overlay_name}.yml"

        ds_name, ds_description = _read_meta(yml)

        Path("dist").mkdir(parents=True, exist_ok=True)
        manifest = f"version {version}\n"
        manifest += f"name {ds_name or overlay_name}\n"
        if ds_description:
            manifest += f"description {ds_description}\n"
        manifest += f"data {name}.csv\n"
        if overlay_yml.exists():
            manifest += f"overlay {overlay_file}\n"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("MANIFEST", manifest)
            zf.write(csv, f"{name}.csv")
            if overlay_yml.exists():
                zf.write(overlay_yml, overlay_file)
        suffix = "  [overlay]" if overlay_yml.exists() else ""
        print(f"  pack  {out}{suffix}")
        ok += 1

    skipped = len(datasets) - ok
    if skipped:
        print(f"\n{ok} packed, {skipped} skipped (missing CSVs)")
    else:
        print(f"\n{ok} archive(s) ready in dist/")


if __name__ == "__main__":
    main()
