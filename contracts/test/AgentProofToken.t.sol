// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {AgentProofToken} from "../src/AgentProofToken.sol";

contract AgentProofTokenTest is Test {

    AgentProofToken public token;

    address public owner    = address(0x1);
    address public alice    = address(0x2);
    address public bob      = address(0x3);
    address public outsider = address(0x4);

    uint256 constant INITIAL_SUPPLY = 1_000_000_000 * 10 ** 18;

    function setUp() public {
        vm.prank(owner);
        token = new AgentProofToken(owner);
    }

    // ---------------------------------------------------------------
    //  Constructor / Initial state
    // ---------------------------------------------------------------

    function test_initialSupply() public view {
        assertEq(token.totalSupply(), INITIAL_SUPPLY);
    }

    function test_ownerReceivesInitialSupply() public view {
        assertEq(token.balanceOf(owner), INITIAL_SUPPLY);
    }

    function test_nameAndSymbol() public view {
        assertEq(token.name(), "AgentProof");
        assertEq(token.symbol(), "APR");
        assertEq(token.decimals(), 18);
    }

    function test_ownerIsSet() public view {
        assertEq(token.owner(), owner);
    }

    // ---------------------------------------------------------------
    //  Transfer
    // ---------------------------------------------------------------

    function test_transferBasic() public {
        uint256 amount = 1000 * 10 ** 18;

        vm.prank(owner);
        bool ok = token.transfer(alice, amount);

        assertTrue(ok);
        assertEq(token.balanceOf(alice), amount);
        assertEq(token.balanceOf(owner), INITIAL_SUPPLY - amount);
    }

    function test_transferEmitsEvent() public {
        uint256 amount = 500 * 10 ** 18;

        vm.prank(owner);
        vm.expectEmit(true, true, false, true);
        emit AgentProofToken.Transfer(owner, alice, amount);
        token.transfer(alice, amount);
    }

    function test_transferInsufficientBalance() public {
        vm.prank(alice);
        vm.expectRevert(AgentProofToken.InsufficientBalance.selector);
        token.transfer(bob, 1);
    }

    function test_transferToZeroAddress() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofToken.ZeroAddress.selector);
        token.transfer(address(0), 100);
    }

    function test_transferZeroAmount() public {
        vm.prank(owner);
        bool ok = token.transfer(alice, 0);
        assertTrue(ok);
        assertEq(token.balanceOf(alice), 0);
    }

    // ---------------------------------------------------------------
    //  Approve / TransferFrom
    // ---------------------------------------------------------------

    function test_approveAndTransferFrom() public {
        uint256 amount = 2000 * 10 ** 18;

        vm.prank(owner);
        token.approve(alice, amount);
        assertEq(token.allowance(owner, alice), amount);

        vm.prank(alice);
        bool ok = token.transferFrom(owner, bob, amount);

        assertTrue(ok);
        assertEq(token.balanceOf(bob), amount);
        assertEq(token.allowance(owner, alice), 0);
    }

    function test_approveEmitsEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, true, false, true);
        emit AgentProofToken.Approval(owner, alice, 1000);
        token.approve(alice, 1000);
    }

    function test_transferFromInsufficientAllowance() public {
        vm.prank(owner);
        token.approve(alice, 100);

        vm.prank(alice);
        vm.expectRevert(AgentProofToken.InsufficientAllowance.selector);
        token.transferFrom(owner, bob, 200);
    }

    function test_transferFromInsufficientBalance() public {
        // Alice has 0 tokens but gives Bob unlimited allowance
        vm.prank(alice);
        token.approve(bob, type(uint256).max);

        vm.prank(bob);
        vm.expectRevert(AgentProofToken.InsufficientBalance.selector);
        token.transferFrom(alice, outsider, 1);
    }

    function test_transferFromToZeroAddress() public {
        vm.prank(owner);
        token.approve(alice, 1000);

        vm.prank(alice);
        vm.expectRevert(AgentProofToken.ZeroAddress.selector);
        token.transferFrom(owner, address(0), 100);
    }

    function test_approveToZeroAddress() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofToken.ZeroAddress.selector);
        token.approve(address(0), 100);
    }

    function test_transferFromDecrementsAllowance() public {
        vm.prank(owner);
        token.approve(alice, 1000);

        vm.prank(alice);
        token.transferFrom(owner, bob, 400);

        assertEq(token.allowance(owner, alice), 600);
    }

    // ---------------------------------------------------------------
    //  Mint
    // ---------------------------------------------------------------

    function test_mintByOwner() public {
        uint256 amount = 5000 * 10 ** 18;

        vm.prank(owner);
        token.mint(alice, amount);

        assertEq(token.balanceOf(alice), amount);
        assertEq(token.totalSupply(), INITIAL_SUPPLY + amount);
    }

    function test_mintEmitsTransferEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, true, false, true);
        emit AgentProofToken.Transfer(address(0), alice, 100);
        token.mint(alice, 100);
    }

    function test_mintByNonOwnerReverts() public {
        vm.prank(outsider);
        vm.expectRevert(AgentProofToken.Unauthorized.selector);
        token.mint(outsider, 100);
    }

    function test_mintToZeroAddress() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofToken.ZeroAddress.selector);
        token.mint(address(0), 100);
    }

    // ---------------------------------------------------------------
    //  Burn
    // ---------------------------------------------------------------

    function test_burnReducesTotalSupply() public {
        uint256 burnAmount = 1000 * 10 ** 18;

        vm.prank(owner);
        token.burn(burnAmount);

        assertEq(token.balanceOf(owner), INITIAL_SUPPLY - burnAmount);
        assertEq(token.totalSupply(), INITIAL_SUPPLY - burnAmount);
    }

    function test_burnEmitsTransferEvent() public {
        uint256 amount = 500 * 10 ** 18;

        vm.prank(owner);
        vm.expectEmit(true, true, false, true);
        emit AgentProofToken.Transfer(owner, address(0), amount);
        token.burn(amount);
    }

    function test_burnInsufficientBalance() public {
        vm.prank(alice);
        vm.expectRevert(AgentProofToken.InsufficientBalance.selector);
        token.burn(1);
    }

    function test_burnEntireBalance() public {
        // Transfer some to alice, then burn all
        uint256 amount = 100 * 10 ** 18;
        vm.prank(owner);
        token.transfer(alice, amount);

        vm.prank(alice);
        token.burn(amount);

        assertEq(token.balanceOf(alice), 0);
        assertEq(token.totalSupply(), INITIAL_SUPPLY - amount);
    }

    // ---------------------------------------------------------------
    //  Ownership
    // ---------------------------------------------------------------

    function test_transferOwnership() public {
        vm.prank(owner);
        token.transferOwnership(alice);
        assertEq(token.owner(), alice);
    }

    function test_transferOwnershipEmitsEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, true, false, false);
        emit AgentProofToken.OwnershipTransferred(owner, alice);
        token.transferOwnership(alice);
    }

    function test_nonOwnerCannotTransferOwnership() public {
        vm.prank(outsider);
        vm.expectRevert(AgentProofToken.Unauthorized.selector);
        token.transferOwnership(outsider);
    }

    function test_cannotTransferOwnershipToZero() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofToken.ZeroAddress.selector);
        token.transferOwnership(address(0));
    }

    // ---------------------------------------------------------------
    //  Deflationary scenario: mint + burn combined
    // ---------------------------------------------------------------

    function test_deflationaryScenario() public {
        // Simulate: owner distributes tokens, recipients burn some as payment
        uint256 dist = 10_000 * 10 ** 18;
        vm.prank(owner);
        token.transfer(alice, dist);

        // Alice burns 10% as a premium payment
        uint256 burnAmt = dist / 10;
        vm.prank(alice);
        token.burn(burnAmt);

        assertEq(token.balanceOf(alice), dist - burnAmt);
        assertEq(token.totalSupply(), INITIAL_SUPPLY - burnAmt);
    }

    // ---------------------------------------------------------------
    //  Gas snapshots
    // ---------------------------------------------------------------

    function test_gasSnapshot_transfer() public {
        vm.prank(owner);
        uint256 gasBefore = gasleft();
        token.transfer(alice, 1000);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: transfer", gasUsed);
    }

    function test_gasSnapshot_burn() public {
        vm.prank(owner);
        uint256 gasBefore = gasleft();
        token.burn(1000);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: burn", gasUsed);
    }
}
