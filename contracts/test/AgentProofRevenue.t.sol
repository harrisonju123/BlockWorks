// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {AgentProofToken} from "../src/AgentProofToken.sol";
import {AgentProofRevenue} from "../src/AgentProofRevenue.sol";

contract AgentProofRevenueTest is Test {

    AgentProofToken public token;
    AgentProofRevenue public revenue;

    address public deployer;
    address public treasury;
    address public payer;
    address public participant1;
    address public participant2;
    address public participant3;

    function setUp() public {
        deployer = address(this);
        treasury = makeAddr("treasury");
        payer = makeAddr("payer");
        participant1 = makeAddr("participant1");
        participant2 = makeAddr("participant2");
        participant3 = makeAddr("participant3");

        token = new AgentProofToken(deployer);
        revenue = new AgentProofRevenue(token, treasury);

        // Fund the payer with tokens for settlements
        token.transfer(payer, 1_000_000 ether);
    }

    // ---------------------------------------------------------------
    //  Helpers
    // ---------------------------------------------------------------

    /// @dev Have the payer approve and settle a standard two-participant split.
    function _settleStandard(bytes32 execId) internal {
        address[] memory participants = new address[](2);
        participants[0] = participant1;
        participants[1] = participant2;

        uint256[] memory amounts = new uint256[](2);
        amounts[0] = 70 ether;
        amounts[1] = 27 ether;

        uint256 protocolFee = 3 ether;
        uint256 burnAmount = 0.9 ether; // 30% of 3

        uint256 totalNeeded = 70 ether + 27 ether + 3 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), totalNeeded);
        revenue.settle(execId, participants, amounts, protocolFee, burnAmount);
        vm.stopPrank();
    }

    // ---------------------------------------------------------------
    //  Constructor tests
    // ---------------------------------------------------------------

    function test_constructorSetsState() public view {
        assertEq(address(revenue.token()), address(token));
        assertEq(revenue.treasury(), treasury);
        assertEq(revenue.owner(), deployer);
    }

    function test_constructorRevertsZeroToken() public {
        vm.expectRevert(AgentProofRevenue.ZeroAddress.selector);
        new AgentProofRevenue(AgentProofToken(address(0)), treasury);
    }

    function test_constructorRevertsZeroTreasury() public {
        vm.expectRevert(AgentProofRevenue.ZeroAddress.selector);
        new AgentProofRevenue(token, address(0));
    }

    // ---------------------------------------------------------------
    //  Settlement tests
    // ---------------------------------------------------------------

    function test_settleDistributesTokens() public {
        _settleStandard(bytes32("exec-1"));

        assertEq(token.balanceOf(participant1), 70 ether);
        assertEq(token.balanceOf(participant2), 27 ether);
    }

    function test_settleCollectsProtocolFee() public {
        _settleStandard(bytes32("exec-1"));

        // Treasury gets protocolFee - burnAmount = 3 - 0.9 = 2.1
        assertEq(token.balanceOf(treasury), 2.1 ether);
    }

    function test_settleBurnsTokens() public {
        _settleStandard(bytes32("exec-1"));

        // Burn address gets burnAmount = 0.9
        assertEq(token.balanceOf(revenue.BURN_ADDRESS()), 0.9 ether);
    }

    function test_settleUpdatesEarnings() public {
        _settleStandard(bytes32("exec-1"));

        assertEq(revenue.getEarnings(participant1), 70 ether);
        assertEq(revenue.getEarnings(participant2), 27 ether);
    }

    function test_settleCumulativeEarnings() public {
        _settleStandard(bytes32("exec-1"));
        _settleStandard(bytes32("exec-2"));

        assertEq(revenue.getEarnings(participant1), 140 ether);
        assertEq(revenue.getEarnings(participant2), 54 ether);
    }

    function test_settleUpdatesProtocolRevenue() public {
        _settleStandard(bytes32("exec-1"));

        assertEq(revenue.getProtocolRevenue(), 3 ether);
        assertEq(revenue.getBurnedTotal(), 0.9 ether);
        assertEq(revenue.totalSettlements(), 1);
    }

    function test_settleEmitsSettledEvent() public {
        address[] memory participants = new address[](2);
        participants[0] = participant1;
        participants[1] = participant2;

        uint256[] memory amounts = new uint256[](2);
        amounts[0] = 70 ether;
        amounts[1] = 27 ether;

        uint256 totalNeeded = 100 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), totalNeeded);

        vm.expectEmit(true, false, false, true);
        emit AgentProofRevenue.Settled(
            bytes32("exec-e"),
            100 ether,  // 70 + 27 + 3
            3 ether,
            0.9 ether,
            2
        );
        revenue.settle(bytes32("exec-e"), participants, amounts, 3 ether, 0.9 ether);
        vm.stopPrank();
    }

    function test_settleEmitsEarningsUpdatedEvent() public {
        address[] memory participants = new address[](1);
        participants[0] = participant1;

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 50 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), 53 ether);

        vm.expectEmit(true, false, false, true);
        emit AgentProofRevenue.EarningsUpdated(participant1, 50 ether, 50 ether);
        revenue.settle(bytes32("exec-ev"), participants, amounts, 3 ether, 0.9 ether);
        vm.stopPrank();
    }

    // ---------------------------------------------------------------
    //  Three participants
    // ---------------------------------------------------------------

    function test_settleThreeParticipants() public {
        address[] memory participants = new address[](3);
        participants[0] = participant1;
        participants[1] = participant2;
        participants[2] = participant3;

        uint256[] memory amounts = new uint256[](3);
        amounts[0] = 40 ether;
        amounts[1] = 30 ether;
        amounts[2] = 27 ether;

        uint256 protocolFee = 3 ether;
        uint256 burnAmount = 0.9 ether;
        uint256 totalNeeded = 100 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), totalNeeded);
        revenue.settle(bytes32("exec-3"), participants, amounts, protocolFee, burnAmount);
        vm.stopPrank();

        assertEq(token.balanceOf(participant1), 40 ether);
        assertEq(token.balanceOf(participant2), 30 ether);
        assertEq(token.balanceOf(participant3), 27 ether);
    }

    // ---------------------------------------------------------------
    //  Zero-amount participant (skipped)
    // ---------------------------------------------------------------

    function test_settleSkipsZeroAmountParticipant() public {
        address[] memory participants = new address[](2);
        participants[0] = participant1;
        participants[1] = participant2;

        uint256[] memory amounts = new uint256[](2);
        amounts[0] = 97 ether;
        amounts[1] = 0;  // zero-share participant

        vm.startPrank(payer);
        token.approve(address(revenue), 100 ether);
        revenue.settle(bytes32("exec-z"), participants, amounts, 3 ether, 0.9 ether);
        vm.stopPrank();

        assertEq(token.balanceOf(participant1), 97 ether);
        assertEq(token.balanceOf(participant2), 0);
        // Earnings only track non-zero
        assertEq(revenue.getEarnings(participant2), 0);
    }

    // ---------------------------------------------------------------
    //  Revert conditions
    // ---------------------------------------------------------------

    function test_settleRevertsAlreadySettled() public {
        _settleStandard(bytes32("exec-dup"));

        vm.expectRevert(AgentProofRevenue.AlreadySettled.selector);
        _settleStandard(bytes32("exec-dup"));
    }

    function test_settleRevertsEmptyParticipants() public {
        address[] memory empty = new address[](0);
        uint256[] memory emptyAmounts = new uint256[](0);

        vm.startPrank(payer);
        token.approve(address(revenue), 10 ether);
        vm.expectRevert(AgentProofRevenue.EmptyParticipants.selector);
        revenue.settle(bytes32("exec-empty"), empty, emptyAmounts, 3 ether, 0.9 ether);
        vm.stopPrank();
    }

    function test_settleRevertsArrayLengthMismatch() public {
        address[] memory participants = new address[](2);
        participants[0] = participant1;
        participants[1] = participant2;

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 50 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), 100 ether);
        vm.expectRevert(AgentProofRevenue.ArrayLengthMismatch.selector);
        revenue.settle(bytes32("exec-mismatch"), participants, amounts, 3 ether, 0.9 ether);
        vm.stopPrank();
    }

    function test_settleRevertsBurnExceedsFee() public {
        address[] memory participants = new address[](1);
        participants[0] = participant1;

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 50 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), 100 ether);
        vm.expectRevert(AgentProofRevenue.InvalidAmount.selector);
        // burnAmount > protocolFee
        revenue.settle(bytes32("exec-burn"), participants, amounts, 3 ether, 4 ether);
        vm.stopPrank();
    }

    function test_settleRevertsZeroAddressParticipant() public {
        address[] memory participants = new address[](1);
        participants[0] = address(0);

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 50 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), 100 ether);
        vm.expectRevert(AgentProofRevenue.ZeroAddress.selector);
        revenue.settle(bytes32("exec-zero"), participants, amounts, 3 ether, 0.9 ether);
        vm.stopPrank();
    }

    // ---------------------------------------------------------------
    //  Read functions with no settlements
    // ---------------------------------------------------------------

    function test_getEarningsDefaultsToZero() public view {
        assertEq(revenue.getEarnings(participant1), 0);
    }

    function test_getProtocolRevenueDefaultsToZero() public view {
        assertEq(revenue.getProtocolRevenue(), 0);
    }

    function test_getBurnedTotalDefaultsToZero() public view {
        assertEq(revenue.getBurnedTotal(), 0);
    }

    // ---------------------------------------------------------------
    //  Admin tests
    // ---------------------------------------------------------------

    function test_setTreasuryUpdates() public {
        address newTreasury = makeAddr("newTreasury");
        revenue.setTreasury(newTreasury);
        assertEq(revenue.treasury(), newTreasury);
    }

    function test_setTreasuryEmitsEvent() public {
        address newTreasury = makeAddr("newTreasury");
        vm.expectEmit(true, true, false, false);
        emit AgentProofRevenue.TreasuryUpdated(treasury, newTreasury);
        revenue.setTreasury(newTreasury);
    }

    function test_setTreasuryRevertsZeroAddress() public {
        vm.expectRevert(AgentProofRevenue.ZeroAddress.selector);
        revenue.setTreasury(address(0));
    }

    function test_setTreasuryRevertsNonOwner() public {
        vm.prank(payer);
        vm.expectRevert(AgentProofRevenue.Unauthorized.selector);
        revenue.setTreasury(makeAddr("x"));
    }

    function test_transferOwnership() public {
        address newOwner = makeAddr("newOwner");
        revenue.transferOwnership(newOwner);
        assertEq(revenue.owner(), newOwner);
    }

    function test_transferOwnershipRevertsZeroAddress() public {
        vm.expectRevert(AgentProofRevenue.ZeroAddress.selector);
        revenue.transferOwnership(address(0));
    }

    function test_transferOwnershipRevertsNonOwner() public {
        vm.prank(payer);
        vm.expectRevert(AgentProofRevenue.Unauthorized.selector);
        revenue.transferOwnership(makeAddr("x"));
    }

    // ---------------------------------------------------------------
    //  Gas snapshot
    // ---------------------------------------------------------------

    function test_gasSnapshot_settleTwoParticipants() public {
        address[] memory participants = new address[](2);
        participants[0] = participant1;
        participants[1] = participant2;

        uint256[] memory amounts = new uint256[](2);
        amounts[0] = 70 ether;
        amounts[1] = 27 ether;

        vm.startPrank(payer);
        token.approve(address(revenue), 100 ether);

        uint256 gasBefore = gasleft();
        revenue.settle(bytes32("exec-gas"), participants, amounts, 3 ether, 0.9 ether);
        uint256 gasUsed = gasBefore - gasleft();
        vm.stopPrank();

        emit log_named_uint("Gas: settle 2 participants", gasUsed);
    }
}
