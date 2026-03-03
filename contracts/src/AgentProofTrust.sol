// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AgentProof Trust Score Registry
/// @author AgentProof
/// @notice On-chain trust scores for AI agents and MCP servers.
///         Scores stored as uint16 (0-10000) representing 0.0000-1.0000
///         for gas efficiency. Off-chain systems compute scores; this
///         contract is the canonical public record.
/// @dev Designed for Base L2. Authorized updaters push scores on-chain.
///      Top-agent queries should use off-chain indexing due to gas cost
///      of on-chain sorting.
contract AgentProofTrust {

    // ---------------------------------------------------------------
    //  Types
    // ---------------------------------------------------------------

    struct TrustScore {
        uint16 composite;    // 0-10000 -> 0.0000-1.0000
        uint16 reliability;
        uint16 efficiency;
        uint16 quality;
        uint16 usage;
        uint40 lastUpdated;
    }

    // ---------------------------------------------------------------
    //  State
    // ---------------------------------------------------------------

    /// @dev agentIdHash -> TrustScore
    mapping(bytes32 => TrustScore) public scores;

    /// @dev Track all registered agents for enumeration
    bytes32[] public agentIds;
    mapping(bytes32 => bool) public isRegistered;

    /// @dev Addresses authorized to update scores
    mapping(address => bool) public updaters;

    /// @dev Contract owner -- can grant/revoke updater roles
    address public owner;

    // ---------------------------------------------------------------
    //  Events
    // ---------------------------------------------------------------

    event ScoreUpdated(
        bytes32 indexed agentIdHash,
        uint16 composite,
        uint16 reliability,
        uint16 efficiency,
        uint16 quality,
        uint16 usage
    );

    event AgentRegistered(bytes32 indexed agentIdHash);
    event UpdaterGranted(address indexed updater);
    event UpdaterRevoked(address indexed updater);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    // ---------------------------------------------------------------
    //  Errors
    // ---------------------------------------------------------------

    error Unauthorized();
    error ZeroAddress();
    error InvalidScore();
    error AgentNotRegistered();
    error AgentAlreadyRegistered();
    error LimitExceedsAgentCount();

    // ---------------------------------------------------------------
    //  Modifiers
    // ---------------------------------------------------------------

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyUpdater() {
        if (!updaters[msg.sender]) revert Unauthorized();
        _;
    }

    // ---------------------------------------------------------------
    //  Constructor
    // ---------------------------------------------------------------

    /// @param initialOwner The address that owns this contract and
    ///        receives the first updater grant.
    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        updaters[initialOwner] = true;
        emit UpdaterGranted(initialOwner);
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    /// @notice Grant score update rights to an address.
    function grantUpdater(address updater) external onlyOwner {
        if (updater == address(0)) revert ZeroAddress();
        updaters[updater] = true;
        emit UpdaterGranted(updater);
    }

    /// @notice Revoke score update rights from an address.
    function revokeUpdater(address updater) external onlyOwner {
        updaters[updater] = false;
        emit UpdaterRevoked(updater);
    }

    /// @notice Transfer contract ownership.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    // ---------------------------------------------------------------
    //  Core: Register & Update
    // ---------------------------------------------------------------

    /// @notice Register a new agent with neutral scores (5000 = 0.5).
    /// @param agentIdHash Keccak-256 hash of the agent identifier.
    function registerAgent(bytes32 agentIdHash) external onlyUpdater {
        if (isRegistered[agentIdHash]) revert AgentAlreadyRegistered();

        isRegistered[agentIdHash] = true;
        agentIds.push(agentIdHash);

        scores[agentIdHash] = TrustScore({
            composite: 5000,
            reliability: 5000,
            efficiency: 5000,
            quality: 5000,
            usage: 5000,
            lastUpdated: uint40(block.timestamp)
        });

        emit AgentRegistered(agentIdHash);
        emit ScoreUpdated(agentIdHash, 5000, 5000, 5000, 5000, 5000);
    }

    /// @notice Update an agent's trust scores. All dimension values must
    ///         be in [0, 10000]. Composite is provided by the off-chain
    ///         calculator (weighted sum).
    /// @param agentIdHash  The agent's pseudonymous identifier hash.
    /// @param composite    Weighted composite score (0-10000).
    /// @param reliability  Reliability dimension (0-10000).
    /// @param efficiency   Efficiency dimension (0-10000).
    /// @param quality      Quality dimension (0-10000).
    /// @param usage        Usage volume dimension (0-10000).
    function updateScore(
        bytes32 agentIdHash,
        uint16 composite,
        uint16 reliability,
        uint16 efficiency,
        uint16 quality,
        uint16 usage
    ) external onlyUpdater {
        if (!isRegistered[agentIdHash]) revert AgentNotRegistered();
        if (composite > 10000 || reliability > 10000 ||
            efficiency > 10000 || quality > 10000 || usage > 10000) {
            revert InvalidScore();
        }

        scores[agentIdHash] = TrustScore({
            composite: composite,
            reliability: reliability,
            efficiency: efficiency,
            quality: quality,
            usage: usage,
            lastUpdated: uint40(block.timestamp)
        });

        emit ScoreUpdated(agentIdHash, composite, reliability, efficiency, quality, usage);
    }

    // ---------------------------------------------------------------
    //  Read
    // ---------------------------------------------------------------

    /// @notice Retrieve an agent's trust scores.
    /// @param agentIdHash The agent's pseudonymous identifier hash.
    /// @return The TrustScore struct.
    function getScore(bytes32 agentIdHash) external view returns (TrustScore memory) {
        if (!isRegistered[agentIdHash]) revert AgentNotRegistered();
        return scores[agentIdHash];
    }

    /// @notice Get the total number of registered agents.
    /// @return The count of registered agents.
    function agentCount() external view returns (uint256) {
        return agentIds.length;
    }

    /// @notice Get top agents by composite score.
    /// @dev WARNING: O(n*limit) on-chain. For production use, prefer off-chain
    ///      indexing via events. This is provided for convenience on small sets.
    /// @param limit Maximum number of agents to return.
    /// @return topAgents Array of agent ID hashes, sorted by composite desc.
    /// @return topScores Array of composite scores corresponding to topAgents.
    function getTopAgents(uint256 limit)
        external
        view
        returns (bytes32[] memory topAgents, uint16[] memory topScores)
    {
        uint256 total = agentIds.length;
        if (limit > total) limit = total;

        topAgents = new bytes32[](limit);
        topScores = new uint16[](limit);

        // Track which indices have been picked to avoid duplicates
        bool[] memory picked = new bool[](total);

        for (uint256 i = 0; i < limit; i++) {
            uint16 bestScore = 0;
            uint256 bestIdx = 0;
            bool found = false;

            for (uint256 j = 0; j < total; j++) {
                if (picked[j]) continue;

                uint16 s = scores[agentIds[j]].composite;
                if (!found || s > bestScore) {
                    bestScore = s;
                    bestIdx = j;
                    found = true;
                }
            }

            picked[bestIdx] = true;
            topAgents[i] = agentIds[bestIdx];
            topScores[i] = bestScore;
        }
    }
}
