// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AgentProof Attestation Registry
/// @author AgentProof
/// @notice Stores cryptographic commitments to off-chain AI operations data.
///         No raw data touches the chain -- only hashes and Merkle roots.
/// @dev Designed for Base L2. Struct packing: uint40+uint40+uint64 fit one slot.
contract AgentProofAttestation {

    // ---------------------------------------------------------------
    //  Types
    // ---------------------------------------------------------------

    struct Attestation {
        bytes32 orgIdHash;
        uint40  periodStart;
        uint40  periodEnd;
        bytes32 metricsHash;
        bytes32 benchmarkHash;
        bytes32 merkleRoot;
        bytes32 prevHash;
        uint64  nonce;
        uint40  timestamp;
    }

    /// @dev Input struct for batchAttest to keep calldata clean.
    struct AttestInput {
        bytes32 orgIdHash;
        uint40  periodStart;
        uint40  periodEnd;
        bytes32 metricsHash;
        bytes32 benchmarkHash;
        bytes32 merkleRoot;
        bytes32 prevHash;
    }

    // ---------------------------------------------------------------
    //  State
    // ---------------------------------------------------------------

    /// @dev orgIdHash -> nonce -> Attestation
    mapping(bytes32 => mapping(uint64 => Attestation)) public attestations;

    /// @dev orgIdHash -> latest nonce (0 means no attestations yet)
    mapping(bytes32 => uint64) public latestNonce;

    /// @dev Addresses authorized to submit attestations
    mapping(address => bool) public attestors;

    /// @dev Contract owner -- can grant/revoke attestor roles
    address public owner;

    // ---------------------------------------------------------------
    //  Events
    // ---------------------------------------------------------------

    event AttestationSubmitted(
        bytes32 indexed orgIdHash,
        uint64  nonce,
        bytes32 merkleRoot
    );

    event AttestorGranted(address indexed attestor);

    event AttestorRevoked(address indexed attestor);

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    // ---------------------------------------------------------------
    //  Errors
    // ---------------------------------------------------------------

    error Unauthorized();
    error InvalidPrevHash(bytes32 expected, bytes32 provided);
    error InvalidPeriod();
    error ZeroAddress();

    // ---------------------------------------------------------------
    //  Modifiers
    // ---------------------------------------------------------------

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyAttestor() {
        if (!attestors[msg.sender]) revert Unauthorized();
        _;
    }

    // ---------------------------------------------------------------
    //  Constructor
    // ---------------------------------------------------------------

    /// @param initialOwner The address that will own this contract and
    ///        receive the first attestor grant.
    constructor(address initialOwner) {
        owner = initialOwner;
        attestors[initialOwner] = true;
        emit AttestorGranted(initialOwner);
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    /// @notice Grant attestation submission rights to an address.
    /// @param attestor The address to authorize.
    function grantAttestor(address attestor) external onlyOwner {
        if (attestor == address(0)) revert ZeroAddress();
        attestors[attestor] = true;
        emit AttestorGranted(attestor);
    }

    /// @notice Revoke attestation submission rights from an address.
    /// @param attestor The address to deauthorize.
    function revokeAttestor(address attestor) external onlyOwner {
        attestors[attestor] = false;
        emit AttestorRevoked(attestor);
    }

    /// @notice Transfer contract ownership to a new address.
    /// @param newOwner The address of the new owner.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    // ---------------------------------------------------------------
    //  Core: Submit
    // ---------------------------------------------------------------

    /// @notice Submit a single attestation. Enforces chain linkage and nonce ordering.
    /// @param orgIdHash      keccak256 of the org identifier (pseudonymous).
    /// @param periodStart    Unix timestamp for the start of the attestation period.
    /// @param periodEnd      Unix timestamp for the end of the attestation period.
    /// @param metricsHash    SHA-256 hash of canonical period metrics JSON.
    /// @param benchmarkHash  SHA-256 hash of the fitness matrix snapshot.
    /// @param merkleRoot     Root of the trace evaluation Merkle tree.
    /// @param prevHash       Hash of this org's previous attestation (bytes32(0) for first).
    function attest(
        bytes32 orgIdHash,
        uint40  periodStart,
        uint40  periodEnd,
        bytes32 metricsHash,
        bytes32 benchmarkHash,
        bytes32 merkleRoot,
        bytes32 prevHash
    ) external onlyAttestor {
        _attest(orgIdHash, periodStart, periodEnd, metricsHash, benchmarkHash, merkleRoot, prevHash);
    }

    /// @notice Submit attestations for multiple orgs in a single transaction.
    /// @dev Uses a struct array to keep calldata ergonomic.
    ///      Saves ~30k gas per attestation vs individual calls.
    /// @param inputs Array of AttestInput structs.
    function batchAttest(AttestInput[] calldata inputs) external onlyAttestor {
        uint256 len = inputs.length;
        for (uint256 i = 0; i < len;) {
            AttestInput calldata inp = inputs[i];
            _attest(
                inp.orgIdHash,
                inp.periodStart,
                inp.periodEnd,
                inp.metricsHash,
                inp.benchmarkHash,
                inp.merkleRoot,
                inp.prevHash
            );
            unchecked { ++i; }
        }
    }

    // ---------------------------------------------------------------
    //  Core: Verify / Read
    // ---------------------------------------------------------------

    /// @notice Retrieve an attestation by org and nonce.
    /// @param orgIdHash The org's pseudonymous identifier hash.
    /// @param nonce     The attestation sequence number to look up.
    /// @return The Attestation struct (all zeroes if it doesn't exist).
    function verify(
        bytes32 orgIdHash,
        uint64  nonce
    ) external view returns (Attestation memory) {
        return attestations[orgIdHash][nonce];
    }

    /// @notice Retrieve the most recent attestation for an org.
    /// @param orgIdHash The org's pseudonymous identifier hash.
    /// @return The latest Attestation struct.
    function getLatest(bytes32 orgIdHash) external view returns (Attestation memory) {
        uint64 nonce = latestNonce[orgIdHash];
        return attestations[orgIdHash][nonce];
    }

    /// @notice Retrieve the latest nonce for an org.
    /// @param orgIdHash The org's pseudonymous identifier hash.
    /// @return The latest nonce value (0 means no attestations).
    function getLatestNonce(bytes32 orgIdHash) external view returns (uint64) {
        return latestNonce[orgIdHash];
    }

    // ---------------------------------------------------------------
    //  Internal
    // ---------------------------------------------------------------

    /// @dev Core attestation logic shared by attest() and batchAttest().
    ///      Enforces period validity, chain linkage, stores the record,
    ///      and emits the event.
    function _attest(
        bytes32 orgIdHash,
        uint40  periodStart,
        uint40  periodEnd,
        bytes32 metricsHash,
        bytes32 benchmarkHash,
        bytes32 merkleRoot,
        bytes32 prevHash
    ) internal {
        if (periodEnd <= periodStart) revert InvalidPeriod();

        uint64 currentNonce = latestNonce[orgIdHash];
        uint64 newNonce;
        unchecked { newNonce = currentNonce + 1; }

        // Chain linkage enforcement
        if (currentNonce == 0) {
            // First attestation for this org: prevHash must be zero
            if (prevHash != bytes32(0)) {
                revert InvalidPrevHash(bytes32(0), prevHash);
            }
        } else {
            // Subsequent attestation: prevHash must match hash of the previous record
            bytes32 computedPrev = _computeAttestationHash(
                attestations[orgIdHash][currentNonce]
            );
            if (prevHash != computedPrev) {
                revert InvalidPrevHash(computedPrev, prevHash);
            }
        }

        attestations[orgIdHash][newNonce] = Attestation({
            orgIdHash: orgIdHash,
            periodStart: periodStart,
            periodEnd: periodEnd,
            metricsHash: metricsHash,
            benchmarkHash: benchmarkHash,
            merkleRoot: merkleRoot,
            prevHash: prevHash,
            nonce: newNonce,
            timestamp: uint40(block.timestamp)
        });

        latestNonce[orgIdHash] = newNonce;

        emit AttestationSubmitted(orgIdHash, newNonce, merkleRoot);
    }

    /// @dev Compute the keccak256 hash of an attestation for chain linkage.
    ///      Uses abi.encodePacked for gas efficiency. The off-chain code
    ///      must replicate this exact encoding order.
    function _computeAttestationHash(
        Attestation storage a
    ) internal view returns (bytes32) {
        return keccak256(abi.encodePacked(
            a.orgIdHash,
            a.periodStart,
            a.periodEnd,
            a.metricsHash,
            a.benchmarkHash,
            a.merkleRoot,
            a.prevHash,
            a.nonce
        ));
    }
}
