.PHONY: setup download-qwen download-ministral download-zimage run test

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

test:
	.venv/bin/python -m pytest -q

