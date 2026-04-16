# ourAgentTeams — Makefile
# All targets auto-detect the active Python environment.
# See README for setup instructions per platform.

.PHONY: help setup setup-conda setup-docker test run clean

# ── Default target ────────────────────────────────────────────────

help:
	@echo ""
	@echo "  ourAgentTeams — Available Make Targets"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make setup           Auto-detect uv/venv and install (macOS/Linux)"
	@echo "  make setup-conda     Create conda env 'ouragentteams'"
	@echo "  make setup-docker    Build Docker image"
	@echo "  make test            Run pytest test suite"
	@echo "  make run ARGS='...'  Run any CLI command, e.g. ARGS=\"start '任务'\""
	@echo "  make clean           Remove .venv and __pycache__"
	@echo "  ─────────────────────────────────────────────────────"
	@echo ""

# ── Path A: uv / venv (auto-detect) ──────────────────────────────

setup:
	@bash setup.sh

# ── Path B: conda ─────────────────────────────────────────────────

setup-conda:
	@echo "[make] Creating conda environment 'ouragentteams'..."
	@if conda env list | grep -q "^ouragentteams "; then \
		echo "[make] Environment exists, updating..."; \
		conda env update -f environment.yml --prune; \
	else \
		conda env create -f environment.yml; \
	fi
	@echo "[make] Downloading spacy language model..."
	@conda run -n ouragentteams python -m spacy download en_core_web_lg
	@mkdir -p data/tasks data/sessions data/memory data/vectorstore
	@echo ""
	@echo "[make] Done! Activate with:"
	@echo "       conda activate ouragentteams"

# ── Path C: Docker ────────────────────────────────────────────────

setup-docker:
	@echo "[make] Building Docker image..."
	@docker compose build
	@echo ""
	@echo "[make] Done! Run commands with:"
	@echo "       docker compose run --rm app start \"你的任务\""

# ── Test ──────────────────────────────────────────────────────────

test:
	@if [ -f .venv/bin/pytest ]; then \
		.venv/bin/python -m pytest -q; \
	elif command -v conda &>/dev/null && conda env list | grep -q "^ouragentteams "; then \
		conda run -n ouragentteams python -m pytest -q; \
	else \
		python -m pytest -q; \
	fi

# ── Run ───────────────────────────────────────────────────────────

run:
	@if [ -f .venv/bin/python ]; then \
		.venv/bin/python -m src.cli.main $(ARGS); \
	elif command -v conda &>/dev/null && conda env list | grep -q "^ouragentteams "; then \
		conda run -n ouragentteams python -m src.cli.main $(ARGS); \
	else \
		python -m src.cli.main $(ARGS); \
	fi

# ── Clean ─────────────────────────────────────────────────────────

clean:
	@echo "[make] Removing .venv and __pycache__..."
	@rm -rf .venv
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "[make] Done."
