.PHONY: setup seed test test-integration test-e2e lint typecheck \
       paper paper-a paper-b agent logs dashboard \
       funding-report portfolio-report \
       docker-up docker-down clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PYTEST := $(PYTHON) -m pytest
DOCKER_COMPOSE := docker compose

# ── Setup ────────────────────────────────────────────────────────────────

$(VENV)/bin/activate:
	python3.13 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip

setup: $(VENV)/bin/activate
	$(PYTHON) -m pip install -e ".[dev]"
	$(DOCKER_COMPOSE) up -d redis postgres
	@echo "Dependencies installed, services started"
	@echo "Activate the venv: source $(VENV)/bin/activate"

seed:
	$(PYTHON) scripts/seed_data.py

# ── Testing ──────────────────────────────────────────────────────────────

test:
	$(PYTEST) --ignore=tests/integration --ignore=tests/e2e -v

test-integration:
	$(DOCKER_COMPOSE) up -d redis postgres
	$(PYTEST) tests/integration -v -m integration

test-e2e:
	$(DOCKER_COMPOSE) up -d
	$(PYTEST) tests/e2e -v -m e2e

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy libs/ agents/ orchestrator/

fmt:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

# ── Running ──────────────────────────────────────────────────────────────

paper:
	ENVIRONMENT=paper $(DOCKER_COMPOSE) up -d --build
	@echo "Full pipeline running in paper mode (both portfolios)"

paper-a:
	ENVIRONMENT=paper PORTFOLIO_MODE=a $(DOCKER_COMPOSE) up -d --build
	@echo "Portfolio A pipeline running in paper mode"

paper-b:
	ENVIRONMENT=paper PORTFOLIO_MODE=b $(DOCKER_COMPOSE) up -d --build
	@echo "Portfolio B pipeline running in paper mode"

agent:
ifndef AGENT
	$(error AGENT is not set. Usage: make agent AGENT=signals)
endif
	$(PYTHON) -m agents.$(AGENT).main

logs:
ifndef AGENT
	$(DOCKER_COMPOSE) logs -f
else
	$(DOCKER_COMPOSE) logs -f $(AGENT)
endif

# ── Dashboard ────────────────────────────────────────────────────────────

dashboard:
	$(PYTHON) scripts/dashboard.py

# ── Reports ──────────────────────────────────────────────────────────────

funding-report:
	$(PYTHON) scripts/funding_analysis.py

portfolio-report:
	$(PYTHON) scripts/portfolio_report.py

# ── Docker ───────────────────────────────────────────────────────────────

docker-up:
	$(DOCKER_COMPOSE) up -d

docker-down:
	$(DOCKER_COMPOSE) down

clean:
	$(DOCKER_COMPOSE) down -v
	rm -rf $(VENV)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
