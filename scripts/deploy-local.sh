#!/usr/bin/env bash
set -euo pipefail

ANVIL_URL="${ANVIL_URL:-http://anvil:8545}"
DEPLOYER_PRIVATE_KEY="${DEPLOYER_PRIVATE_KEY:-0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80}"
OUTPUT_FILE="${OUTPUT_FILE:-/app/deployments/local.json}"
MAX_RETRIES=30

echo "Waiting for Anvil at $ANVIL_URL..."
for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf -X POST "$ANVIL_URL" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' > /dev/null 2>&1; then
        echo "Anvil is ready (attempt $i)"
        break
    fi
    if [ "$i" -eq "$MAX_RETRIES" ]; then
        echo "ERROR: Anvil not ready after $MAX_RETRIES attempts"
        exit 1
    fi
    sleep 1
done

cd /app/contracts

# Ensure forge-std is available
if [ ! -d "lib/forge-std" ]; then
    echo "Installing forge-std..."
    forge install foundry-rs/forge-std --no-commit
fi

echo "Deploying contracts..."
DEPLOY_OUTPUT=$(DEPLOYER_PRIVATE_KEY="$DEPLOYER_PRIVATE_KEY" \
    forge script script/Deploy.s.sol \
    --rpc-url "$ANVIL_URL" \
    --broadcast \
    --silent 2>&1) || {
    echo "Forge script failed:"
    echo "$DEPLOY_OUTPUT"
    exit 1
}

echo "$DEPLOY_OUTPUT"

# Parse addresses from forge console.log output
parse_address() {
    echo "$DEPLOY_OUTPUT" | grep "$1:" | awk '{print $NF}'
}

TOKEN=$(parse_address "AgentProofToken")
ATTESTATION=$(parse_address "AgentProofAttestation")
CHANNEL=$(parse_address "AgentProofChannel")
STAKING=$(parse_address "AgentProofStaking")
TRUST=$(parse_address "AgentProofTrust")
REVENUE=$(parse_address "AgentProofRevenue")

# Write deployment addresses to JSON
cat > "$OUTPUT_FILE" <<EOF
{
  "chain_id": 31337,
  "deployer": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
  "contracts": {
    "AgentProofToken": "${TOKEN}",
    "AgentProofAttestation": "${ATTESTATION}",
    "AgentProofChannel": "${CHANNEL}",
    "AgentProofStaking": "${STAKING}",
    "AgentProofTrust": "${TRUST}",
    "AgentProofRevenue": "${REVENUE}"
  }
}
EOF

echo "Deployment addresses written to $OUTPUT_FILE"
cat "$OUTPUT_FILE"
