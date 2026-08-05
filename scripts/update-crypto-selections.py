#!/usr/bin/env python3
"""Update crypto large-cap symbol selections using CoinGecko market cap ranking.

Behaviour:
- Queries CoinGecko for top-50 coins by market cap
- Filters to coins with an active USDT pair on Binance
- Selects top-N per dataset (count is read from the current YAML)
- Updates crypto/*.yml symbol lists in place
- Removes rows for dropped symbols from existing data/*.csv files
- New symbols get full backfill on the next `make fetch`

The YAML and each data CSV are always in sync; older dataset versions
already uploaded to fugazi-web serve as the historical archive.
"""

import csv
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (yaml_path, csv_path)
DATASETS = [
    (REPO / "crypto/large-cap-1d.yml",  REPO / "data/crypto/large-cap-1d.csv"),
    (REPO / "crypto/large-cap-4h.yml",  REPO / "data/crypto/large-cap-4h.csv"),
    (REPO / "crypto/large-cap-1h.yml",  REPO / "data/crypto/large-cap-1h.csv"),
]

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, pause: float = 1.0) -> object:
    time.sleep(pause)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# ── Data sources ──────────────────────────────────────────────────────────────

def binance_usdt_pairs() -> set[str]:
    """Return all USDT pairs currently listed on Binance (e.g. 'BTCUSDT')."""
    print("  fetching Binance USDT pairs…")
    tickers = _get("https://api.binance.com/api/v3/ticker/price")
    return {t["symbol"] for t in tickers if t["symbol"].endswith("USDT")}

def coingecko_top(n: int = 50) -> list[dict]:
    """Return top-n coins ordered by market cap from CoinGecko."""
    print("  fetching CoinGecko market cap ranking…")
    return _get(
        f"https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&order=market_cap_desc&per_page={n}&page=1&sparkline=false"
    )

# ── YAML manipulation ─────────────────────────────────────────────────────────

def read_symbols(yaml_path: Path) -> list[str]:
    """Extract the symbol list from a crypto dataset YAML."""
    symbols = []
    in_symbols = False
    for line in yaml_path.read_text().splitlines():
        if re.match(r"^\s+symbols:\s*$", line):
            in_symbols = True
            continue
        if in_symbols:
            m = re.match(r"^\s+-\s+(\S+)", line)
            if m:
                symbols.append(m.group(1))
            elif line.strip() and not line.startswith(" "):
                break
    return symbols

def write_symbols(yaml_path: Path, symbols: list[str]) -> None:
    """Replace the symbols list in a crypto dataset YAML."""
    text = yaml_path.read_text()
    # Build replacement block
    indent = "        "  # 8 spaces — matches current YAML indentation
    new_block = "      symbols:\n" + "".join(f"{indent}- {s}\n" for s in symbols)
    # Replace from 'symbols:' line to end of list (lines starting with '        -')
    text = re.sub(
        r"      symbols:\n(?:        - \S+\n)+",
        new_block,
        text,
    )
    yaml_path.write_text(text)

# ── CSV manipulation ──────────────────────────────────────────────────────────

def filter_csv(csv_path: Path, keep: set[str]) -> int:
    """Remove rows for symbols not in keep. Returns number of rows removed."""
    if not csv_path.exists():
        return 0
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    kept = [r for r in rows if r["symbol"] in keep]
    removed = len(rows) - len(kept)
    if removed:
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(kept)
    return removed

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Fetching selection sources…")
    cg_coins = coingecko_top(50)
    binance_pairs = binance_usdt_pairs()
    print()

    # Build ordered candidate list: CoinGecko rank → Binance USDT pair
    # Exclude stablecoins (price pegged to ~1 USD) — they aren't tradeable alpha sources
    candidates: list[str] = []
    for coin in cg_coins:
        price = coin.get("current_price") or 0
        if 0.95 <= price <= 1.05:
            continue  # stablecoin
        pair = coin["symbol"].upper() + "USDT"
        if pair in binance_pairs:
            candidates.append(pair)

    if not candidates:
        print("ERROR: no candidates found — check network connectivity", file=sys.stderr)
        sys.exit(1)

    any_changed = False

    for yaml_path, csv_path in DATASETS:
        current = read_symbols(yaml_path)
        n = len(current)
        new_selection = candidates[:n]

        added = [s for s in new_selection if s not in current]
        removed = [s for s in current if s not in new_selection]

        print(f"{yaml_path.name}  (top-{n})")
        if not added and not removed:
            print("  no changes")
            continue

        any_changed = True
        if added:
            print(f"  + {', '.join(added)}")
        if removed:
            print(f"  - {', '.join(removed)}")

        write_symbols(yaml_path, new_selection)

        if removed and csv_path.exists():
            n_rows = filter_csv(csv_path, set(new_selection))
            if n_rows:
                print(f"  removed {n_rows} rows from {csv_path.name}")

        if added:
            print(f"  → new symbols will be backfilled by `make fetch`")

        print()

    if any_changed:
        print("Done. Run `make fetch` to backfill new symbols, then `make dist` to package.")
    else:
        print("Done. Selection is already up to date.")

if __name__ == "__main__":
    main()
