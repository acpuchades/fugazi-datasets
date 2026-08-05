VERSION ?= $(shell date +%Y%m%d)

DATASETS     := $(shell find crypto equity macro -name '*.yml' | sort)
DATA_CSVS    := $(patsubst %.yml,data/%.csv,$(DATASETS))

.PHONY: all fetch refresh dist dist-fresh clean

all: dist

## fetch — download only missing or YAML-changed CSVs; update existing ones incrementally
fetch: $(DATA_CSVS)

data/%.csv: %.yml
	@mkdir -p $(dir $@)
	@if [ -f $@ ]; then \
	  last=$$(tail -n +2 $@ | awk -F',' '$$3 > max { max = $$3 } END { print max }' | cut -dT -f1); \
	  next=$$(date -d "$$last + 1 day" +%Y-%m-%d 2>/dev/null || date -v+1d -j -f %Y-%m-%d "$$last" +%Y-%m-%d); \
	  printf "  update %-40s (since %s)\n" "$<" "$$next"; \
	  out=$$(fugazi get --quiet @$< --since $$next -o /tmp/_fugazi_incr.csv 2>&1) && \
	    tail -n +2 /tmp/_fugazi_incr.csv >> $@ || { echo "$$out" >&2; true; }; \
	else \
	  printf "  fetch  %s\n" "$<"; \
	  out=$$(fugazi get --quiet @$< -o $@ 2>&1) || { echo "$$out" >&2; false; }; \
	fi

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
