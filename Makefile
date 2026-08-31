.PHONY: resolve test build clean

PYTHON ?= python3

resolve:
	$(PYTHON) scripts/build_bundle.py --resolve-only

test:
	$(PYTHON) -m unittest discover -v

build:
	$(PYTHON) scripts/build_bundle.py

clean:
	rm -rf dist .cache __pycache__ scripts/__pycache__ tests/__pycache__
