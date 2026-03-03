"""Tests for the Merkle tree library.

Covers construction, proof generation/verification, edge cases,
and the sorted-pair hashing invariant that makes trees order-independent.
"""

from __future__ import annotations

import hashlib

import pytest

from agentproof.attestation.merkle import EMPTY_LEAF, MerkleTree, _hash_pair


class TestMerkleTreeConstruction:
    """Core tree building from known inputs."""

    def test_empty_tree_returns_empty_leaf(self) -> None:
        tree = MerkleTree([])
        assert tree.root == EMPTY_LEAF
        assert len(tree.layers) == 1
        assert tree.layers[0] == [EMPTY_LEAF]

    def test_single_leaf(self) -> None:
        tree = MerkleTree([b"hello"])
        leaf = MerkleTree.leaf_hash(b"hello")
        # 1 is a power of 2, so no padding needed; root is the leaf itself
        assert tree.root == leaf
        assert len(tree.layers) == 1

    def test_two_leaves(self) -> None:
        tree = MerkleTree([b"a", b"b"])
        leaf_a = MerkleTree.leaf_hash(b"a")
        leaf_b = MerkleTree.leaf_hash(b"b")
        expected_root = _hash_pair(leaf_a, leaf_b)
        assert tree.root == expected_root
        assert len(tree.layers) == 2

    def test_four_leaves_known_root(self) -> None:
        leaves = [b"alpha", b"bravo", b"charlie", b"delta"]
        tree = MerkleTree(leaves)

        hashes = [MerkleTree.leaf_hash(leaf) for leaf in leaves]
        pair_01 = _hash_pair(hashes[0], hashes[1])
        pair_23 = _hash_pair(hashes[2], hashes[3])
        expected_root = _hash_pair(pair_01, pair_23)

        assert tree.root == expected_root
        assert len(tree.layers) == 3  # leaves, intermediates, root

    def test_three_leaves_padded_to_four(self) -> None:
        """Odd leaf count pads to next power of two with EMPTY_LEAF."""
        tree = MerkleTree([b"x", b"y", b"z"])

        hashes = [MerkleTree.leaf_hash(leaf) for leaf in [b"x", b"y", b"z"]]
        hashes.append(EMPTY_LEAF)  # padding

        pair_01 = _hash_pair(hashes[0], hashes[1])
        pair_23 = _hash_pair(hashes[2], hashes[3])
        expected_root = _hash_pair(pair_01, pair_23)

        assert tree.root == expected_root
        assert len(tree.layers[0]) == 4  # padded leaf layer

    def test_five_leaves_padded_to_eight(self) -> None:
        leaves = [f"leaf-{i}".encode() for i in range(5)]
        tree = MerkleTree(leaves)
        assert len(tree.layers[0]) == 8  # next power of 2 above 5

    def test_power_of_two_no_padding(self) -> None:
        leaves = [f"leaf-{i}".encode() for i in range(8)]
        tree = MerkleTree(leaves)
        assert len(tree.layers[0]) == 8

    def test_root_is_64_char_hex(self) -> None:
        tree = MerkleTree([b"test"])
        assert len(tree.root) == 64
        # Verify it's valid hex
        int(tree.root, 16)

    def test_deterministic_same_inputs(self) -> None:
        """Same leaves in same order always produce the same root."""
        leaves = [b"one", b"two", b"three", b"four"]
        root_a = MerkleTree(leaves).root
        root_b = MerkleTree(leaves).root
        assert root_a == root_b

    def test_string_leaves(self) -> None:
        """String inputs are accepted and encoded to UTF-8."""
        tree_bytes = MerkleTree([b"hello", b"world"])
        tree_str = MerkleTree(["hello", "world"])
        assert tree_bytes.root == tree_str.root


class TestSortedPairHashing:
    """Verify sorted-pair hashing gives order independence within pairs."""

    def test_hash_pair_order_independent(self) -> None:
        a = hashlib.sha256(b"foo").hexdigest()
        b = hashlib.sha256(b"bar").hexdigest()
        assert _hash_pair(a, b) == _hash_pair(b, a)

    def test_tree_ab_equals_tree_ba(self) -> None:
        """Two-leaf tree with (a,b) and (b,a) produce the same root."""
        tree_ab = MerkleTree([b"a", b"b"])
        tree_ba = MerkleTree([b"b", b"a"])
        assert tree_ab.root == tree_ba.root

    def test_hash_pair_different_inputs_different_output(self) -> None:
        a = hashlib.sha256(b"foo").hexdigest()
        b = hashlib.sha256(b"bar").hexdigest()
        c = hashlib.sha256(b"baz").hexdigest()
        assert _hash_pair(a, b) != _hash_pair(a, c)


class TestProofGeneration:
    """Merkle proof generation and index bounds."""

    def test_proof_length_matches_tree_depth(self) -> None:
        tree = MerkleTree([b"a", b"b", b"c", b"d"])
        proof = tree.get_proof(0)
        # 4 leaves -> 2 layers below root -> 2 siblings in proof
        assert len(proof) == 2

    def test_proof_for_each_leaf_position(self) -> None:
        leaves = [b"one", b"two", b"three", b"four"]
        tree = MerkleTree(leaves)
        for i in range(4):
            proof = tree.get_proof(i)
            assert len(proof) == 2

    def test_proof_out_of_range_raises(self) -> None:
        tree = MerkleTree([b"a", b"b"])
        with pytest.raises(IndexError):
            tree.get_proof(2)
        with pytest.raises(IndexError):
            tree.get_proof(-1)

    def test_single_leaf_proof_is_empty(self) -> None:
        tree = MerkleTree([b"only"])
        proof = tree.get_proof(0)
        # 1 leaf = 1 layer = root is the leaf itself, no siblings needed
        assert len(proof) == 0
        # Verify still works: leaf hash IS the root
        leaf_hash = MerkleTree.leaf_hash(b"only")
        assert MerkleTree.verify_proof(leaf_hash, proof, tree.root)


class TestProofVerification:
    """Round-trip: build tree -> get proof -> verify."""

    def test_verify_all_leaves_four_leaf_tree(self) -> None:
        leaves = [b"alpha", b"bravo", b"charlie", b"delta"]
        tree = MerkleTree(leaves)

        for i in range(4):
            leaf_hash = MerkleTree.leaf_hash(leaves[i])
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(leaf_hash, proof, tree.root)

    def test_verify_single_leaf_tree(self) -> None:
        tree = MerkleTree([b"solo"])
        leaf_hash = MerkleTree.leaf_hash(b"solo")
        proof = tree.get_proof(0)
        assert MerkleTree.verify_proof(leaf_hash, proof, tree.root)

    def test_verify_two_leaf_tree(self) -> None:
        tree = MerkleTree([b"left", b"right"])
        for i in range(2):
            leaf_hash = MerkleTree.leaf_hash([b"left", b"right"][i])
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(leaf_hash, proof, tree.root)

    def test_verify_odd_leaf_count(self) -> None:
        leaves = [b"a", b"b", b"c"]
        tree = MerkleTree(leaves)
        for i in range(3):
            leaf_hash = MerkleTree.leaf_hash(leaves[i])
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(leaf_hash, proof, tree.root)

    def test_wrong_leaf_hash_fails_verification(self) -> None:
        tree = MerkleTree([b"real", b"data"])
        proof = tree.get_proof(0)
        fake_hash = MerkleTree.leaf_hash(b"fake")
        assert not MerkleTree.verify_proof(fake_hash, proof, tree.root)

    def test_wrong_root_fails_verification(self) -> None:
        tree = MerkleTree([b"a", b"b"])
        leaf_hash = MerkleTree.leaf_hash(b"a")
        proof = tree.get_proof(0)
        wrong_root = "0" * 64
        assert not MerkleTree.verify_proof(leaf_hash, proof, wrong_root)

    def test_tampered_proof_fails_verification(self) -> None:
        tree = MerkleTree([b"a", b"b", b"c", b"d"])
        leaf_hash = MerkleTree.leaf_hash(b"a")
        proof = tree.get_proof(0)
        # Tamper with a sibling hash
        tampered = [("0" * 64, p[1]) for p in proof]
        assert not MerkleTree.verify_proof(leaf_hash, tampered, tree.root)

    def test_verify_large_tree_1000_leaves(self) -> None:
        """Stress test: 1000 leaves, verify a handful of them."""
        leaves = [f"leaf-{i}".encode() for i in range(1000)]
        tree = MerkleTree(leaves)

        # Verify first, last, and a middle leaf
        for idx in [0, 499, 999]:
            leaf_hash = MerkleTree.leaf_hash(leaves[idx])
            proof = tree.get_proof(idx)
            assert MerkleTree.verify_proof(leaf_hash, proof, tree.root), (
                f"Verification failed for leaf index {idx}"
            )

    def test_large_tree_root_is_deterministic(self) -> None:
        leaves = [f"leaf-{i}".encode() for i in range(1000)]
        root_a = MerkleTree(leaves).root
        root_b = MerkleTree(leaves).root
        assert root_a == root_b


class TestLeafHash:
    """Static method for hashing individual leaves."""

    def test_bytes_input(self) -> None:
        result = MerkleTree.leaf_hash(b"test")
        expected = hashlib.sha256(b"test").hexdigest()
        assert result == expected

    def test_string_input(self) -> None:
        result = MerkleTree.leaf_hash("test")
        expected = hashlib.sha256(b"test").hexdigest()
        assert result == expected

    def test_output_is_64_char_hex(self) -> None:
        result = MerkleTree.leaf_hash(b"anything")
        assert len(result) == 64
        int(result, 16)  # valid hex

    def test_different_inputs_different_hashes(self) -> None:
        assert MerkleTree.leaf_hash(b"a") != MerkleTree.leaf_hash(b"b")
