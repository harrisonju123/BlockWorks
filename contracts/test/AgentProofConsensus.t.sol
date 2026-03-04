// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {AgentProofConsensus} from "../src/AgentProofConsensus.sol";
import {AgentProofStaking} from "../src/AgentProofStaking.sol";
import {AgentProofAttestation} from "../src/AgentProofAttestation.sol";

contract AgentProofConsensusTest is Test {

    AgentProofStaking     public staking;
    AgentProofAttestation public att;
    AgentProofConsensus   public consensus;

    address public owner = address(0x1);
    address public alice = address(0x10);  // 3 ETH validator
    address public bob   = address(0x20);  // 2 ETH validator
    address public carol = address(0x30);  // 1 ETH validator
    address public eve   = address(0x40);  // non-validator

    bytes32 constant ORG_A         = keccak256("org-alpha");
    uint40  constant PERIOD_START  = 1_700_000_000;
    uint40  constant PERIOD_END    = 1_700_086_400;
    bytes32 constant METRICS_HASH  = keccak256("metrics-v1");
    bytes32 constant BENCH_HASH    = keccak256("benchmark-v1");
    bytes32 constant PREV_HASH     = bytes32(0);
    uint64  constant NONCE_1       = 1;

    // Build a simple Merkle tree: 2 leaves, sorted-pair SHA-256
    bytes32 leaf0;
    bytes32 leaf1;
    bytes32 merkleRoot;

    function setUp() public {
        // Deploy staking + attestation
        vm.startPrank(owner);
        staking = new AgentProofStaking(owner, 0.1 ether, 300);
        att = new AgentProofAttestation(owner);

        // Deploy consensus, wire roles
        consensus = new AgentProofConsensus(staking, att, owner);
        att.grantAttestor(address(consensus));
        staking.transferOwnership(address(consensus));

        // Lower quorum to 3 for testing (already default)
        consensus.setMinQuorum(3);
        vm.stopPrank();

        // Fund & stake validators: Alice=3, Bob=2, Carol=1 (total=6 ETH)
        vm.deal(alice, 10 ether);
        vm.deal(bob,   10 ether);
        vm.deal(carol, 10 ether);
        vm.deal(eve,   10 ether);

        vm.prank(alice);
        staking.stake{value: 3 ether}();
        vm.prank(bob);
        staking.stake{value: 2 ether}();
        vm.prank(carol);
        staking.stake{value: 1 ether}();

        // Pre-compute Merkle tree (2 leaves, sorted-pair SHA-256)
        leaf0 = sha256(abi.encodePacked("leaf-0"));
        leaf1 = sha256(abi.encodePacked("leaf-1"));
        if (leaf0 < leaf1) {
            merkleRoot = sha256(abi.encodePacked(leaf0, leaf1));
        } else {
            merkleRoot = sha256(abi.encodePacked(leaf1, leaf0));
        }
    }

    // ---------------------------------------------------------------
    //  Proposal creation
    // ---------------------------------------------------------------

    function test_proposeCreatesProposal() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        assertEq(pid, 1);
        assertEq(consensus.proposalCount(), 1);
    }

    function test_proposeAutoVotesForProposer() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        assertTrue(consensus.hasVoted(pid, alice));
        assertTrue(consensus.votedYes(pid, alice));
        assertEq(consensus.getVoterCount(pid), 1);
    }

    function test_proposeEmitsEvent() public {
        vm.prank(alice);
        vm.expectEmit(true, true, false, true);
        emit AgentProofConsensus.ProposalCreated(1, ORG_A, NONCE_1, alice);
        consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
    }

    function test_proposeSlotUniqueness() public {
        vm.prank(alice);
        consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        // Same org+nonce should revert
        vm.prank(bob);
        vm.expectRevert(
            abi.encodeWithSelector(
                AgentProofConsensus.ProposalSlotTaken.selector, ORG_A, NONCE_1
            )
        );
        consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
    }

    function test_proposeNonValidatorReverts() public {
        vm.prank(eve);
        vm.expectRevert(AgentProofConsensus.NotValidator.selector);
        consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
    }

    // ---------------------------------------------------------------
    //  Voting
    // ---------------------------------------------------------------

    function test_voteAccumulatesStake() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.prank(bob);
        consensus.vote(pid, true);

        // Alice(3) + Bob(2) = 5 ETH yes, 5 ETH total
        (,,,,,,,,, uint40 createdAt, uint256 totalStake, uint256 yesStake,,) = consensus.proposals(pid);
        assertEq(totalStake, 5 ether);
        assertEq(yesStake, 5 ether);
    }

    function test_voteNoDoesNotAddToYesStake() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.prank(bob);
        consensus.vote(pid, false);

        (,,,,,,,,,,uint256 totalStake, uint256 yesStake,,) = consensus.proposals(pid);
        assertEq(totalStake, 5 ether);
        assertEq(yesStake, 3 ether); // Only Alice's auto-vote
    }

    function test_voteDuplicateRejected() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.prank(alice);
        vm.expectRevert(AgentProofConsensus.AlreadyVoted.selector);
        consensus.vote(pid, true);
    }

    function test_voteNonValidatorReverts() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.prank(eve);
        vm.expectRevert(AgentProofConsensus.NotValidator.selector);
        consensus.vote(pid, true);
    }

    function test_voteAfterTTLReverts() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.warp(block.timestamp + consensus.PROPOSAL_TTL() + 1);

        vm.prank(bob);
        vm.expectRevert(AgentProofConsensus.ProposalTTLExpired.selector);
        consensus.vote(pid, true);
    }

    function test_voteEmitsEvent() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.prank(bob);
        vm.expectEmit(true, true, false, true);
        emit AgentProofConsensus.Voted(pid, bob, true, 2 ether);
        consensus.vote(pid, true);
    }

    // ---------------------------------------------------------------
    //  Finalization
    // ---------------------------------------------------------------

    function test_finalizeWithSupermajority() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);
        vm.prank(carol);
        consensus.vote(pid, true);

        // All 3 vote yes: 6/6 = 100% > 66.67%, quorum=3
        consensus.finalize(pid);

        (,,,,,,,,,,,, bool finalized,) = consensus.proposals(pid);
        assertTrue(finalized);
    }

    function test_finalizeWritesThroughToAttestation() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);
        vm.prank(carol);
        consensus.vote(pid, true);

        consensus.finalize(pid);

        // Attestation contract should now have nonce 1
        assertEq(att.getLatestNonce(ORG_A), 1);
        AgentProofAttestation.Attestation memory a = att.verify(ORG_A, 1);
        assertEq(a.orgIdHash, ORG_A);
        assertEq(a.metricsHash, METRICS_HASH);
        assertEq(a.merkleRoot, merkleRoot);
    }

    function test_finalizeEmitsEvent() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);
        vm.prank(carol);
        consensus.vote(pid, true);

        vm.expectEmit(true, true, false, true);
        emit AgentProofConsensus.ProposalFinalized(pid, ORG_A, NONCE_1);
        consensus.finalize(pid);
    }

    function test_finalizeBelowQuorumReverts() public {
        // Lower quorum requires 3, but only 2 vote
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);

        vm.expectRevert(
            abi.encodeWithSelector(
                AgentProofConsensus.QuorumNotMet.selector, 2, 3
            )
        );
        consensus.finalize(pid);
    }

    function test_finalizeBelowSupermajorityReverts() public {
        // Alice(3) yes, Bob(2) no, Carol(1) no
        // yesStake=3, totalStake=6 -> 50% < 66.67%
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, false);
        vm.prank(carol);
        consensus.vote(pid, false);

        vm.expectRevert(
            abi.encodeWithSelector(
                AgentProofConsensus.SupermajorityNotMet.selector, 3 ether, 6 ether
            )
        );
        consensus.finalize(pid);
    }

    function test_finalizeExactTwoThirdsThreshold() public {
        // Alice(3) yes, Bob(2) yes, Carol(1) no
        // yesStake=5, totalStake=6 -> 83.3% > 66.67% -> passes
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);
        vm.prank(carol);
        consensus.vote(pid, false);

        consensus.finalize(pid);

        (,,,,,,,,,,,, bool finalized,) = consensus.proposals(pid);
        assertTrue(finalized);
    }

    function test_finalizeAlreadyFinalizedReverts() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);
        vm.prank(carol);
        consensus.vote(pid, true);

        consensus.finalize(pid);

        vm.expectRevert(AgentProofConsensus.ProposalAlreadyFinalized.selector);
        consensus.finalize(pid);
    }

    // ---------------------------------------------------------------
    //  Challenge
    // ---------------------------------------------------------------

    function _createAndFinalize() internal returns (uint256 pid) {
        vm.prank(alice);
        pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);
        vm.prank(carol);
        consensus.vote(pid, true);
        consensus.finalize(pid);
    }

    function test_challengeWithValidProof() public {
        uint256 pid = _createAndFinalize();

        // Build proof for leaf0 in a 2-leaf tree: sibling is leaf1
        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );
        assertEq(cid, 1);
    }

    function test_challengeInsufficientBondReverts() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        vm.expectRevert(
            abi.encodeWithSelector(
                AgentProofConsensus.InsufficientBond.selector, 0.001 ether, 0.01 ether
            )
        );
        consensus.challengeAttestation{value: 0.001 ether}(pid, leaf0, proof, "");
    }

    function test_challengeInvalidMerkleProofReverts() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = keccak256("bogus-sibling");

        vm.prank(eve);
        vm.expectRevert(AgentProofConsensus.InvalidMerkleProof.selector);
        consensus.challengeAttestation{value: 0.01 ether}(pid, leaf0, proof, "");
    }

    function test_challengeNonFinalizedReverts() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        vm.expectRevert(AgentProofConsensus.ProposalNotFinalized.selector);
        consensus.challengeAttestation{value: 0.01 ether}(pid, leaf0, proof, "");
    }

    function test_challengeEmitsEvent() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        vm.expectEmit(true, true, false, true);
        emit AgentProofConsensus.ChallengeCreated(1, pid, eve);
        consensus.challengeAttestation{value: 0.01 ether}(pid, leaf0, proof, "");
    }

    // ---------------------------------------------------------------
    //  Resolution
    // ---------------------------------------------------------------

    function test_resolveChallengerWinsSlashesYesVoters() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );

        uint256 eveBefore = eve.balance;

        // All 3 voted yes. Slash 5% of each:
        // Alice: 3 ETH * 5% = 0.15 ETH
        // Bob:   2 ETH * 5% = 0.10 ETH
        // Carol: 1 ETH * 5% = 0.05 ETH
        // Total slashed = 0.30 ETH
        // Eve gets bond(0.01) + 50%(0.30) = 0.16 ETH
        vm.prank(owner);
        consensus.resolveChallenge(cid, true);

        assertEq(staking.getStake(alice), 2.85 ether);
        assertEq(staking.getStake(bob),   1.90 ether);
        assertEq(staking.getStake(carol),  0.95 ether);
        assertEq(eve.balance, eveBefore + 0.16 ether);
    }

    function test_resolveChallengerLosesForfeitsBond() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );

        uint256 eveBefore = eve.balance;

        vm.prank(owner);
        consensus.resolveChallenge(cid, false);

        // Eve doesn't get bond back; stakes unchanged
        assertEq(eve.balance, eveBefore);
        assertEq(staking.getStake(alice), 3 ether);
        assertEq(staking.getStake(bob),   2 ether);
        assertEq(staking.getStake(carol), 1 ether);
    }

    function test_resolveEmitsEvent() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );

        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit AgentProofConsensus.ChallengeResolved(cid, true);
        consensus.resolveChallenge(cid, true);
    }

    function test_resolveAlreadyResolvedReverts() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );

        vm.prank(owner);
        consensus.resolveChallenge(cid, false);

        vm.prank(owner);
        vm.expectRevert(AgentProofConsensus.ChallengeAlreadyResolved.selector);
        consensus.resolveChallenge(cid, false);
    }

    function test_resolveNonOwnerReverts() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );

        vm.prank(eve);
        vm.expectRevert(AgentProofConsensus.Unauthorized.selector);
        consensus.resolveChallenge(cid, true);
    }

    function test_reChallengeAfterResolution() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        // First challenge — challenger loses
        vm.prank(eve);
        uint256 cid1 = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );
        vm.prank(owner);
        consensus.resolveChallenge(cid1, false);

        // Proposal not slashed, so can be challenged again
        // (but only if not already slashed)
        (,,,,,,,,,,,,, bool slashed) = consensus.proposals(pid);
        assertFalse(slashed);

        // Second challenge succeeds
        vm.prank(eve);
        uint256 cid2 = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );
        assertEq(cid2, 2);
    }

    function test_reChallengeBlockedAfterSlash() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );
        vm.prank(owner);
        consensus.resolveChallenge(cid, true); // challenger wins -> slashed

        vm.prank(eve);
        vm.expectRevert(AgentProofConsensus.ProposalAlreadySlashed.selector);
        consensus.challengeAttestation{value: 0.01 ether}(pid, leaf0, proof, "");
    }

    // ---------------------------------------------------------------
    //  Expiry
    // ---------------------------------------------------------------

    function test_expireProposalAfterTTL() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.warp(block.timestamp + consensus.PROPOSAL_TTL() + 1);
        consensus.expireProposal(pid);

        // Slot is freed — can propose again for same org+nonce
        assertEq(consensus.proposalByOrgNonce(ORG_A, NONCE_1), 0);
    }

    function test_expireBeforeTTLReverts() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.expectRevert(AgentProofConsensus.ProposalNotExpired.selector);
        consensus.expireProposal(pid);
    }

    function test_expireFinalizedReverts() public {
        uint256 pid = _createAndFinalize();

        vm.warp(block.timestamp + consensus.PROPOSAL_TTL() + 1);
        vm.expectRevert(AgentProofConsensus.ProposalAlreadyFinalized.selector);
        consensus.expireProposal(pid);
    }

    // ---------------------------------------------------------------
    //  Edge cases
    // ---------------------------------------------------------------

    function test_proposalNotFoundReverts() public {
        vm.expectRevert(AgentProofConsensus.ProposalNotFound.selector);
        consensus.finalize(999);
    }

    function test_challengeNotFoundReverts() public {
        vm.prank(owner);
        vm.expectRevert(AgentProofConsensus.ChallengeNotFound.selector);
        consensus.resolveChallenge(999, true);
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    function test_setMinQuorum() public {
        vm.prank(owner);
        vm.expectEmit(false, false, false, true);
        emit AgentProofConsensus.MinQuorumUpdated(3, 5);
        consensus.setMinQuorum(5);
        assertEq(consensus.minQuorum(), 5);
    }

    function test_setSlashPercentBps() public {
        vm.prank(owner);
        consensus.setSlashPercentBps(1000);
        assertEq(consensus.slashPercentBps(), 1000);
    }

    function test_setChallengeBondMin() public {
        vm.prank(owner);
        consensus.setChallengeBondMin(0.1 ether);
        assertEq(consensus.challengeBondMin(), 0.1 ether);
    }

    function test_transferOwnership() public {
        vm.prank(owner);
        consensus.transferOwnership(bob);
        assertEq(consensus.owner(), bob);
    }

    function test_adminFunctionsRevertNonOwner() public {
        vm.startPrank(eve);

        vm.expectRevert(AgentProofConsensus.Unauthorized.selector);
        consensus.setMinQuorum(5);

        vm.expectRevert(AgentProofConsensus.Unauthorized.selector);
        consensus.setSlashPercentBps(1000);

        vm.expectRevert(AgentProofConsensus.Unauthorized.selector);
        consensus.setChallengeBondMin(0.1 ether);

        vm.expectRevert(AgentProofConsensus.Unauthorized.selector);
        consensus.transferOwnership(eve);

        vm.stopPrank();
    }

    // ---------------------------------------------------------------
    //  Gas snapshots
    // ---------------------------------------------------------------

    function test_gasSnapshot_propose() public {
        vm.prank(alice);
        uint256 gasBefore = gasleft();
        consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: propose (incl. auto-vote)", gasUsed);
    }

    function test_gasSnapshot_vote() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );

        vm.prank(bob);
        uint256 gasBefore = gasleft();
        consensus.vote(pid, true);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: vote", gasUsed);
    }

    function test_gasSnapshot_finalize() public {
        vm.prank(alice);
        uint256 pid = consensus.propose(
            ORG_A, PERIOD_START, PERIOD_END,
            METRICS_HASH, BENCH_HASH, merkleRoot, PREV_HASH, NONCE_1
        );
        vm.prank(bob);
        consensus.vote(pid, true);
        vm.prank(carol);
        consensus.vote(pid, true);

        uint256 gasBefore = gasleft();
        consensus.finalize(pid);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: finalize (write-through)", gasUsed);
    }

    function test_gasSnapshot_challenge() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 gasBefore = gasleft();
        consensus.challengeAttestation{value: 0.01 ether}(pid, leaf0, proof, "");
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: challenge", gasUsed);
    }

    function test_gasSnapshot_resolve() public {
        uint256 pid = _createAndFinalize();

        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leaf1;

        vm.prank(eve);
        uint256 cid = consensus.challengeAttestation{value: 0.01 ether}(
            pid, leaf0, proof, ""
        );

        vm.prank(owner);
        uint256 gasBefore = gasleft();
        consensus.resolveChallenge(cid, true);
        uint256 gasUsed = gasBefore - gasleft();
        emit log_named_uint("Gas: resolve (challenger wins, 3 validators slashed)", gasUsed);
    }
}
