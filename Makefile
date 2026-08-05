VERSION ?= $(shell date +%Y%m%d)

DATASETS := $(shell find crypto equity macro -name '*.yml' | sort)

.PHONY: all fetch dist clean

all: dist

## fetch — download candle data for every dataset into data/
fetch:
	@for yml in $(DATASETS); do \
	  slug=$$(echo $$yml | sed 's|\.yml$$||'); \
	  mkdir -p data/$$(dirname $$slug); \
	  echo "  fetch $$yml"; \
	  fugazi get @$$yml -o data/$$slug.csv; \
	done

## dist — package existing CSVs into fugazi-web upload archives
dist:
	@python3 pack.py $(VERSION)

## dist-fresh — fetch + package in one step
dist-fresh: fetch dist

## clean — remove downloaded data and built archives
clean:
	rm -rf data/ dist/
