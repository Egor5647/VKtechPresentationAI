.PHONY: setup run test check-api

setup:
	./scripts/setup_local.sh

check-api:
	.venv/bin/python scripts/check_api.py

run:
	./scripts/start_all.sh

test:
	.venv/bin/python -m pytest -q
