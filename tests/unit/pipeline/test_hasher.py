"""Tests for canonical content hashing."""

from agentproof.pipeline.hasher import hash_content


class TestHashContent:
    def test_plain_text(self):
        result = hash_content("hello world")
        assert len(result) == 64  # SHA-256 hex length
        assert result == hash_content("hello world")

    def test_strips_whitespace(self):
        assert hash_content("  hello  ") == hash_content("hello")

    def test_json_key_order_independent(self):
        """Same JSON with different key ordering should hash identically."""
        a = '{"b": 2, "a": 1}'
        b = '{"a": 1, "b": 2}'
        assert hash_content(a) == hash_content(b)

    def test_dict_input(self):
        d = {"z": 1, "a": 2}
        s = '{"a": 2, "z": 1}'
        assert hash_content(d) == hash_content(s)

    def test_list_of_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        result = hash_content(msgs)
        assert len(result) == 64
        # Same messages should produce same hash
        assert result == hash_content(msgs)

    def test_different_content_different_hash(self):
        assert hash_content("hello") != hash_content("world")
