.PHONY: dev dev-proxy down reset logs db-shell test test-unit test-integration lint fmt typecheck ci install claude claude-proxy forge-test deploy-local

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
	pytest tests/ -v --cov=agentproof --cov-report=term-missing

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
	mypy src/agentproof/

# Full CI check
ci: lint typecheck test

# Launch Claude Code through AgentProof → Anthropic (use with `make dev`)
claude:
	ANTHROPIC_BASE_URL=http://localhost:8100 claude

# Launch Claude Code through AgentProof → LiteLLM (use with `make dev-proxy`)
claude-proxy:
	ANTHROPIC_BASE_URL=http://localhost:8100 claude

# Run Foundry contract tests
forge-test:
	cd contracts && forge test -v

# Deploy contracts to local Anvil
deploy-local:
	cd contracts && DEPLOYER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 forge script script/Deploy.s.sol --rpc-url http://localhost:8545 --broadcast

# Install dev dependencies
install:
	pip install -e ".[dev]"
	cd dashboard && pnpm install
