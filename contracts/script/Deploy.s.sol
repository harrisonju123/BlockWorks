// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {AgentProofAttestation} from "../src/AgentProofAttestation.sol";

/// @title Deploy AgentProofAttestation to Base Sepolia
/// @dev Usage: forge script script/Deploy.s.sol --rpc-url base_sepolia --broadcast --verify
contract Deploy is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);

        vm.startBroadcast(deployerPrivateKey);

        AgentProofAttestation registry = new AgentProofAttestation(deployer);

        vm.stopBroadcast();

        console.log("AgentProofAttestation deployed at:", address(registry));
        console.log("Owner / first attestor:", deployer);
    }
}
