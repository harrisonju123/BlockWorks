// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {AgentProofTrust} from "../src/AgentProofTrust.sol";

contract AgentProofTrustTest is Test {

    AgentProofTrust public registry;

    address public owner   = address(0x1);
    address public updater = address(0x2);
    address public outsider = address(0x3);

    bytes32 constant AGENT_A = keccak256("agent-alpha");
    bytes32 constant AGENT_B = keccak256("agent-beta");
    bytes32 constant AGENT_C = keccak256("agent-gamma");

    function setUp() public {
        vm.prank(owner);
        registry = new AgentProofTrust(owner);
    }

    // ---------------------------------------------------------------
    //  Access control
    // ---------------------------------------------------------------

    function test_ownerIsUpdaterByDefault() public view {
        assertTrue(registry.updaters(owner));
    }

    function test_grantUpdater() public {
        vm.prank(owner);
        registry.grantUpdater(updater);
        assertTrue(registry.updaters(updater));
    }

    function test_revokeUpdater() public {
        vm.prank(owner);
        registry.grantUpdater(updater);

        vm.prank(owner);
        registry.revokeUpdater(updater);
        assertFalse(registry.updaters(updater));
    }

    function test_grantUpdaterEmitsEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, false, false, false);
        emit AgentProofTrust.UpdaterGranted(updater);
        registry.grantUpdater(updater);
    }

    function test_nonOwnerCannotGrantUpdater() public {
        vm.prank(outsider);
        vm.expectRevert(AgentProofTrust.Unauthorized.selector);
        registry.grantUpdater(updater);
    }

    function test_nonUpdaterCannotRegisterAgent() public {
        vm.prank(outsider);
        vm.expectRevert(AgentProofTrust.Unauthorized.selector);
        registry.registerAgent(AGENT_A);
    }

    function test_nonUpdaterCannotUpdateScore() public {
        vm.prank(owner);
        registry.registerAgent(AGENT_A);

        vm.prank(outsider);
        vm.expectRevert(AgentProofTrust.Unauthorized.selector);
        registry.updateScore(AGENT_A, 7000, 8000, 6000, 7500, 5000);
    }

    // ---------------------------------------------------------------
    //  Register
    // ---------------------------------------------------------------

    function test_registerAgentSetsNeutralScores() public {
        vm.prank(owner);
        registry.registerAgent(AGENT_A);

        AgentProofTrust.TrustScore memory s = registry.getScore(AGENT_A);
        assertEq(s.composite, 5000);
        assertEq(s.reliability, 5000);
        assertEq(s.efficiency, 5000);
        assertEq(s.quality, 5000);
        assertEq(s.usage, 5000);
    }

    function test_registerAgentEmitsEvents() public {
        vm.prank(owner);
        vm.expectEmit(true, false, false, false);
        emit AgentProofTrust.AgentRegistered(AGENT_A);
        registry.registerAgent(AGENT_A);
    }

    function test_registerAgentIncreasesCount() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);
        registry.registerAgent(AGENT_B);
        vm.stopPrank();

        assertEq(registry.agentCount(), 2);
    }

    function test_registerDuplicateAgentReverts() public {
        vm.prank(owner);
        registry.registerAgent(AGENT_A);

        vm.prank(owner);
        vm.expectRevert(AgentProofTrust.AgentAlreadyRegistered.selector);
        registry.registerAgent(AGENT_A);
    }

    // ---------------------------------------------------------------
    //  Update scores
    // ---------------------------------------------------------------

    function test_updateScore() public {
        vm.warp(1_700_000_000);
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);

        vm.warp(1_700_100_000);
        registry.updateScore(AGENT_A, 8500, 9000, 7500, 8800, 6000);
        vm.stopPrank();

        AgentProofTrust.TrustScore memory s = registry.getScore(AGENT_A);
        assertEq(s.composite, 8500);
        assertEq(s.reliability, 9000);
        assertEq(s.efficiency, 7500);
        assertEq(s.quality, 8800);
        assertEq(s.usage, 6000);
        assertEq(s.lastUpdated, 1_700_100_000);
    }

    function test_updateScoreEmitsEvent() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);

        vm.expectEmit(true, false, false, true);
        emit AgentProofTrust.ScoreUpdated(AGENT_A, 8500, 9000, 7500, 8800, 6000);
        registry.updateScore(AGENT_A, 8500, 9000, 7500, 8800, 6000);
        vm.stopPrank();
    }

    function test_updateScoreUnregisteredAgentReverts() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofTrust.AgentNotRegistered.selector);
        registry.updateScore(AGENT_A, 5000, 5000, 5000, 5000, 5000);
    }

    function test_updateScoreExceedingMaxReverts() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);

        // composite > 10000
        vm.expectRevert(AgentProofTrust.InvalidScore.selector);
        registry.updateScore(AGENT_A, 10001, 5000, 5000, 5000, 5000);
        vm.stopPrank();
    }

    function test_updateScoreReliabilityExceedingMaxReverts() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);

        vm.expectRevert(AgentProofTrust.InvalidScore.selector);
        registry.updateScore(AGENT_A, 5000, 10001, 5000, 5000, 5000);
        vm.stopPrank();
    }

    function test_updateScoreBoundaryValues() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);

        // Max valid values
        registry.updateScore(AGENT_A, 10000, 10000, 10000, 10000, 10000);
        AgentProofTrust.TrustScore memory s = registry.getScore(AGENT_A);
        assertEq(s.composite, 10000);

        // Min valid values
        registry.updateScore(AGENT_A, 0, 0, 0, 0, 0);
        s = registry.getScore(AGENT_A);
        assertEq(s.composite, 0);
        vm.stopPrank();
    }

    // ---------------------------------------------------------------
    //  Read: getScore
    // ---------------------------------------------------------------

    function test_getScoreUnregisteredReverts() public {
        vm.expectRevert(AgentProofTrust.AgentNotRegistered.selector);
        registry.getScore(AGENT_A);
    }

    // ---------------------------------------------------------------
    //  Read: getTopAgents
    // ---------------------------------------------------------------

    function test_getTopAgentsEmpty() public view {
        (bytes32[] memory agents, uint16[] memory topScores) = registry.getTopAgents(5);
        assertEq(agents.length, 0);
        assertEq(topScores.length, 0);
    }

    function test_getTopAgentsSorted() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);
        registry.registerAgent(AGENT_B);
        registry.registerAgent(AGENT_C);

        // Set different composite scores
        registry.updateScore(AGENT_A, 3000, 5000, 5000, 5000, 5000);
        registry.updateScore(AGENT_B, 9000, 5000, 5000, 5000, 5000);
        registry.updateScore(AGENT_C, 6000, 5000, 5000, 5000, 5000);
        vm.stopPrank();

        (bytes32[] memory agents, uint16[] memory topScores) = registry.getTopAgents(3);

        assertEq(agents.length, 3);
        assertEq(agents[0], AGENT_B);  // 9000 highest
        assertEq(agents[1], AGENT_C);  // 6000
        assertEq(agents[2], AGENT_A);  // 3000 lowest

        assertEq(topScores[0], 9000);
        assertEq(topScores[1], 6000);
        assertEq(topScores[2], 3000);
    }

    function test_getTopAgentsLimitLargerThanCount() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);
        registry.registerAgent(AGENT_B);
        vm.stopPrank();

        // Requesting more than exist should clamp to actual count
        (bytes32[] memory agents, ) = registry.getTopAgents(10);
        assertEq(agents.length, 2);
    }

    function test_getTopAgentsLimitOne() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);
        registry.updateScore(AGENT_A, 9000, 5000, 5000, 5000, 5000);
        registry.registerAgent(AGENT_B);
        registry.updateScore(AGENT_B, 3000, 5000, 5000, 5000, 5000);
        vm.stopPrank();

        (bytes32[] memory agents, uint16[] memory topScores) = registry.getTopAgents(1);
        assertEq(agents.length, 1);
        assertEq(agents[0], AGENT_A);
        assertEq(topScores[0], 9000);
    }

    // ---------------------------------------------------------------
    //  Ownership
    // ---------------------------------------------------------------

    function test_transferOwnership() public {
        vm.prank(owner);
        registry.transferOwnership(updater);
        assertEq(registry.owner(), updater);
    }

    function test_nonOwnerCannotTransferOwnership() public {
        vm.prank(outsider);
        vm.expectRevert(AgentProofTrust.Unauthorized.selector);
        registry.transferOwnership(outsider);
    }

    // ---------------------------------------------------------------
    //  Gas snapshots
    // ---------------------------------------------------------------

    function test_gasSnapshot_registerAgent() public {
        vm.prank(owner);
        uint256 gasBefore = gasleft();
        registry.registerAgent(AGENT_A);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: registerAgent", gasUsed);
    }

    function test_gasSnapshot_updateScore() public {
        vm.startPrank(owner);
        registry.registerAgent(AGENT_A);

        uint256 gasBefore = gasleft();
        registry.updateScore(AGENT_A, 8500, 9000, 7500, 8800, 6000);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: updateScore", gasUsed);
        vm.stopPrank();
    }
}
