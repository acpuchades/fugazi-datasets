VERSION ?= $(shell date +%Y%m%d)

DATASETS     := $(shell find crypto equity macro -name '*.yml' | sort)
DATA_CSVS    := $(patsubst %.yml,data/%.csv,$(DATASETS))

.PHONY: all fetch fetch-cg refresh update-selections dist dist-fresh clean

## all — package existing CSVs; fetches first if nothing has been downloaded yet
all:
	@if [ -z "$$(find data -name '*.csv' -print -quit 2>/dev/null)" ]; then \
	  $(MAKE) --no-print-directory fetch; \
	fi
	@$(MAKE) --no-print-directory dist

## fetch — download the CSVs whose dataset YAML is newer, or missing entirely
fetch: $(DATA_CSVS)

# Each fetch downloads the dataset whole, so a CSV is always exactly what its
# YAML declares. `refresh` below is the way to redownload one that is merely
# stale in wall-clock terms, since make can't see that from mtimes.
data/%.csv: %.yml
	@python3 scripts/fetch-dataset.py $< $@

## update-selections — refresh crypto symbol lists from CoinGecko + Binance market cap ranking
update-selections:
	@python3 scripts/update-crypto-selections.py

## fetch-cg — download CoinGecko daily overlay data (market_cap, price, volume, supply) for crypto datasets
fetch-cg:
	@python3 scripts/fetch-cg-overlays.py

## refresh — force re-download of all CSVs from scratch
refresh:
	rm -f $(DATA_CSVS)
	@$(MAKE) fetch

## dist — package existing CSVs into fugazi-web upload archives
dist:
	@python3 pack.py $(VERSION)

## dist-fresh — fetch + package in one step
dist-fresh: fetch dist

## clean — remove downloaded data and built archives
clean:
	rm -rf data/ dist/
