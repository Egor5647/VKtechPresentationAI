.PHONY: setup check-api download-qwen download-ministral download-zimage run run-local test

setup:
	./scripts/setup_local.sh

download-qwen:
	./scripts/download_model.sh qwen

download-ministral:
	./scripts/download_model.sh ministral

download-zimage:
	./scripts/download_model.sh zimage

run:
	./scripts/start_all.sh

check-api:
	./scripts/check_polza.py

run-local:
	AI_PROVIDER=local ./scripts/start_local.sh

test:
	.venv/bin/python -m pytest -q
