PYTHON ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3.11)
.PHONY: setup test bench dev-backend dev-frontend samples
setup:
	@test -n "$(PYTHON)" || (echo 'Need Python 3.11-3.13 (python3.14 is not supported yet)'; exit 1)
	cd backend && rm -rf .venv && $(PYTHON) -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt -r requirements-ml.txt
	cd frontend && npm install
test:
	cd backend && . .venv/bin/activate && python -m pytest -q
samples:
	cd backend && . .venv/bin/activate && python scripts/make_samples.py
bench:
	cd backend && . .venv/bin/activate && python scripts/benchmark.py --split test --embedding sentence-transformers
dev-backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000
dev-frontend:
	cd frontend && npm run dev
