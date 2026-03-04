// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {AgentProofToken} from "../src/AgentProofToken.sol";
import {AgentProofAttestation} from "../src/AgentProofAttestation.sol";
import {AgentProofChannel} from "../src/AgentProofChannel.sol";
import {AgentProofStaking} from "../src/AgentProofStaking.sol";
import {AgentProofTrust} from "../src/AgentProofTrust.sol";
import {AgentProofRevenue} from "../src/AgentProofRevenue.sol";

/// @title Deploy all AgentProof contracts
/// @dev Usage: forge script script/Deploy.s.sol --rpc-url anvil --broadcast
contract Deploy is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);

        uint256 minStake = vm.envOr("STAKING_MIN_STAKE", uint256(0.1 ether));
        uint256 cooldownPeriod = vm.envOr("STAKING_COOLDOWN", uint256(300));

        vm.startBroadcast(deployerPrivateKey);

        // Deploy order respects constructor dependencies:
        // Token + Attestation + Trust (independent) -> Channel (no deps)
        // -> Staking (independent) -> Revenue (needs Token)
        AgentProofToken token = new AgentProofToken(deployer);
        AgentProofAttestation attestation = new AgentProofAttestation(deployer);
        AgentProofChannel channel = new AgentProofChannel();
        AgentProofStaking staking = new AgentProofStaking(deployer, minStake, cooldownPeriod);
        AgentProofTrust trust = new AgentProofTrust(deployer);
        AgentProofRevenue revenue = new AgentProofRevenue(token, deployer);

        vm.stopBroadcast();

        console.log("AgentProofToken:", address(token));
        console.log("AgentProofAttestation:", address(attestation));
        console.log("AgentProofChannel:", address(channel));
        console.log("AgentProofStaking:", address(staking));
        console.log("AgentProofTrust:", address(trust));
        console.log("AgentProofRevenue:", address(revenue));
    }
}
