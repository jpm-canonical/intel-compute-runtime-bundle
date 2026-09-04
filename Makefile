.PHONY: resolve test build package clean

PYTHON ?= python3

resolve:
	@$(PYTHON) scripts/build_bundle.py --resolve-only

test:
	@$(PYTHON) -m unittest discover -v

build:
	@$(PYTHON) scripts/build_bundle.py

package:
	@set -e; \
	tmpdir=$$(mktemp -d); \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	mkdir -p dist; \
	$(PYTHON) scripts/build_bundle.py --output-dir "$$tmpdir"; \
	tar -czf dist/intel-compute-runtime-amd64.tar.gz -C "$$tmpdir" .

clean:
	rm -rf dist .cache __pycache__ scripts/__pycache__ tests/__pycache__
