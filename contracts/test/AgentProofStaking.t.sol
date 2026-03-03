// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {AgentProofStaking} from "../src/AgentProofStaking.sol";

contract AgentProofStakingTest is Test {

    AgentProofStaking public staking;

    address public owner = address(0x1);
    address public alice = address(0x2);
    address public bob   = address(0x3);
    address public eve   = address(0x4);

    uint256 constant MIN_STAKE = 0.1 ether;
    uint256 constant COOLDOWN  = 7 days;

    function setUp() public {
        vm.prank(owner);
        staking = new AgentProofStaking(owner, MIN_STAKE, COOLDOWN);
        // Fund test accounts
        vm.deal(alice, 10 ether);
        vm.deal(bob, 10 ether);
        vm.deal(eve, 10 ether);
    }

    // ---------------------------------------------------------------
    //  Constructor
    // ---------------------------------------------------------------

    function test_constructorSetsState() public view {
        assertEq(staking.owner(), owner);
        assertEq(staking.minStake(), MIN_STAKE);
        assertEq(staking.cooldownPeriod(), COOLDOWN);
    }

    function test_constructorRevertsOnZeroAddress() public {
        vm.expectRevert(AgentProofStaking.ZeroAddress.selector);
        new AgentProofStaking(address(0), MIN_STAKE, COOLDOWN);
    }

    // ---------------------------------------------------------------
    //  Staking
    // ---------------------------------------------------------------

    function test_stakeBecomesValidator() public {
        vm.prank(alice);
        staking.stake{value: 0.1 ether}();

        assertEq(staking.getStake(alice), 0.1 ether);
        assertTrue(staking.isValidator(alice));
    }

    function test_stakeBelowMinDoesNotBecomeValidator() public {
        vm.prank(alice);
        staking.stake{value: 0.05 ether}();

        assertEq(staking.getStake(alice), 0.05 ether);
        assertFalse(staking.isValidator(alice));
    }

    function test_stakeAccumulatesAcrossMultipleCalls() public {
        vm.startPrank(alice);
        staking.stake{value: 0.05 ether}();
        assertFalse(staking.isValidator(alice));

        staking.stake{value: 0.05 ether}();
        assertTrue(staking.isValidator(alice));
        assertEq(staking.getStake(alice), 0.1 ether);
        vm.stopPrank();
    }

    function test_stakeEmitsEvent() public {
        vm.prank(alice);
        vm.expectEmit(true, false, false, true);
        emit AgentProofStaking.Staked(alice, 0.1 ether, 0.1 ether);
        staking.stake{value: 0.1 ether}();
    }

    function test_stakeRevertsOnZeroAmount() public {
        vm.prank(alice);
        vm.expectRevert(AgentProofStaking.ZeroAmount.selector);
        staking.stake{value: 0}();
    }

    // ---------------------------------------------------------------
    //  Unstake request + cooldown + withdrawal
    // ---------------------------------------------------------------

    function test_requestUnstakeStartsCooldown() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(alice);
        staking.requestUnstake(0.5 ether);

        // Stake amount unchanged until withdrawal
        assertEq(staking.getStake(alice), 1 ether);
    }

    function test_withdrawAfterCooldown() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(alice);
        staking.requestUnstake(0.5 ether);

        // Warp past cooldown
        vm.warp(block.timestamp + COOLDOWN + 1);

        uint256 balanceBefore = alice.balance;
        vm.prank(alice);
        staking.withdraw();

        assertEq(staking.getStake(alice), 0.5 ether);
        assertEq(alice.balance, balanceBefore + 0.5 ether);
        assertTrue(staking.isValidator(alice));
    }

    function test_withdrawFullStakeLosesValidatorStatus() public {
        vm.prank(alice);
        staking.stake{value: 0.1 ether}();

        vm.prank(alice);
        staking.requestUnstake(0.1 ether);

        vm.warp(block.timestamp + COOLDOWN + 1);

        vm.prank(alice);
        staking.withdraw();

        assertEq(staking.getStake(alice), 0);
        assertFalse(staking.isValidator(alice));
    }

    function test_withdrawBeforeCooldownReverts() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(alice);
        staking.requestUnstake(0.5 ether);

        // Don't warp — try to withdraw immediately
        vm.prank(alice);
        vm.expectRevert(); // CooldownNotElapsed
        staking.withdraw();
    }

    function test_withdrawWithNoPendingUnstakeReverts() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(alice);
        vm.expectRevert(AgentProofStaking.NoPendingUnstake.selector);
        staking.withdraw();
    }

    function test_requestUnstakeMoreThanStakeReverts() public {
        vm.prank(alice);
        staking.stake{value: 0.1 ether}();

        vm.prank(alice);
        vm.expectRevert(
            abi.encodeWithSelector(
                AgentProofStaking.InsufficientBalance.selector,
                1 ether,
                0.1 ether
            )
        );
        staking.requestUnstake(1 ether);
    }

    function test_requestUnstakeNonValidatorReverts() public {
        vm.prank(alice);
        vm.expectRevert(AgentProofStaking.NotValidator.selector);
        staking.requestUnstake(0.1 ether);
    }

    function test_requestUnstakeZeroAmountReverts() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(alice);
        vm.expectRevert(AgentProofStaking.ZeroAmount.selector);
        staking.requestUnstake(0);
    }

    function test_unstakeEmitsEvent() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(alice);
        staking.requestUnstake(0.5 ether);

        vm.warp(block.timestamp + COOLDOWN + 1);

        vm.prank(alice);
        vm.expectEmit(true, false, false, true);
        emit AgentProofStaking.Unstaked(alice, 0.5 ether, 0.5 ether);
        staking.withdraw();
    }

    // ---------------------------------------------------------------
    //  Slashing
    // ---------------------------------------------------------------

    function test_slashReducesStake() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(owner);
        staking.slash(alice, 0.2 ether, "outlier score");

        assertEq(staking.getStake(alice), 0.8 ether);
        assertTrue(staking.isValidator(alice));
    }

    function test_slashBelowMinLosesValidatorStatus() public {
        vm.prank(alice);
        staking.stake{value: 0.1 ether}();

        vm.prank(owner);
        staking.slash(alice, 0.05 ether, "dishonest");

        assertEq(staking.getStake(alice), 0.05 ether);
        assertFalse(staking.isValidator(alice));
    }

    function test_slashCappedAtCurrentStake() public {
        vm.prank(alice);
        staking.stake{value: 0.1 ether}();

        // Slash more than available
        vm.prank(owner);
        staking.slash(alice, 10 ether, "severe offense");

        assertEq(staking.getStake(alice), 0);
        assertFalse(staking.isValidator(alice));
    }

    function test_slashEmitsEvent() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit AgentProofStaking.Slashed(alice, 0.2 ether, "test");
        staking.slash(alice, 0.2 ether, "test");
    }

    function test_slashNonValidatorReverts() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofStaking.NotValidator.selector);
        staking.slash(alice, 0.1 ether, "not a validator");
    }

    function test_slashZeroAmountReverts() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(owner);
        vm.expectRevert(AgentProofStaking.ZeroAmount.selector);
        staking.slash(alice, 0, "zero");
    }

    function test_slashByNonOwnerReverts() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(eve);
        vm.expectRevert(AgentProofStaking.Unauthorized.selector);
        staking.slash(alice, 0.1 ether, "unauthorized");
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    function test_transferOwnership() public {
        vm.prank(owner);
        staking.transferOwnership(bob);
        assertEq(staking.owner(), bob);
    }

    function test_transferOwnershipRevertsZeroAddress() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofStaking.ZeroAddress.selector);
        staking.transferOwnership(address(0));
    }

    function test_transferOwnershipRevertsNonOwner() public {
        vm.prank(eve);
        vm.expectRevert(AgentProofStaking.Unauthorized.selector);
        staking.transferOwnership(eve);
    }

    function test_setMinStake() public {
        vm.prank(owner);
        vm.expectEmit(false, false, false, true);
        emit AgentProofStaking.MinStakeUpdated(MIN_STAKE, 0.5 ether);
        staking.setMinStake(0.5 ether);

        assertEq(staking.minStake(), 0.5 ether);
    }

    function test_setMinStakeRevertsNonOwner() public {
        vm.prank(eve);
        vm.expectRevert(AgentProofStaking.Unauthorized.selector);
        staking.setMinStake(0.5 ether);
    }

    // ---------------------------------------------------------------
    //  View functions
    // ---------------------------------------------------------------

    function test_getStakeReturnsZeroForNonStaker() public view {
        assertEq(staking.getStake(eve), 0);
    }

    function test_isValidatorReturnsFalseForNonStaker() public view {
        assertFalse(staking.isValidator(eve));
    }

    // ---------------------------------------------------------------
    //  Multi-validator scenario
    // ---------------------------------------------------------------

    function test_multipleValidatorsIndependent() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(bob);
        staking.stake{value: 0.5 ether}();

        assertTrue(staking.isValidator(alice));
        assertTrue(staking.isValidator(bob));
        assertEq(staking.getStake(alice), 1 ether);
        assertEq(staking.getStake(bob), 0.5 ether);

        // Slash alice doesn't affect bob
        vm.prank(owner);
        staking.slash(alice, 0.3 ether, "outlier");

        assertEq(staking.getStake(alice), 0.7 ether);
        assertEq(staking.getStake(bob), 0.5 ether);
    }

    // ---------------------------------------------------------------
    //  Gas snapshots
    // ---------------------------------------------------------------

    function test_gasSnapshot_stake() public {
        vm.prank(alice);
        uint256 gasBefore = gasleft();
        staking.stake{value: 0.1 ether}();
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: stake (first time)", gasUsed);
    }

    function test_gasSnapshot_slash() public {
        vm.prank(alice);
        staking.stake{value: 1 ether}();

        vm.prank(owner);
        uint256 gasBefore = gasleft();
        staking.slash(alice, 0.1 ether, "test");
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: slash", gasUsed);
    }
}
