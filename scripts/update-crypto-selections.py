#!/usr/bin/env python3
"""Update crypto symbol selections using CoinGecko market cap ranking.

Behaviour:
- Queries CoinGecko for top-50 coins by market cap
- Filters to coins with an active USDT pair on Binance
- Excludes stablecoins (price pegged to ~1 USD)
- Each dataset declares how many symbols it wants and which other dataset
  to exclude (so large-cap and mid-cap don't overlap)
- Carries each dataset's anchors through untouched — symbols an overlay names
  and that must stay in the fetch even though the ranking would not pick them
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

# Each entry: (yaml_path, exclude_yaml_path_or_None, anchors)
#
# exclude_yaml: symbols already selected in that dataset are skipped here,
#   so large-cap and mid-cap never overlap.
#
# anchors: symbols pinned into the dataset regardless of ranking, because an
#   overlay names them. They sit outside the ranked universe — they don't count
#   toward the dataset's size, they survive a reselection, and they are kept out
#   of the candidate pool so they can't also be drawn as a constituent. Dropping
#   one silently breaks the overlay that reads it, so they live here rather than
#   only in the YAML, where the rewrite below would clobber them.
#
# Every mid-cap set excludes large-cap-1d rather than the large-cap file at its
# own interval. large-cap-1h is a deliberately reduced 5-symbol universe, so
# excluding it would leave mid-cap-1h holding ranks 6–15 while mid-cap-1d holds
# 10–19 — three datasets that describe themselves as the same universe, and are
# meant to be compared cross-sectionally, would quietly diverge.
DATASETS = [
    (REPO / "crypto/large-cap-1d.yml",  None,                              ()),
    (REPO / "crypto/large-cap-4h.yml",  None,                              ()),
    (REPO / "crypto/large-cap-1h.yml",  None,                              ()),
    (REPO / "crypto/mid-cap-1d.yml",    REPO / "crypto/large-cap-1d.yml",  ("BTCUSDT",)),
    (REPO / "crypto/mid-cap-4h.yml",    REPO / "crypto/large-cap-1d.yml",  ("BTCUSDT",)),
    (REPO / "crypto/mid-cap-1h.yml",    REPO / "crypto/large-cap-1d.yml",  ("BTCUSDT",)),
]

# Trailing comment carried on an anchor's line, so the YAML says why the symbol
# is there without the reader having to cross-reference this script.
ANCHOR_NOTE = {"BTCUSDT": "anchor for btc_sharpe*; not a mid-cap constituent"}

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

def write_symbols(yaml_path: Path, symbols: list[str], anchors: tuple[str, ...] = ()) -> None:
    """Replace the symbols list in a crypto dataset YAML, re-emitting `anchors`.

    The whole block is rewritten, so anything not passed back in is lost — which
    is why anchors are re-appended here rather than being left for the regex to
    preserve. They go last so the ranked selection reads in rank order.

    The match tolerates a trailing `# ...` on a symbol line, since the anchors
    this writes carry one; without that, a second run would fail to match the
    block it had just written and silently leave the file untouched.
    """
    text = yaml_path.read_text()
    indent = "        "  # 8 spaces
    lines = [f"{indent}- {s}\n" for s in symbols]
    for a in anchors:
        note = ANCHOR_NOTE.get(a)
        lines.append(f"{indent}- {a}   # {note}\n" if note else f"{indent}- {a}\n")
    new_block = "      symbols:\n" + "".join(lines)
    text, n = re.subn(
        r"      symbols:\n(?:        - \S+(?:[ \t]+#.*)?\n)+",
        lambda _: new_block,
        text,
    )
    if n != 1:
        raise SystemExit(f"{yaml_path}: expected one symbols block, matched {n}")
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

    for yaml_path, exclude_yaml, anchors in DATASETS:
        # Build the available pool for this dataset (exclude symbols reserved by
        # another, and any anchor, which is present for the overlay's sake and
        # must not also be drawn as a ranked constituent).
        excluded: set[str] = set()
        if exclude_yaml and exclude_yaml.exists():
            excluded = set(read_symbols(exclude_yaml))
        pool = [s for s in candidates if s not in excluded and s not in anchors]

        # Anchors are not part of the ranked universe, so they neither count
        # toward `n` nor show up as an addition/removal against the ranking.
        file_symbols = read_symbols(yaml_path)
        current = [s for s in file_symbols if s not in anchors]
        n = len(current)
        new_selection = pool[:n]

        added = [s for s in new_selection if s not in current]
        removed = [s for s in current if s not in new_selection]
        # Self-heal: if an anchor was deleted by hand, put it back rather than
        # leaving the overlay that reads it to fail at fetch time.
        missing = [a for a in anchors if a not in file_symbols]

        label = f"top-{n}" if not excluded else f"rank {len(excluded)+1}–{len(excluded)+n}"
        print(f"{yaml_path.name}  ({label})")
        if not added and not removed and not missing:
            print("  no changes")
            print()
            continue

        any_changed = True
        if added:
            print(f"  + {', '.join(added)}")
        if removed:
            print(f"  - {', '.join(removed)}")
        if missing:
            print(f"  ⚓ restoring anchor: {', '.join(missing)}")

        write_symbols(yaml_path, new_selection, anchors)
        print(f"  → `make fetch` will redownload this dataset")
        print()

    if any_changed:
        print("Done. Run `make fetch` to redownload the changed datasets, then `make dist` to package.")
    else:
        print("Done. Selection is already up to date.")

if __name__ == "__main__":
    main()
