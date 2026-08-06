#!/usr/bin/env python3
"""Fetch CoinGecko daily overlay data (market_cap, price, volume, circulating_supply)
for crypto dataset YAMLs that declare a `coingecko_ids` mapping.

Usage:
    python3 scripts/fetch-cg-overlays.py [dataset.yml ...]

If no paths are given, processes all crypto/*.yml files. Writes
data/<category>/<name>-cg.csv for each matching dataset.

The `coingecko_ids` field in a dataset YAML maps Binance pair → CoinGecko coin id:

    coingecko_ids:
      BTCUSDT: bitcoin
      ETHUSDT: ethereum

CoinGecko data is always fetched at daily granularity (the API only provides
daily bars beyond ~90 days), regardless of the dataset's own interval. The
resulting CSV can be joined to any bar frequency using the date column.
"""
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: pip install pyyaml")

DEFAULT_SINCE = "2020-01-01"
RATE_LIMIT_SLEEP = 2.0  # seconds between datasets (CoinGecko free tier: ~10-30 req/min)

# CoinGecko's public tier serves the last 365 days and answers anything older
# with `http 401` (error_code 10012) — for the whole request, so one bar out of
# range costs the entire dataset. Clamp instead of failing: the overlay is a
# side channel joined onto candle CSVs that carry the full history themselves.
# One day of slack, since the limit is enforced against the server's clock.
PUBLIC_TIER_DAYS = 364


def fetch_dataset(yml_path: Path) -> bool:
    with yml_path.open() as f:
        spec = yaml.safe_load(f)

    ids: dict[str, str] = spec.get("coingecko_ids", {})
    if not ids:
        return False

    slug = yml_path.with_suffix("")        # e.g. crypto/large-cap-1d
    out_csv = Path("data") / f"{slug}-cg.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    declared: str = spec.get("since", DEFAULT_SINCE)
    floor = (date.today() - timedelta(days=PUBLIC_TIER_DAYS)).isoformat()
    since = max(declared, floor)  # ISO dates order lexicographically

    # Every symbol in a spec carries its own `[freq,...]` bracket; a single
    # trailing one would only bind to the last pair, and `fugazi get` rejects
    # the rest outright ("missing `[freq,...]` bracket").
    pairs = ",".join(f"{sym}={cid}[1d]" for sym, cid in ids.items())
    cg_spec = f"cg:{pairs}"

    clamped = " — clamped to the public tier's 365-day window" if since > declared else ""
    print(f"  fetch-cg  {yml_path}  →  {out_csv}  (since {since}{clamped})")
    cmd = ["fugazi", "get", cg_spec, "--since", since, "--quiet", "-o", str(out_csv)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  error ({yml_path}): {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def main() -> None:
    if sys.argv[1:]:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        paths = sorted(Path(".").glob("crypto/*.yml"))

    fetched = 0
    for i, p in enumerate(paths):
        did_fetch = fetch_dataset(p)
        if did_fetch:
            fetched += 1
            if i < len(paths) - 1:
                time.sleep(RATE_LIMIT_SLEEP)

    if fetched:
        print(f"\n{fetched} dataset(s) fetched — run 'make dist' or 'python pack.py' to repack")
    else:
        print("no datasets with coingecko_ids found")


if __name__ == "__main__":
    main()
