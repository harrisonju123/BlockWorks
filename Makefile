.PHONY: dev down reset test lint typecheck ci dashboard

# Start the full local stack (DB + LiteLLM proxy + API)
dev:
	docker compose up -d db litellm
	@echo "Waiting for DB to be healthy..."
	@docker compose exec db pg_isready -U agentproof -q || sleep 2
	uvicorn agentproof.api.app:app --host 0.0.0.0 --port 8100 --reload

# Start everything in Docker (no local Python)
dev-docker:
	docker compose up --build

# Stop all services
down:
	docker compose down

# Full teardown: remove volumes, rebuild
reset:
	docker compose down -v
	docker compose up --build -d

# Run tests
test:
	pytest tests/ -v --cov=agentproof --cov-report=term-missing

# Run unit tests only (fast)
test-unit:
	pytest tests/unit/ -v

# Run integration tests (needs Docker)
test-integration:
	pytest tests/integration/ -v

# Lint and format
lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

# Format in place
fmt:
	ruff check --fix src/ tests/
	ruff format src/ tests/

# Type check
typecheck:
	mypy src/agentproof/

# Full CI check
ci: lint typecheck test

# Dashboard dev server
dashboard:
	cd dashboard && pnpm dev

# Install dev dependencies
install:
	pip install -e ".[dev]"
	cd dashboard && pnpm install

# DB shell
db-shell:
	docker compose exec db psql -U agentproof
