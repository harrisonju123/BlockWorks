// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AgentProofToken} from "./AgentProofToken.sol";

/// @title AgentProof Revenue Sharing
/// @author AgentProof
/// @notice On-chain settlement for multi-agent workflow revenue splitting.
///         Distributes ERC-20 tokens to participants, collects a protocol fee
///         for the treasury, and burns a portion for deflationary pressure.
/// @dev Requires token approval from the payer before calling settle().
///      Settlement is idempotent per executionId — cannot settle twice.
contract AgentProofRevenue {

    // ---------------------------------------------------------------
    //  State
    // ---------------------------------------------------------------

    AgentProofToken public immutable token;
    address public treasury;
    address public owner;

    /// @dev Dead address for burns (tokens sent here are permanently removed from circulation)
    address public constant BURN_ADDRESS = address(0xdead);

    /// @dev Tracks which execution IDs have been settled to prevent double-settlement
    mapping(bytes32 => bool) public settled;

    /// @dev Cumulative earnings per participant address
    mapping(address => uint256) public earnings;

    /// @dev Protocol-level running totals
    uint256 public totalProtocolRevenue;
    uint256 public totalBurned;
    uint256 public totalSettlements;

    // ---------------------------------------------------------------
    //  Events
    // ---------------------------------------------------------------

    event Settled(
        bytes32 indexed executionId,
        uint256 totalAmount,
        uint256 protocolFee,
        uint256 burnAmount,
        uint256 participantCount
    );

    event EarningsUpdated(
        address indexed participant,
        uint256 amount,
        uint256 cumulative
    );

    event TreasuryUpdated(address indexed oldTreasury, address indexed newTreasury);

    // ---------------------------------------------------------------
    //  Errors
    // ---------------------------------------------------------------

    error Unauthorized();
    error AlreadySettled();
    error ZeroAddress();
    error ArrayLengthMismatch();
    error EmptyParticipants();
    error InvalidAmount();
    error TransferFailed();

    // ---------------------------------------------------------------
    //  Modifiers
    // ---------------------------------------------------------------

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    // ---------------------------------------------------------------
    //  Constructor
    // ---------------------------------------------------------------

    /// @param _token   The AgentProofToken used for payments.
    /// @param _treasury Address that receives protocol fees (net of burns).
    constructor(AgentProofToken _token, address _treasury) {
        if (address(_token) == address(0)) revert ZeroAddress();
        if (_treasury == address(0)) revert ZeroAddress();

        token = _token;
        treasury = _treasury;
        owner = msg.sender;
    }

    // ---------------------------------------------------------------
    //  Settlement
    // ---------------------------------------------------------------

    /// @notice Settle a workflow execution by distributing tokens.
    /// @dev Caller must have approved this contract to spend
    ///      (sum(amounts) + protocolFee) tokens beforehand.
    /// @param executionId  Unique identifier for the workflow execution.
    /// @param participants Addresses of revenue share recipients.
    /// @param amounts      Token amounts for each participant (in wei).
    /// @param protocolFee  Total protocol fee amount (in wei).
    /// @param burnAmount   Portion of protocolFee to burn (in wei).
    function settle(
        bytes32 executionId,
        address[] calldata participants,
        uint256[] calldata amounts,
        uint256 protocolFee,
        uint256 burnAmount
    ) external {
        if (settled[executionId]) revert AlreadySettled();
        if (participants.length == 0) revert EmptyParticipants();
        if (participants.length != amounts.length) revert ArrayLengthMismatch();
        if (burnAmount > protocolFee) revert InvalidAmount();

        settled[executionId] = true;

        uint256 totalDistributed = 0;

        // Distribute to participants
        for (uint256 i = 0; i < participants.length; i++) {
            if (participants[i] == address(0)) revert ZeroAddress();
            if (amounts[i] == 0) continue;

            bool ok = token.transferFrom(msg.sender, participants[i], amounts[i]);
            if (!ok) revert TransferFailed();

            earnings[participants[i]] += amounts[i];
            totalDistributed += amounts[i];

            emit EarningsUpdated(
                participants[i],
                amounts[i],
                earnings[participants[i]]
            );
        }

        // Protocol fee: treasury portion
        uint256 treasuryAmount = protocolFee - burnAmount;
        if (treasuryAmount > 0) {
            bool ok = token.transferFrom(msg.sender, treasury, treasuryAmount);
            if (!ok) revert TransferFailed();
        }

        // Burn portion — send to dead address (token.burn would require
        // this contract to hold tokens; transferring to BURN_ADDRESS is simpler)
        if (burnAmount > 0) {
            bool ok = token.transferFrom(msg.sender, BURN_ADDRESS, burnAmount);
            if (!ok) revert TransferFailed();
        }

        totalProtocolRevenue += protocolFee;
        totalBurned += burnAmount;
        totalSettlements += 1;

        emit Settled(
            executionId,
            totalDistributed + protocolFee,
            protocolFee,
            burnAmount,
            participants.length
        );
    }

    // ---------------------------------------------------------------
    //  Read
    // ---------------------------------------------------------------

    /// @notice Get cumulative earnings for a participant.
    /// @param participant The address to query.
    /// @return The total tokens earned across all settlements.
    function getEarnings(address participant) external view returns (uint256) {
        return earnings[participant];
    }

    /// @notice Get total protocol revenue collected.
    /// @return Cumulative protocol fees in token wei.
    function getProtocolRevenue() external view returns (uint256) {
        return totalProtocolRevenue;
    }

    /// @notice Get total tokens burned.
    /// @return Cumulative burned amount in token wei.
    function getBurnedTotal() external view returns (uint256) {
        return totalBurned;
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    /// @notice Update the treasury address.
    /// @param newTreasury The new treasury address.
    function setTreasury(address newTreasury) external onlyOwner {
        if (newTreasury == address(0)) revert ZeroAddress();
        emit TreasuryUpdated(treasury, newTreasury);
        treasury = newTreasury;
    }

    /// @notice Transfer contract ownership.
    /// @param newOwner The new owner address.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        owner = newOwner;
    }
}
