// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {AgentProofAttestation} from "../src/AgentProofAttestation.sol";

contract AgentProofAttestationTest is Test {

    AgentProofAttestation public registry;

    address public owner = address(0x1);
    address public attestor = address(0x2);
    address public outsider = address(0x3);

    // Reusable test data
    bytes32 constant ORG_A = keccak256("org-alpha");
    bytes32 constant ORG_B = keccak256("org-beta");
    bytes32 constant ORG_C = keccak256("org-gamma");

    uint40  constant PERIOD_START = 1_700_000_000;
    uint40  constant PERIOD_END   = 1_700_086_400; // +1 day

    bytes32 constant METRICS_HASH   = keccak256("metrics-v1");
    bytes32 constant BENCHMARK_HASH = keccak256("benchmark-v1");
    bytes32 constant MERKLE_ROOT    = keccak256("merkle-root-v1");

    function setUp() public {
        vm.prank(owner);
        registry = new AgentProofAttestation(owner);
    }

    // ---------------------------------------------------------------
    //  Access control
    // ---------------------------------------------------------------

    function test_ownerIsAttestorByDefault() public view {
        assertTrue(registry.attestors(owner));
    }

    function test_grantAttestor() public {
        vm.prank(owner);
        registry.grantAttestor(attestor);
        assertTrue(registry.attestors(attestor));
    }

    function test_revokeAttestor() public {
        vm.prank(owner);
        registry.grantAttestor(attestor);

        vm.prank(owner);
        registry.revokeAttestor(attestor);
        assertFalse(registry.attestors(attestor));
    }

    function test_grantAttestorEmitsEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, false, false, false);
        emit AgentProofAttestation.AttestorGranted(attestor);
        registry.grantAttestor(attestor);
    }

    function test_revokeAttestorEmitsEvent() public {
        vm.prank(owner);
        registry.grantAttestor(attestor);

        vm.prank(owner);
        vm.expectEmit(true, false, false, false);
        emit AgentProofAttestation.AttestorRevoked(attestor);
        registry.revokeAttestor(attestor);
    }

    function test_nonOwnerCannotGrantAttestor() public {
        vm.prank(outsider);
        vm.expectRevert(AgentProofAttestation.Unauthorized.selector);
        registry.grantAttestor(attestor);
    }

    function test_nonAttestorCannotAttest() public {
        vm.prank(outsider);
        vm.expectRevert(AgentProofAttestation.Unauthorized.selector);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );
    }

    function test_nonAttestorCannotBatchAttest() public {
        AgentProofAttestation.AttestInput[] memory inputs =
            new AgentProofAttestation.AttestInput[](1);
        inputs[0] = AgentProofAttestation.AttestInput({
            orgIdHash: ORG_A,
            periodStart: PERIOD_START,
            periodEnd: PERIOD_END,
            metricsHash: METRICS_HASH,
            benchmarkHash: BENCHMARK_HASH,
            merkleRoot: MERKLE_ROOT,
            prevHash: bytes32(0)
        });

        vm.prank(outsider);
        vm.expectRevert(AgentProofAttestation.Unauthorized.selector);
        registry.batchAttest(inputs);
    }

    // ---------------------------------------------------------------
    //  First attestation (nonce=0 -> nonce=1, prevHash=0)
    // ---------------------------------------------------------------

    function test_firstAttestationSucceeds() public {
        vm.prank(owner);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );

        assertEq(registry.getLatestNonce(ORG_A), 1);
    }

    function test_firstAttestationEmitsEvent() public {
        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit AgentProofAttestation.AttestationSubmitted(ORG_A, 1, MERKLE_ROOT);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );
    }

    function test_firstAttestationRejectsNonZeroPrevHash() public {
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                AgentProofAttestation.InvalidPrevHash.selector,
                bytes32(0),
                bytes32(uint256(0xdead))
            )
        );
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(uint256(0xdead))
        );
    }

    // ---------------------------------------------------------------
    //  Single attestation submit and retrieve
    // ---------------------------------------------------------------

    function test_submitAndRetrieveSingleAttestation() public {
        vm.warp(1_700_100_000);

        vm.prank(owner);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );

        AgentProofAttestation.Attestation memory a = registry.verify(ORG_A, 1);
        assertEq(a.orgIdHash, ORG_A);
        assertEq(a.periodStart, PERIOD_START);
        assertEq(a.periodEnd, PERIOD_END);
        assertEq(a.metricsHash, METRICS_HASH);
        assertEq(a.benchmarkHash, BENCHMARK_HASH);
        assertEq(a.merkleRoot, MERKLE_ROOT);
        assertEq(a.prevHash, bytes32(0));
        assertEq(a.nonce, 1);
        assertEq(a.timestamp, 1_700_100_000);
    }

    function test_verifyReturnsCorrectData() public {
        vm.prank(owner);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );

        // verify() and getLatest() should return the same thing for nonce 1
        AgentProofAttestation.Attestation memory fromVerify = registry.verify(ORG_A, 1);
        AgentProofAttestation.Attestation memory fromLatest = registry.getLatest(ORG_A);
        assertEq(fromVerify.orgIdHash, fromLatest.orgIdHash);
        assertEq(fromVerify.nonce, fromLatest.nonce);
        assertEq(fromVerify.merkleRoot, fromLatest.merkleRoot);
    }

    // ---------------------------------------------------------------
    //  Period validation
    // ---------------------------------------------------------------

    function test_rejectsInvalidPeriod() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofAttestation.InvalidPeriod.selector);
        registry.attest(
            ORG_A, PERIOD_END, PERIOD_START, // swapped: end < start
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );
    }

    function test_rejectsEqualPeriod() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofAttestation.InvalidPeriod.selector);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_START,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );
    }

    // ---------------------------------------------------------------
    //  Chain linkage enforcement
    // ---------------------------------------------------------------

    function test_chainLinkageCorrectPrevHash() public {
        // Submit first attestation
        vm.prank(owner);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );

        // Compute prevHash matching what the contract expects
        AgentProofAttestation.Attestation memory prev = registry.verify(ORG_A, 1);
        bytes32 expectedPrev = keccak256(abi.encodePacked(
            prev.orgIdHash,
            prev.periodStart,
            prev.periodEnd,
            prev.metricsHash,
            prev.benchmarkHash,
            prev.merkleRoot,
            prev.prevHash,
            prev.nonce
        ));

        // Second attestation with correct prevHash should succeed
        bytes32 newMetrics = keccak256("metrics-v2");
        bytes32 newMerkle  = keccak256("merkle-root-v2");
        uint40  newStart   = PERIOD_END;
        uint40  newEnd     = PERIOD_END + 86400;

        vm.prank(owner);
        registry.attest(
            ORG_A, newStart, newEnd,
            newMetrics, BENCHMARK_HASH, newMerkle,
            expectedPrev
        );

        assertEq(registry.getLatestNonce(ORG_A), 2);
    }

    function test_chainLinkageWrongPrevHashReverts() public {
        // Submit first attestation
        vm.prank(owner);
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );

        // Second attestation with wrong prevHash should revert
        bytes32 bogus = keccak256("wrong-hash");
        uint40  newStart = PERIOD_END;
        uint40  newEnd   = PERIOD_END + 86400;

        vm.prank(owner);
        vm.expectRevert(); // InvalidPrevHash
        registry.attest(
            ORG_A, newStart, newEnd,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bogus
        );
    }

    // ---------------------------------------------------------------
    //  Nonce sequencing
    // ---------------------------------------------------------------

    function test_nonceIsSequential() public {
        vm.startPrank(owner);

        // First
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );
        assertEq(registry.getLatestNonce(ORG_A), 1);

        // Compute prevHash for nonce 1
        AgentProofAttestation.Attestation memory a1 = registry.verify(ORG_A, 1);
        bytes32 prev1 = _hashAttestation(a1);

        // Second
        uint40 s2 = PERIOD_END;
        uint40 e2 = PERIOD_END + 86400;
        registry.attest(ORG_A, s2, e2, METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT, prev1);
        assertEq(registry.getLatestNonce(ORG_A), 2);

        // Compute prevHash for nonce 2
        AgentProofAttestation.Attestation memory a2 = registry.verify(ORG_A, 2);
        bytes32 prev2 = _hashAttestation(a2);

        // Third
        uint40 s3 = e2;
        uint40 e3 = e2 + 86400;
        registry.attest(ORG_A, s3, e3, METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT, prev2);
        assertEq(registry.getLatestNonce(ORG_A), 3);

        vm.stopPrank();
    }

    // ---------------------------------------------------------------
    //  getLatest returns highest nonce
    // ---------------------------------------------------------------

    function test_getLatestReturnsHighestNonce() public {
        vm.startPrank(owner);

        // Submit three attestations, building the chain
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );

        AgentProofAttestation.Attestation memory a1 = registry.verify(ORG_A, 1);
        bytes32 prev1 = _hashAttestation(a1);

        uint40 s2 = PERIOD_END;
        uint40 e2 = PERIOD_END + 86400;
        bytes32 metrics2 = keccak256("metrics-v2");
        registry.attest(ORG_A, s2, e2, metrics2, BENCHMARK_HASH, MERKLE_ROOT, prev1);

        AgentProofAttestation.Attestation memory a2 = registry.verify(ORG_A, 2);
        bytes32 prev2 = _hashAttestation(a2);

        uint40 s3 = e2;
        uint40 e3 = e2 + 86400;
        bytes32 metrics3 = keccak256("metrics-v3");
        registry.attest(ORG_A, s3, e3, metrics3, BENCHMARK_HASH, MERKLE_ROOT, prev2);

        vm.stopPrank();

        AgentProofAttestation.Attestation memory latest = registry.getLatest(ORG_A);
        assertEq(latest.nonce, 3);
        assertEq(latest.metricsHash, metrics3);
    }

    // ---------------------------------------------------------------
    //  Batch attestation (3 orgs in one tx)
    // ---------------------------------------------------------------

    function test_batchAttestThreeOrgs() public {
        AgentProofAttestation.AttestInput[] memory inputs =
            new AgentProofAttestation.AttestInput[](3);

        inputs[0] = AgentProofAttestation.AttestInput({
            orgIdHash: ORG_A,
            periodStart: PERIOD_START,
            periodEnd: PERIOD_END,
            metricsHash: keccak256("m-a"),
            benchmarkHash: keccak256("b-a"),
            merkleRoot: keccak256("r-a"),
            prevHash: bytes32(0)
        });
        inputs[1] = AgentProofAttestation.AttestInput({
            orgIdHash: ORG_B,
            periodStart: PERIOD_START,
            periodEnd: PERIOD_END,
            metricsHash: keccak256("m-b"),
            benchmarkHash: keccak256("b-b"),
            merkleRoot: keccak256("r-b"),
            prevHash: bytes32(0)
        });
        inputs[2] = AgentProofAttestation.AttestInput({
            orgIdHash: ORG_C,
            periodStart: PERIOD_START,
            periodEnd: PERIOD_END,
            metricsHash: keccak256("m-c"),
            benchmarkHash: keccak256("b-c"),
            merkleRoot: keccak256("r-c"),
            prevHash: bytes32(0)
        });

        vm.prank(owner);
        registry.batchAttest(inputs);

        // All three orgs should have nonce 1
        assertEq(registry.getLatestNonce(ORG_A), 1);
        assertEq(registry.getLatestNonce(ORG_B), 1);
        assertEq(registry.getLatestNonce(ORG_C), 1);

        // Spot check org B data
        AgentProofAttestation.Attestation memory b = registry.verify(ORG_B, 1);
        assertEq(b.metricsHash, keccak256("m-b"));
        assertEq(b.merkleRoot, keccak256("r-b"));
    }

    // ---------------------------------------------------------------
    //  Gas snapshots
    // ---------------------------------------------------------------

    function test_gasSnapshot_singleAttest() public {
        vm.prank(owner);
        uint256 gasBefore = gasleft();
        registry.attest(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCHMARK_HASH, MERKLE_ROOT,
            bytes32(0)
        );
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: single attest (first for org)", gasUsed);
    }

    function test_gasSnapshot_batchOfTen() public {
        AgentProofAttestation.AttestInput[] memory inputs =
            new AgentProofAttestation.AttestInput[](10);

        for (uint256 i = 0; i < 10; i++) {
            inputs[i] = AgentProofAttestation.AttestInput({
                orgIdHash: keccak256(abi.encodePacked("org-", i)),
                periodStart: PERIOD_START,
                periodEnd: PERIOD_END,
                metricsHash: keccak256(abi.encodePacked("metrics-", i)),
                benchmarkHash: keccak256(abi.encodePacked("bench-", i)),
                merkleRoot: keccak256(abi.encodePacked("root-", i)),
                prevHash: bytes32(0)
            });
        }

        vm.prank(owner);
        uint256 gasBefore = gasleft();
        registry.batchAttest(inputs);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: batch attest (10 orgs)", gasUsed);
        emit log_named_uint("Gas: per-org in batch", gasUsed / 10);
    }

    // ---------------------------------------------------------------
    //  Helpers
    // ---------------------------------------------------------------

    /// @dev Mirror the contract's _computeAttestationHash for test assertions.
    function _hashAttestation(
        AgentProofAttestation.Attestation memory a
    ) internal pure returns (bytes32) {
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
