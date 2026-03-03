// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {AgentProofChannel} from "../src/AgentProofChannel.sol";

contract AgentProofChannelTest is Test {

    AgentProofChannel public channel;

    // Test accounts with known private keys for signing
    uint256 constant SENDER_PK   = 0xA11CE;
    uint256 constant RECEIVER_PK = 0xB0B;
    uint256 constant OUTSIDER_PK = 0xBAD;

    address public sender;
    address public receiver;
    address public outsider;

    uint40 constant DURATION = 3600; // 1 hour

    function setUp() public {
        channel = new AgentProofChannel();
        sender   = vm.addr(SENDER_PK);
        receiver = vm.addr(RECEIVER_PK);
        outsider = vm.addr(OUTSIDER_PK);

        // Fund test accounts
        vm.deal(sender, 10 ether);
        vm.deal(receiver, 1 ether);
        vm.deal(outsider, 1 ether);
    }

    // ---------------------------------------------------------------
    //  Helpers
    // ---------------------------------------------------------------

    /// @dev Open a channel from sender to receiver with the given deposit.
    function _openChannel(uint256 deposit) internal returns (bytes32) {
        vm.prank(sender);
        bytes32 channelId = channel.openChannel{value: deposit}(receiver, DURATION);
        return channelId;
    }

    /// @dev Sign a payment state using the EIP-191 format matching the contract.
    function _signPayment(
        bytes32 channelId,
        uint256 amount,
        uint256 nonce,
        uint256 pk
    ) internal pure returns (bytes memory) {
        bytes32 raw = keccak256(abi.encodePacked(channelId, amount, nonce));
        bytes32 msgHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", raw)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, msgHash);
        return abi.encodePacked(r, s, v);
    }

    // ---------------------------------------------------------------
    //  Open channel tests
    // ---------------------------------------------------------------

    function test_openChannelSuccess() public {
        bytes32 channelId = _openChannel(1 ether);

        AgentProofChannel.Channel memory ch = channel.getChannel(channelId);
        assertEq(ch.sender, sender);
        assertEq(ch.receiver, receiver);
        assertEq(ch.deposit, 1 ether);
        assertTrue(ch.isOpen);
        assertGt(ch.expiresAt, uint40(block.timestamp));
    }

    function test_openChannelEmitsEvent() public {
        vm.prank(sender);
        vm.expectEmit(true, true, true, false);
        // We don't know channelId ahead of time, so just check it emits
        bytes32 expectedChannelId = keccak256(
            abi.encodePacked(sender, receiver, block.number)
        );
        uint40 expectedExpiry = uint40(block.timestamp) + DURATION;
        emit AgentProofChannel.ChannelOpened(
            expectedChannelId, sender, receiver, 1 ether, expectedExpiry
        );
        channel.openChannel{value: 1 ether}(receiver, DURATION);
    }

    function test_openChannelRevertsZeroDeposit() public {
        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.InvalidAmount.selector);
        channel.openChannel{value: 0}(receiver, DURATION);
    }

    function test_openChannelRevertsZeroDuration() public {
        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.InvalidDuration.selector);
        channel.openChannel{value: 1 ether}(receiver, 0);
    }

    function test_openChannelRevertsSelfChannel() public {
        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.InvalidAmount.selector);
        channel.openChannel{value: 1 ether}(sender, DURATION);
    }

    function test_openChannelRevertsZeroReceiver() public {
        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.InvalidAmount.selector);
        channel.openChannel{value: 1 ether}(address(0), DURATION);
    }

    // ---------------------------------------------------------------
    //  Cooperative close tests
    // ---------------------------------------------------------------

    function test_cooperativeCloseSuccess() public {
        bytes32 channelId = _openChannel(1 ether);

        uint256 payAmount = 0.3 ether;
        uint256 payNonce  = 5;

        bytes memory senderSig   = _signPayment(channelId, payAmount, payNonce, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, payAmount, payNonce, RECEIVER_PK);

        uint256 senderBalBefore   = sender.balance;
        uint256 receiverBalBefore = receiver.balance;

        vm.prank(sender);
        channel.closeChannel(channelId, payAmount, payNonce, senderSig, receiverSig);

        // Receiver gets payAmount, sender gets deposit - payAmount
        assertEq(receiver.balance, receiverBalBefore + payAmount);
        assertEq(sender.balance, senderBalBefore + (1 ether - payAmount));

        // Channel is now closed
        AgentProofChannel.Channel memory ch = channel.getChannel(channelId);
        assertFalse(ch.isOpen);
    }

    function test_cooperativeCloseZeroAmount() public {
        // Close without any payments — sender gets full refund
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig   = _signPayment(channelId, 0, 0, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, 0, 0, RECEIVER_PK);

        uint256 senderBalBefore = sender.balance;

        vm.prank(receiver);
        channel.closeChannel(channelId, 0, 0, senderSig, receiverSig);

        assertEq(sender.balance, senderBalBefore + 1 ether);
    }

    function test_cooperativeCloseRevertsWrongSenderSig() public {
        bytes32 channelId = _openChannel(1 ether);

        // Sign with outsider's key instead of sender's
        bytes memory badSig      = _signPayment(channelId, 0.5 ether, 1, OUTSIDER_PK);
        bytes memory receiverSig = _signPayment(channelId, 0.5 ether, 1, RECEIVER_PK);

        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.InvalidSignature.selector);
        channel.closeChannel(channelId, 0.5 ether, 1, badSig, receiverSig);
    }

    function test_cooperativeCloseRevertsWrongReceiverSig() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig = _signPayment(channelId, 0.5 ether, 1, SENDER_PK);
        bytes memory badSig    = _signPayment(channelId, 0.5 ether, 1, OUTSIDER_PK);

        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.InvalidSignature.selector);
        channel.closeChannel(channelId, 0.5 ether, 1, senderSig, badSig);
    }

    function test_cooperativeCloseRevertsExceedsDeposit() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig   = _signPayment(channelId, 2 ether, 1, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, 2 ether, 1, RECEIVER_PK);

        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.InvalidAmount.selector);
        channel.closeChannel(channelId, 2 ether, 1, senderSig, receiverSig);
    }

    function test_cooperativeCloseRevertsNonParticipant() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig   = _signPayment(channelId, 0.5 ether, 1, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, 0.5 ether, 1, RECEIVER_PK);

        vm.prank(outsider);
        vm.expectRevert(AgentProofChannel.NotChannelParticipant.selector);
        channel.closeChannel(channelId, 0.5 ether, 1, senderSig, receiverSig);
    }

    function test_cooperativeCloseRevertsAlreadyClosed() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig   = _signPayment(channelId, 0.5 ether, 1, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, 0.5 ether, 1, RECEIVER_PK);

        vm.prank(sender);
        channel.closeChannel(channelId, 0.5 ether, 1, senderSig, receiverSig);

        // Try to close again
        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.ChannelNotOpen.selector);
        channel.closeChannel(channelId, 0.5 ether, 1, senderSig, receiverSig);
    }

    // ---------------------------------------------------------------
    //  Challenge close tests
    // ---------------------------------------------------------------

    function test_challengeCloseSuccess() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig = _signPayment(channelId, 0.4 ether, 3, SENDER_PK);

        vm.prank(receiver);
        channel.challengeClose(channelId, 0.4 ether, 3, senderSig);

        AgentProofChannel.Channel memory ch = channel.getChannel(channelId);
        assertEq(ch.claimedAmount, 0.4 ether);
        assertEq(ch.claimedNonce, 3);
        assertTrue(ch.isOpen); // Still open during challenge period
        assertGt(ch.challengeEnd, 0);
    }

    function test_challengeCanBeSupersededByHigherNonce() public {
        bytes32 channelId = _openChannel(1 ether);

        // First challenge at nonce 3
        bytes memory sig3 = _signPayment(channelId, 0.4 ether, 3, SENDER_PK);
        vm.prank(receiver);
        channel.challengeClose(channelId, 0.4 ether, 3, sig3);

        // Supersede with nonce 5 (higher amount too)
        bytes memory sig5 = _signPayment(channelId, 0.7 ether, 5, SENDER_PK);
        vm.prank(sender);
        channel.challengeClose(channelId, 0.7 ether, 5, sig5);

        AgentProofChannel.Channel memory ch = channel.getChannel(channelId);
        assertEq(ch.claimedAmount, 0.7 ether);
        assertEq(ch.claimedNonce, 5);
    }

    function test_challengeRevertsLowerNonce() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory sig5 = _signPayment(channelId, 0.5 ether, 5, SENDER_PK);
        vm.prank(receiver);
        channel.challengeClose(channelId, 0.5 ether, 5, sig5);

        // Try to challenge with lower nonce
        bytes memory sig3 = _signPayment(channelId, 0.3 ether, 3, SENDER_PK);
        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.HigherNonceRequired.selector);
        channel.challengeClose(channelId, 0.3 ether, 3, sig3);
    }

    function test_finalizeAfterChallengeTimeout() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig = _signPayment(channelId, 0.6 ether, 2, SENDER_PK);
        vm.prank(receiver);
        channel.challengeClose(channelId, 0.6 ether, 2, senderSig);

        // Warp past the challenge period
        vm.warp(block.timestamp + 301);

        uint256 senderBalBefore   = sender.balance;
        uint256 receiverBalBefore = receiver.balance;

        vm.prank(receiver);
        channel.finalizeChallenge(channelId);

        assertEq(receiver.balance, receiverBalBefore + 0.6 ether);
        assertEq(sender.balance, senderBalBefore + 0.4 ether);

        AgentProofChannel.Channel memory ch = channel.getChannel(channelId);
        assertFalse(ch.isOpen);
    }

    function test_finalizeRevertsBeforeTimeout() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig = _signPayment(channelId, 0.5 ether, 1, SENDER_PK);
        vm.prank(receiver);
        channel.challengeClose(channelId, 0.5 ether, 1, senderSig);

        // Don't warp — still within challenge period
        vm.prank(receiver);
        vm.expectRevert(AgentProofChannel.ChallengeNotExpired.selector);
        channel.finalizeChallenge(channelId);
    }

    // ---------------------------------------------------------------
    //  Expiry reclaim tests
    // ---------------------------------------------------------------

    function test_reclaimExpiredChannel() public {
        bytes32 channelId = _openChannel(1 ether);

        // Warp past expiry
        vm.warp(block.timestamp + DURATION + 1);

        uint256 senderBalBefore = sender.balance;

        vm.prank(sender);
        channel.reclaimExpired(channelId);

        assertEq(sender.balance, senderBalBefore + 1 ether);

        AgentProofChannel.Channel memory ch = channel.getChannel(channelId);
        assertFalse(ch.isOpen);
    }

    function test_reclaimRevertsBeforeExpiry() public {
        bytes32 channelId = _openChannel(1 ether);

        vm.prank(sender);
        vm.expectRevert(AgentProofChannel.ChannelNotExpired.selector);
        channel.reclaimExpired(channelId);
    }

    function test_reclaimRevertsNonSender() public {
        bytes32 channelId = _openChannel(1 ether);

        vm.warp(block.timestamp + DURATION + 1);

        vm.prank(receiver);
        vm.expectRevert(AgentProofChannel.NotChannelParticipant.selector);
        channel.reclaimExpired(channelId);
    }

    // ---------------------------------------------------------------
    //  Settlement correctness
    // ---------------------------------------------------------------

    function test_closePaysCorrectAmounts() public {
        bytes32 channelId = _openChannel(2 ether);

        uint256 receiverPay = 1.5 ether;
        bytes memory senderSig   = _signPayment(channelId, receiverPay, 10, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, receiverPay, 10, RECEIVER_PK);

        uint256 senderBefore   = sender.balance;
        uint256 receiverBefore = receiver.balance;

        vm.prank(sender);
        channel.closeChannel(channelId, receiverPay, 10, senderSig, receiverSig);

        // Receiver: +1.5 ETH, Sender: +0.5 ETH (refund)
        assertEq(receiver.balance, receiverBefore + 1.5 ether);
        assertEq(sender.balance, senderBefore + 0.5 ether);
    }

    function test_closeFullDepositToReceiver() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig   = _signPayment(channelId, 1 ether, 1, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, 1 ether, 1, RECEIVER_PK);

        uint256 receiverBefore = receiver.balance;

        vm.prank(receiver);
        channel.closeChannel(channelId, 1 ether, 1, senderSig, receiverSig);

        assertEq(receiver.balance, receiverBefore + 1 ether);
    }

    // ---------------------------------------------------------------
    //  Gas snapshots
    // ---------------------------------------------------------------

    function test_gasSnapshot_openChannel() public {
        vm.prank(sender);
        uint256 gasBefore = gasleft();
        channel.openChannel{value: 1 ether}(receiver, DURATION);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: open channel", gasUsed);
    }

    function test_gasSnapshot_cooperativeClose() public {
        bytes32 channelId = _openChannel(1 ether);

        bytes memory senderSig   = _signPayment(channelId, 0.5 ether, 1, SENDER_PK);
        bytes memory receiverSig = _signPayment(channelId, 0.5 ether, 1, RECEIVER_PK);

        vm.prank(sender);
        uint256 gasBefore = gasleft();
        channel.closeChannel(channelId, 0.5 ether, 1, senderSig, receiverSig);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: cooperative close", gasUsed);
    }
}
