#!/usr/bin/env python3
"""Update crypto symbol selections using CoinGecko market cap ranking.

Behaviour:
- Queries CoinGecko for top-50 coins by market cap
- Filters to coins with an active USDT pair on Binance
- Excludes stablecoins (price pegged to ~1 USD)
- Each dataset declares how many symbols it wants and which other dataset
  to exclude (so large-cap and mid-cap don't overlap)
- Updates crypto/*.yml symbol lists in place

Rewriting a YAML is the whole job: `make fetch` downloads a dataset whole
rather than incrementally, and make sees the newer mtime, so the next fetch
backfills the symbols that arrived and drops the ones that left without this
script having to touch data/ at all. Older dataset versions already uploaded to
fugazi-web serve as the historical archive.
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Each entry: (yaml_path, exclude_yaml_path_or_None)
# exclude_yaml: symbols already selected in that dataset are skipped here,
# so large-cap and mid-cap never overlap.
DATASETS = [
    (REPO / "crypto/large-cap-1d.yml",  None),
    (REPO / "crypto/large-cap-4h.yml",  None),
    (REPO / "crypto/large-cap-1h.yml",  None),
    (REPO / "crypto/mid-cap-1d.yml",    REPO / "crypto/large-cap-1d.yml"),
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
    indent = "        "  # 8 spaces
    new_block = "      symbols:\n" + "".join(f"{indent}- {s}\n" for s in symbols)
    text = re.sub(
        r"      symbols:\n(?:        - \S+\n)+",
        new_block,
        text,
    )
    yaml_path.write_text(text)

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Fetching selection sources…")
    cg_coins = coingecko_top(50)
    binance_pairs = binance_usdt_pairs()
    print()

    # Build ordered candidate list: CoinGecko rank → Binance USDT pair, stablecoins excluded
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

    for yaml_path, exclude_yaml in DATASETS:
        # Build the available pool for this dataset (exclude symbols reserved by another)
        excluded: set[str] = set()
        if exclude_yaml and exclude_yaml.exists():
            excluded = set(read_symbols(exclude_yaml))
        pool = [s for s in candidates if s not in excluded]

        current = read_symbols(yaml_path)
        n = len(current)
        new_selection = pool[:n]

        added = [s for s in new_selection if s not in current]
        removed = [s for s in current if s not in new_selection]

        label = f"top-{n}" if not excluded else f"rank {len(excluded)+1}–{len(excluded)+n}"
        print(f"{yaml_path.name}  ({label})")
        if not added and not removed:
            print("  no changes")
            print()
            continue

        any_changed = True
        if added:
            print(f"  + {', '.join(added)}")
        if removed:
            print(f"  - {', '.join(removed)}")

        write_symbols(yaml_path, new_selection)
        print(f"  → `make fetch` will redownload this dataset")
        print()

    if any_changed:
        print("Done. Run `make fetch` to redownload the changed datasets, then `make dist` to package.")
    else:
        print("Done. Selection is already up to date.")

if __name__ == "__main__":
    main()
