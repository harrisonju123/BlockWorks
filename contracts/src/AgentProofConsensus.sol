// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AgentProofStaking} from "./AgentProofStaking.sol";
import {AgentProofAttestation} from "./AgentProofAttestation.sol";

/// @title AgentProofConsensus — multi-validator proposal, vote, finalize, challenge
/// @notice Ties staking and attestation into a decentralized consensus protocol.
///         Proposals accumulate stake-weighted votes; finalization requires 2/3
///         supermajority + minimum quorum. Anyone can challenge finalized proposals
///         with a bond and Merkle inclusion proof; resolution triggers slashing.
contract AgentProofConsensus {
    // ── Structs ──────────────────────────────────────────────────────────

    struct Proposal {
        bytes32 orgIdHash;
        uint40  periodStart;
        uint40  periodEnd;
        bytes32 metricsHash;
        bytes32 benchmarkHash;
        bytes32 merkleRoot;
        bytes32 prevHash;
        uint64  attestNonce;
        address proposer;
        uint40  createdAt;
        uint256 totalParticipatingStake;
        uint256 yesStake;
        bool    finalized;
        bool    slashed;
    }

    struct Challenge {
        uint256 proposalId;
        address challenger;
        uint256 bond;
        bytes32 disputedLeafHash;
        uint40  filedAt;
        uint40  responseDeadline;
        bool    resolved;
        bool    challengerWon;
    }

    // ── Immutable references ─────────────────────────────────────────────

    AgentProofStaking    public immutable staking;
    AgentProofAttestation public immutable attestation;

    // ── Config (owner-tunable) ───────────────────────────────────────────

    address public owner;
    uint256 public minQuorum      = 3;
    uint256 public slashPercentBps = 500;       // 5%
    uint256 public challengeBondMin = 0.01 ether;

    // ── Constants ────────────────────────────────────────────────────────

    uint256 public constant SUPERMAJORITY_BPS = 6667;   // 2/3 in bps
    uint256 public constant CHALLENGE_PERIOD  = 600;    // 10 min
    uint256 public constant PROPOSAL_TTL      = 86400;  // 24h

    // ── State ────────────────────────────────────────────────────────────

    uint256 public proposalCount;
    mapping(uint256 => Proposal)  public proposals;

    // Track voters per proposal for slashing reference
    mapping(uint256 => address[]) internal _voters;
    mapping(uint256 => mapping(address => bool)) internal _hasVoted;
    // Track which direction each voter chose (true = yes)
    mapping(uint256 => mapping(address => bool)) internal _votedYes;

    // One active proposal per (orgIdHash, nonce) to prevent competing proposals
    mapping(bytes32 => mapping(uint64 => uint256)) public proposalByOrgNonce;

    uint256 public challengeCount;
    mapping(uint256 => Challenge) public challenges;
    // Only one active (unresolved) challenge per proposal
    mapping(uint256 => uint256) public activeChallengeByProposal;

    // ── Events ───────────────────────────────────────────────────────────

    event ProposalCreated(
        uint256 indexed proposalId,
        bytes32 indexed orgIdHash,
        uint64 attestNonce,
        address proposer
    );
    event Voted(
        uint256 indexed proposalId,
        address indexed voter,
        bool inFavor,
        uint256 stake
    );
    event ProposalFinalized(uint256 indexed proposalId, bytes32 indexed orgIdHash, uint64 attestNonce);
    event ProposalExpired(uint256 indexed proposalId);
    event ChallengeCreated(uint256 indexed challengeId, uint256 indexed proposalId, address challenger);
    event ChallengeResolved(uint256 indexed challengeId, bool challengerWon);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event MinQuorumUpdated(uint256 oldQuorum, uint256 newQuorum);
    event SlashPercentUpdated(uint256 oldBps, uint256 newBps);
    event ChallengeBondMinUpdated(uint256 oldBond, uint256 newBond);

    // ── Errors ───────────────────────────────────────────────────────────

    error Unauthorized();
    error NotValidator();
    error ProposalSlotTaken(bytes32 orgIdHash, uint64 nonce);
    error AlreadyVoted();
    error ProposalTTLExpired();
    error ProposalNotExpired();
    error ProposalNotFinalized();
    error ProposalAlreadyFinalized();
    error ProposalAlreadySlashed();
    error QuorumNotMet(uint256 voterCount, uint256 required);
    error SupermajorityNotMet(uint256 yesStake, uint256 totalStake);
    error InsufficientBond(uint256 sent, uint256 required);
    error InvalidMerkleProof();
    error ChallengeAlreadyActive(uint256 existingChallengeId);
    error ChallengeAlreadyResolved();
    error ChallengeNotFound();
    error ProposalNotFound();
    error ZeroAddress();
    error TransferFailed();

    // ── Modifiers ────────────────────────────────────────────────────────

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyValidator() {
        if (!staking.isValidator(msg.sender)) revert NotValidator();
        _;
    }

    // ── Constructor ──────────────────────────────────────────────────────

    constructor(
        AgentProofStaking _staking,
        AgentProofAttestation _attestation,
        address _owner
    ) {
        if (address(_staking) == address(0)) revert ZeroAddress();
        if (address(_attestation) == address(0)) revert ZeroAddress();
        if (_owner == address(0)) revert ZeroAddress();

        staking = _staking;
        attestation = _attestation;
        owner = _owner;

        emit OwnershipTransferred(address(0), _owner);
    }

    // ── Core: Propose ────────────────────────────────────────────────────

    /// @notice Create a proposal and auto-vote yes for the proposer.
    function propose(
        bytes32 orgIdHash,
        uint40  periodStart,
        uint40  periodEnd,
        bytes32 metricsHash,
        bytes32 benchmarkHash,
        bytes32 merkleRoot,
        bytes32 prevHash,
        uint64  attestNonce
    ) external onlyValidator returns (uint256 proposalId) {
        // Prevent competing proposals for the same (org, nonce) slot
        if (proposalByOrgNonce[orgIdHash][attestNonce] != 0) {
            revert ProposalSlotTaken(orgIdHash, attestNonce);
        }

        proposalCount++;
        proposalId = proposalCount;

        Proposal storage p = proposals[proposalId];
        p.orgIdHash     = orgIdHash;
        p.periodStart   = periodStart;
        p.periodEnd     = periodEnd;
        p.metricsHash   = metricsHash;
        p.benchmarkHash = benchmarkHash;
        p.merkleRoot    = merkleRoot;
        p.prevHash      = prevHash;
        p.attestNonce   = attestNonce;
        p.proposer      = msg.sender;
        p.createdAt     = uint40(block.timestamp);

        proposalByOrgNonce[orgIdHash][attestNonce] = proposalId;

        emit ProposalCreated(proposalId, orgIdHash, attestNonce, msg.sender);

        // Auto-vote yes for the proposer (saves 1 txn)
        _vote(proposalId, msg.sender, true);
    }

    // ── Core: Vote ───────────────────────────────────────────────────────

    /// @notice Vote on an open proposal. Stake is read at vote time.
    function vote(uint256 proposalId, bool inFavor) external onlyValidator {
        _vote(proposalId, msg.sender, inFavor);
    }

    function _vote(uint256 proposalId, address voter, bool inFavor) internal {
        Proposal storage p = proposals[proposalId];
        if (p.createdAt == 0) revert ProposalNotFound();
        if (p.finalized) revert ProposalAlreadyFinalized();
        if (block.timestamp > p.createdAt + PROPOSAL_TTL) revert ProposalTTLExpired();
        if (_hasVoted[proposalId][voter]) revert AlreadyVoted();

        uint256 voterStake = staking.getStake(voter);

        _hasVoted[proposalId][voter] = true;
        _votedYes[proposalId][voter] = inFavor;
        _voters[proposalId].push(voter);

        p.totalParticipatingStake += voterStake;
        if (inFavor) {
            p.yesStake += voterStake;
        }

        emit Voted(proposalId, voter, inFavor, voterStake);
    }

    // ── Core: Finalize ───────────────────────────────────────────────────

    /// @notice Finalize a proposal that has reached supermajority + quorum.
    ///         Writes through to the Attestation contract.
    function finalize(uint256 proposalId) external {
        Proposal storage p = proposals[proposalId];
        if (p.createdAt == 0) revert ProposalNotFound();
        if (p.finalized) revert ProposalAlreadyFinalized();

        uint256 voterCount = _voters[proposalId].length;
        if (voterCount < minQuorum) {
            revert QuorumNotMet(voterCount, minQuorum);
        }

        // yesStake * 10000 >= totalParticipatingStake * SUPERMAJORITY_BPS
        if (p.yesStake * 10000 < p.totalParticipatingStake * SUPERMAJORITY_BPS) {
            revert SupermajorityNotMet(p.yesStake, p.totalParticipatingStake);
        }

        p.finalized = true;

        // Write through to the Attestation contract
        attestation.attest(
            p.orgIdHash,
            p.periodStart,
            p.periodEnd,
            p.metricsHash,
            p.benchmarkHash,
            p.merkleRoot,
            p.prevHash
        );

        emit ProposalFinalized(proposalId, p.orgIdHash, p.attestNonce);
    }

    // ── Core: Challenge ──────────────────────────────────────────────────

    /// @notice File a challenge against a finalized proposal.
    ///         Requires a bond and a valid Merkle inclusion proof showing the
    ///         disputed leaf IS in the tree (challenger argues the data is wrong).
    function challengeAttestation(
        uint256 proposalId,
        bytes32 leafHash,
        bytes32[] calldata proof,
        bytes calldata /* evidence — stored off-chain, emitted via event */
    ) external payable returns (uint256 challengeId) {
        Proposal storage p = proposals[proposalId];
        if (p.createdAt == 0) revert ProposalNotFound();
        if (!p.finalized) revert ProposalNotFinalized();
        if (p.slashed) revert ProposalAlreadySlashed();

        if (msg.value < challengeBondMin) {
            revert InsufficientBond(msg.value, challengeBondMin);
        }

        // Only one active challenge per proposal at a time
        uint256 existing = activeChallengeByProposal[proposalId];
        if (existing != 0 && !challenges[existing].resolved) {
            revert ChallengeAlreadyActive(existing);
        }

        // Verify Merkle inclusion — the leaf must actually be in the tree
        if (!_verifyMerkleProof(leafHash, proof, p.merkleRoot)) {
            revert InvalidMerkleProof();
        }

        challengeCount++;
        challengeId = challengeCount;

        Challenge storage c = challenges[challengeId];
        c.proposalId        = proposalId;
        c.challenger        = msg.sender;
        c.bond              = msg.value;
        c.disputedLeafHash  = leafHash;
        c.filedAt           = uint40(block.timestamp);
        c.responseDeadline  = uint40(block.timestamp + CHALLENGE_PERIOD);

        activeChallengeByProposal[proposalId] = challengeId;

        emit ChallengeCreated(challengeId, proposalId, msg.sender);
    }

    // ── Core: Resolve ────────────────────────────────────────────────────

    /// @notice Resolve a challenge. Owner-gated in V1 (future: decentralized panel).
    ///         If challenger wins: slash all yes-voters, return bond + 50% of slash.
    ///         If challenger loses: bond forfeited.
    function resolveChallenge(uint256 challengeId, bool challengerWins) external onlyOwner {
        Challenge storage c = challenges[challengeId];
        if (c.filedAt == 0) revert ChallengeNotFound();
        if (c.resolved) revert ChallengeAlreadyResolved();

        c.resolved = true;
        c.challengerWon = challengerWins;

        Proposal storage p = proposals[c.proposalId];

        if (challengerWins) {
            p.slashed = true;

            // Slash all yes-voters
            uint256 totalSlashed = 0;
            address[] storage voters = _voters[c.proposalId];
            for (uint256 i = 0; i < voters.length; i++) {
                if (_votedYes[c.proposalId][voters[i]]) {
                    uint256 voterStake = staking.getStake(voters[i]);
                    uint256 slashAmt = (voterStake * slashPercentBps) / 10000;
                    if (slashAmt > 0) {
                        staking.slash(voters[i], slashAmt, "Consensus challenge lost");
                        totalSlashed += slashAmt;
                    }
                }
            }

            // Return bond + 50% of total slash proceeds to challenger
            uint256 reward = c.bond + (totalSlashed / 2);
            (bool ok, ) = c.challenger.call{value: reward}("");
            if (!ok) revert TransferFailed();
        }
        // If challenger loses: bond stays in contract (forfeited)

        emit ChallengeResolved(challengeId, challengerWins);
    }

    // ── Expire ───────────────────────────────────────────────────────────

    /// @notice Free the (orgIdHash, nonce) slot after TTL expires without finalization.
    function expireProposal(uint256 proposalId) external {
        Proposal storage p = proposals[proposalId];
        if (p.createdAt == 0) revert ProposalNotFound();
        if (p.finalized) revert ProposalAlreadyFinalized();
        if (block.timestamp <= p.createdAt + PROPOSAL_TTL) revert ProposalNotExpired();

        // Free the slot so a new proposal can be made for this (org, nonce)
        delete proposalByOrgNonce[p.orgIdHash][p.attestNonce];

        emit ProposalExpired(proposalId);
    }

    // ── Merkle verification ──────────────────────────────────────────────

    /// @notice SHA-256 sorted-pair Merkle proof verification.
    ///         Matches the Python MerkleTree in attestation/merkle.py.
    function _verifyMerkleProof(
        bytes32 leafHash,
        bytes32[] calldata proof,
        bytes32 root
    ) internal pure returns (bool) {
        bytes32 current = leafHash;

        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 sibling = proof[i];

            // Sorted-pair: always hash min first (deterministic ordering)
            if (current < sibling) {
                current = sha256(abi.encodePacked(current, sibling));
            } else {
                current = sha256(abi.encodePacked(sibling, current));
            }
        }

        return current == root;
    }

    // ── View helpers ─────────────────────────────────────────────────────

    function getVoters(uint256 proposalId) external view returns (address[] memory) {
        return _voters[proposalId];
    }

    function getVoterCount(uint256 proposalId) external view returns (uint256) {
        return _voters[proposalId].length;
    }

    function hasVoted(uint256 proposalId, address voter) external view returns (bool) {
        return _hasVoted[proposalId][voter];
    }

    function votedYes(uint256 proposalId, address voter) external view returns (bool) {
        return _votedYes[proposalId][voter];
    }

    // ── Admin ────────────────────────────────────────────────────────────

    error InvalidQuorum();
    error InvalidSlashPercent();

    function setMinQuorum(uint256 _minQuorum) external onlyOwner {
        if (_minQuorum == 0) revert InvalidQuorum();
        emit MinQuorumUpdated(minQuorum, _minQuorum);
        minQuorum = _minQuorum;
    }

    function setSlashPercentBps(uint256 _bps) external onlyOwner {
        if (_bps > 10000) revert InvalidSlashPercent();
        emit SlashPercentUpdated(slashPercentBps, _bps);
        slashPercentBps = _bps;
    }

    function setChallengeBondMin(uint256 _bond) external onlyOwner {
        emit ChallengeBondMinUpdated(challengeBondMin, _bond);
        challengeBondMin = _bond;
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    // Allow contract to receive ETH (for forfeited bonds)
    receive() external payable {}
}
