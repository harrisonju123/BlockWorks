.PHONY: dev dev-proxy down reset logs db-shell test test-unit test-integration lint fmt typecheck ci install claude claude-proxy forge-test deploy-local seed demo demo-traffic demo-check demo-reset benchmark benchmark-anthropic benchmark-estimate validate-fitness

# Start full stack (DB + API + Dashboard)
dev:
	docker compose up --build

# Start full stack + local LiteLLM proxy (for testing the callback locally)
dev-proxy:
	docker compose --profile proxy up --build

# Stop all services
down:
	docker compose down

# Full teardown: wipe DB volumes + rebuild
reset:
	docker compose down -v
	docker compose up --build

# Tail logs
logs:
	docker compose logs -f

# DB shell
db-shell:
	docker compose exec db psql -U agentproof

# Run unit tests only (fast)
test-unit:
	pytest tests/unit/ -v

# Run integration tests (needs Docker)
test-integration:
	pytest tests/integration/ -v

# Run all tests with coverage
test:
	pytest tests/ -v --cov=blockthrough --cov-report=term-missing

# Lint and format check
lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

# Format in place
fmt:
	ruff check --fix src/ tests/
	ruff format src/ tests/

# Type check
typecheck:
	mypy src/blockthrough/

# Full CI check
ci: lint typecheck test

# Launch Claude Code through Blockthrough → Anthropic (use with `make dev`)
claude:
	ANTHROPIC_BASE_URL=http://localhost:8100 claude

# Launch Claude Code through Blockthrough → LiteLLM (use with `make dev-proxy`)
claude-proxy:
	ANTHROPIC_BASE_URL=http://localhost:8100 claude

# Force a specific model, bypassing routing: make force-model MODEL=claude-opus-4-6
force-model:
	ANTHROPIC_BASE_URL=http://localhost:8100 ANTHROPIC_EXTRA_HEADERS='{"x-force-model":"$(MODEL)"}' claude

# Run Foundry contract tests
forge-test:
	cd contracts && forge test -v

# Deploy contracts to local Anvil
deploy-local:
	cd contracts && DEPLOYER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 forge script script/Deploy.s.sol --rpc-url http://localhost:8545 --broadcast

# Seed demo data into a running stack
seed:
	docker compose exec -T api python /app/scripts/seed_demo.py

# Pre-flight check: Docker installed and running
demo-check:
	@command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found. Install Docker Desktop first."; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon not running. Start Docker Desktop first."; exit 1; }
	@command -v curl >/dev/null 2>&1 || { echo "ERROR: curl not found."; exit 1; }
	@echo "Pre-flight OK"

# Clean slate: tear down everything including volumes
demo-reset:
	docker compose down -v
	@echo "Volumes removed. Run 'make demo' to start fresh."

# One-command demo: build, start, wait, seed
demo: demo-check
	docker compose up -d --build
	@echo "Waiting for DB (up to 60s)..."
	@for i in $$(seq 1 60); do \
		docker compose exec -T db pg_isready -U agentproof > /dev/null 2>&1 && break; \
		[ $$i -eq 60 ] && echo "ERROR: DB did not become ready" && exit 1; \
		sleep 1; \
	done
	@echo "Waiting for API (up to 60s)..."
	@for i in $$(seq 1 60); do \
		curl -sf http://localhost:8100/health > /dev/null 2>&1 && break; \
		[ $$i -eq 60 ] && echo "ERROR: API did not become ready" && exit 1; \
		sleep 1; \
	done
	@echo "Seeding demo data..."
	docker compose exec -T api python /app/scripts/seed_demo.py
	@echo "Waiting for Dashboard (up to 90s)..."
	@for i in $$(seq 1 90); do \
		curl -sf http://localhost:8081 > /dev/null 2>&1 && break; \
		[ $$i -eq 90 ] && echo "ERROR: Dashboard did not become ready" && exit 1; \
		sleep 1; \
	done
	@echo ""
	@echo "╔══════════════════════════════════════════╗"
	@echo "║            Demo Ready                    ║"
	@echo "╠══════════════════════════════════════════╣"
	@echo "║  Dashboard:  http://localhost:8081       ║"
	@echo "║  API:        http://localhost:8100       ║"
	@echo "╠══════════════════════════════════════════╣"
	@echo "║  Next steps:                             ║"
	@echo "║    make demo-traffic  (live requests)    ║"
	@echo "║    make demo-reset    (clean slate)      ║"
	@echo "║                                          ║"
	@echo "║  Talk track: docs/DEMO_GUIDE.md          ║"
	@echo "╚══════════════════════════════════════════╝"

# Send live demo traffic through the proxy (requires ANTHROPIC_API_KEY)
demo-traffic:
	python scripts/demo_traffic.py

# Install dev dependencies
install:
	pip install -e ".[dev]"
	cd dashboard && pnpm install

# Run full benchmark eval set against all configured models
benchmark:
	docker compose exec -T api python /app/scripts/run_benchmarks.py

# Run benchmarks for Anthropic models only (cheapest to validate pipeline)
benchmark-anthropic:
	docker compose exec -T api python /app/scripts/run_benchmarks.py --models claude-sonnet-4-6 claude-haiku-4-5-20251001

# Dry run: show cost estimate without making API calls
benchmark-estimate:
	docker compose exec -T api python /app/scripts/run_benchmarks.py --dry-run

# Validate fitness matrix after benchmark run
validate-fitness:
	docker compose exec -T api python /app/scripts/validate_fitness.py
