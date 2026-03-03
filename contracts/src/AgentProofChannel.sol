// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AgentProof State Channel
/// @author AgentProof
/// @notice Minimal state channel for micropayments between agent operators
///         and tool/MCP providers. Supports cooperative close and dispute
///         via challenge timeout.
/// @dev Designed for Base L2. Channels lock ETH as deposit; off-chain
///      payment updates are signed by both parties and only the final
///      state is settled on-chain.
contract AgentProofChannel {

    // ---------------------------------------------------------------
    //  Types
    // ---------------------------------------------------------------

    struct Channel {
        address sender;
        address receiver;
        uint256 deposit;
        uint256 claimedAmount;
        uint256 claimedNonce;
        uint40  openedAt;
        uint40  expiresAt;
        uint40  challengeEnd;
        bool    isOpen;
    }

    // ---------------------------------------------------------------
    //  State
    // ---------------------------------------------------------------

    mapping(bytes32 => Channel) public channels;

    /// @dev Settlement delay for challenge period (seconds)
    uint40 public constant CHALLENGE_PERIOD = 300;

    // ---------------------------------------------------------------
    //  Events
    // ---------------------------------------------------------------

    event ChannelOpened(
        bytes32 indexed channelId,
        address indexed sender,
        address indexed receiver,
        uint256 deposit,
        uint40  expiresAt
    );

    event ChannelClosed(
        bytes32 indexed channelId,
        uint256 senderAmount,
        uint256 receiverAmount
    );

    event ChallengeFiled(
        bytes32 indexed channelId,
        uint256 amount,
        uint256 nonce,
        uint40  challengeEnd
    );

    // ---------------------------------------------------------------
    //  Errors
    // ---------------------------------------------------------------

    error ChannelNotFound();
    error ChannelNotOpen();
    error ChannelStillOpen();
    error NotChannelParticipant();
    error InvalidSignature();
    error InvalidAmount();
    error InvalidDuration();
    error ChallengeNotExpired();
    error ChallengeActive();
    error HigherNonceRequired();
    error TransferFailed();
    error ChannelExpired();
    error ChannelNotExpired();

    // ---------------------------------------------------------------
    //  Open
    // ---------------------------------------------------------------

    /// @notice Open a payment channel, locking msg.value as the deposit.
    /// @param receiver The address that will receive payments.
    /// @param duration Channel lifetime in seconds.
    /// @return channelId The unique identifier for this channel.
    function openChannel(
        address receiver,
        uint40 duration
    ) external payable returns (bytes32 channelId) {
        if (msg.value == 0) revert InvalidAmount();
        if (duration == 0) revert InvalidDuration();
        if (receiver == address(0) || receiver == msg.sender) revert InvalidAmount();

        channelId = keccak256(
            abi.encodePacked(msg.sender, receiver, block.number)
        );

        // Prevent collisions (extremely unlikely with block.number)
        if (channels[channelId].openedAt != 0) revert InvalidAmount();

        uint40 expiresAt = uint40(block.timestamp) + duration;

        channels[channelId] = Channel({
            sender: msg.sender,
            receiver: receiver,
            deposit: msg.value,
            claimedAmount: 0,
            claimedNonce: 0,
            openedAt: uint40(block.timestamp),
            expiresAt: expiresAt,
            challengeEnd: 0,
            isOpen: true
        });

        emit ChannelOpened(channelId, msg.sender, receiver, msg.value, expiresAt);
    }

    // ---------------------------------------------------------------
    //  Cooperative Close
    // ---------------------------------------------------------------

    /// @notice Close a channel cooperatively with both signatures.
    /// @param channelId The channel to close.
    /// @param amount    The final amount owed to the receiver (in wei).
    /// @param nonce     The final payment nonce.
    /// @param senderSig Sender's signature over (channelId, amount, nonce).
    /// @param receiverSig Receiver's signature over (channelId, amount, nonce).
    function closeChannel(
        bytes32 channelId,
        uint256 amount,
        uint256 nonce,
        bytes memory senderSig,
        bytes memory receiverSig
    ) external {
        Channel storage ch = channels[channelId];
        if (!ch.isOpen) revert ChannelNotOpen();

        // Only participants can close
        if (msg.sender != ch.sender && msg.sender != ch.receiver) {
            revert NotChannelParticipant();
        }
        if (amount > ch.deposit) revert InvalidAmount();

        bytes32 msgHash = _paymentHash(channelId, amount, nonce);

        if (_recoverSigner(msgHash, senderSig) != ch.sender) revert InvalidSignature();
        if (_recoverSigner(msgHash, receiverSig) != ch.receiver) revert InvalidSignature();

        _settle(channelId, amount);
    }

    // ---------------------------------------------------------------
    //  Challenge Close (dispute mechanism)
    // ---------------------------------------------------------------

    /// @notice File a challenge to close the channel unilaterally.
    ///         Starts a timeout; if unchallenged, the channel settles
    ///         at the claimed amount after CHALLENGE_PERIOD.
    /// @param channelId The channel to challenge.
    /// @param amount    The claimed final amount owed to receiver.
    /// @param nonce     The payment nonce for this state.
    /// @param senderSig Sender's signature proving this state was agreed.
    function challengeClose(
        bytes32 channelId,
        uint256 amount,
        uint256 nonce,
        bytes memory senderSig
    ) external {
        Channel storage ch = channels[channelId];
        if (!ch.isOpen) revert ChannelNotOpen();
        if (msg.sender != ch.sender && msg.sender != ch.receiver) {
            revert NotChannelParticipant();
        }
        if (amount > ch.deposit) revert InvalidAmount();

        // If there's already a challenge, the new nonce must be higher
        if (ch.challengeEnd != 0 && nonce <= ch.claimedNonce) {
            revert HigherNonceRequired();
        }

        bytes32 msgHash = _paymentHash(channelId, amount, nonce);
        if (_recoverSigner(msgHash, senderSig) != ch.sender) revert InvalidSignature();

        ch.claimedAmount = amount;
        ch.claimedNonce = nonce;
        ch.challengeEnd = uint40(block.timestamp) + CHALLENGE_PERIOD;

        emit ChallengeFiled(channelId, amount, nonce, ch.challengeEnd);
    }

    /// @notice Finalize a challenged close after the challenge period expires.
    /// @param channelId The channel to finalize.
    function finalizeChallenge(bytes32 channelId) external {
        Channel storage ch = channels[channelId];
        if (!ch.isOpen) revert ChannelNotOpen();
        if (ch.challengeEnd == 0) revert ChannelNotFound();
        if (block.timestamp < ch.challengeEnd) revert ChallengeNotExpired();

        _settle(channelId, ch.claimedAmount);
    }

    // ---------------------------------------------------------------
    //  Expiry Reclaim
    // ---------------------------------------------------------------

    /// @notice Reclaim deposit from an expired channel that was never closed.
    /// @param channelId The expired channel to reclaim.
    function reclaimExpired(bytes32 channelId) external {
        Channel storage ch = channels[channelId];
        if (!ch.isOpen) revert ChannelNotOpen();
        if (block.timestamp < ch.expiresAt) revert ChannelNotExpired();
        if (msg.sender != ch.sender) revert NotChannelParticipant();

        // Return full deposit to sender — no payments were settled
        uint256 deposit = ch.deposit;
        ch.isOpen = false;
        ch.deposit = 0;

        (bool ok, ) = payable(ch.sender).call{value: deposit}("");
        if (!ok) revert TransferFailed();

        emit ChannelClosed(channelId, deposit, 0);
    }

    // ---------------------------------------------------------------
    //  Read
    // ---------------------------------------------------------------

    /// @notice Get channel state.
    /// @param channelId The channel to query.
    /// @return The Channel struct.
    function getChannel(bytes32 channelId) external view returns (Channel memory) {
        return channels[channelId];
    }

    // ---------------------------------------------------------------
    //  Internal
    // ---------------------------------------------------------------

    /// @dev Settle a channel: pay receiver their amount, refund sender the rest.
    function _settle(bytes32 channelId, uint256 receiverAmount) internal {
        Channel storage ch = channels[channelId];
        uint256 senderRefund = ch.deposit - receiverAmount;

        ch.isOpen = false;
        ch.deposit = 0;

        // Pay receiver
        if (receiverAmount > 0) {
            (bool ok1, ) = payable(ch.receiver).call{value: receiverAmount}("");
            if (!ok1) revert TransferFailed();
        }

        // Refund sender
        if (senderRefund > 0) {
            (bool ok2, ) = payable(ch.sender).call{value: senderRefund}("");
            if (!ok2) revert TransferFailed();
        }

        emit ChannelClosed(channelId, senderRefund, receiverAmount);
    }

    /// @dev Compute the EIP-191 signed message hash for a payment state.
    function _paymentHash(
        bytes32 channelId,
        uint256 amount,
        uint256 nonce
    ) internal pure returns (bytes32) {
        bytes32 raw = keccak256(abi.encodePacked(channelId, amount, nonce));
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", raw));
    }

    /// @dev Recover the signer address from a 65-byte ECDSA signature.
    function _recoverSigner(
        bytes32 msgHash,
        bytes memory sig
    ) internal pure returns (address) {
        if (sig.length != 65) revert InvalidSignature();

        bytes32 r;
        bytes32 s;
        uint8 v;

        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }

        if (v < 27) v += 27;

        return ecrecover(msgHash, v, r, s);
    }
}
