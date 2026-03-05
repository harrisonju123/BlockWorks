"""Provider-aware request body sanitization for cross-provider routing.

When smart routing overrides a model to a different provider, the request body
may contain provider-specific parameters that the target rejects.  LiteLLM
translates the core message format (messages, tools, model, temperature, etc.)
but passes through unknown top-level params verbatim — causing 400s.

This module strips those params based on the target provider so each provider
only sees parameters it understands.

Usage in the proxy layer::

    from blockthrough.routing.sanitize import sanitize_for_target
    sanitize_for_target(body, source_model="claude-opus-4-6", target_model="gpt-5.2-chat-latest")
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blockthrough.utils import infer_provider


@dataclass(frozen=True)
class ProviderSpec:
    """Declares provider-specific params and content block types.

    ``exclusive_params`` — top-level body keys that ONLY this provider accepts.
    ``exclusive_content_types`` — content block types in messages that only this
        provider produces/accepts (e.g. Anthropic ``thinking`` blocks).
    """

    exclusive_params: frozenset[str] = field(default_factory=frozenset)
    exclusive_content_types: frozenset[str] = field(default_factory=frozenset)


# Each entry lists params/blocks that are EXCLUSIVE to that provider.
# When routing AWAY from a provider, its exclusive params get stripped.
PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        exclusive_params=frozenset({
            "output_config",  # structured output config
            "thinking",       # extended thinking toggle
            "top_k",          # Anthropic-only sampling param
            "metadata",       # request metadata (user_id tracking)
        }),
        exclusive_content_types=frozenset({
            "thinking",       # extended thinking content blocks
        }),
    ),
    "openai": ProviderSpec(
        exclusive_params=frozenset({
            "logprobs",
            "top_logprobs",
            "logit_bias",
            "parallel_tool_calls",
            "service_tier",
            "response_format",  # OpenAI structured output format
        }),
    ),
}


def sanitize_for_target(
    body: dict,
    *,
    source_model: str,
    target_model: str,
) -> None:
    """Strip provider-specific params when routing crosses provider boundaries.

    Mutates ``body`` in-place.  No-op when source and target are the same
    provider.
    """
    source_provider = infer_provider(source_model)
    target_provider = infer_provider(target_model)

    if source_provider == target_provider:
        return

    source_spec = PROVIDER_SPECS.get(source_provider)
    if source_spec is None:
        return

    # Strip top-level params exclusive to the source provider
    for key in source_spec.exclusive_params:
        body.pop(key, None)

    # Strip content block types exclusive to the source provider
    if source_spec.exclusive_content_types:
        _strip_content_block_types(body, source_spec.exclusive_content_types)

    # OpenAI requires every tool_call_id to have a matching tool response.
    if target_provider == "openai":
        repair_tool_pairing(body)


def repair_tool_pairing(body: dict) -> None:
    """Fix orphaned tool calls and normalize tool messages for LiteLLM translation.

    Safe to call unconditionally — no-op when there are no issues.
    Should be called before forwarding to any upstream that might route to
    OpenAI, since OpenAI strictly rejects orphaned tool_call_ids.

    Also splits mixed user messages (tool_result + text blocks) into separate
    messages so LiteLLM can cleanly translate them to OpenAI format.
    """
    _repair_tool_call_pairing(body)
    _repair_anthropic_tool_pairing(body)
    _normalize_tool_use_ids(body)
    _split_mixed_tool_result_messages(body)


def _repair_tool_call_pairing(body: dict) -> None:
    """Ensure every assistant tool_call has a matching tool response message.

    OpenAI rejects requests where an assistant message has tool_calls but the
    subsequent messages don't include tool-role responses for every call ID.
    This can happen when conversations are truncated mid-tool-use or when
    format translation drops messages.

    Strategy: collect all tool_call_ids from assistant messages and all
    responded-to IDs from tool messages. For any orphaned call IDs, remove
    the tool_call entry from the assistant message rather than fabricating
    a fake response.
    """
    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return

    # Collect all tool_call_ids that have a tool-role response
    responded_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            tcid = msg.get("tool_call_id")
            if tcid:
                responded_ids.add(tcid)

    # Walk assistant messages and prune orphaned tool_calls
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if not tcs or not isinstance(tcs, list):
            continue

        pruned = [tc for tc in tcs if tc.get("id") in responded_ids]
        if len(pruned) == len(tcs):
            continue  # all paired, nothing to do

        if pruned:
            msg["tool_calls"] = pruned
        else:
            # No tool calls left — remove the key entirely so the message
            # is treated as a plain assistant message
            del msg["tool_calls"]


def _repair_anthropic_tool_pairing(body: dict) -> None:
    """Fix orphaned tool_use blocks in Anthropic-format messages.

    Anthropic uses content blocks: assistant messages contain tool_use blocks
    (with an id), and user messages contain tool_result blocks (referencing
    tool_use_id). When LiteLLM translates these to OpenAI format, orphaned
    tool_use blocks become orphaned tool_calls — causing the same 400.
    """
    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return

    # Collect all tool_use_ids that have a tool_result response
    responded_ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tuid = block.get("tool_use_id")
                if tuid:
                    responded_ids.add(tuid)

    # Prune orphaned tool_use blocks from assistant messages
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        original_len = len(content)
        msg["content"] = [
            block for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") not in responded_ids
            )
        ]
        # If we stripped all content, keep an empty string so the message
        # isn't malformed
        if not msg["content"] and original_len > 0:
            msg["content"] = ""


def _normalize_tool_use_ids(body: dict) -> None:
    """Rewrite non-Anthropic tool_use IDs so LiteLLM translates them correctly.

    When a conversation is round-tripped through OpenAI (via LiteLLM routing),
    tool_use blocks end up with OpenAI's ``call_`` ID prefix instead of
    Anthropic's ``toolu_`` prefix.  LiteLLM's Anthropic→OpenAI translator
    relies on ID format to map tool_use/tool_result pairs — ``call_`` prefixed
    IDs break this mapping, causing orphaned tool_call_id errors.

    Rewrites IDs in-place and updates matching tool_result references.
    """
    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return

    # Build a rename map: old_id → new_id for any non-toolu_ IDs
    rename: dict[str, str] = {}
    counter = 0

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            old_id = block.get("id", "")
            if old_id and not old_id.startswith("toolu_"):
                counter += 1
                new_id = f"toolu_proxy_{counter:04d}"
                rename[old_id] = new_id
                block["id"] = new_id

    if not rename:
        return

    # Update corresponding tool_result references
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            old_ref = block.get("tool_use_id", "")
            if old_ref in rename:
                block["tool_use_id"] = rename[old_ref]


def _split_mixed_tool_result_messages(body: dict) -> None:
    """Split user messages that mix tool_result and text blocks into separate messages.

    LiteLLM struggles to translate mixed user messages like:
        {"role": "user", "content": [tool_result, text, text, ...]}
    into the OpenAI format, which requires separate message roles:
        {"role": "tool", ...}  +  {"role": "user", ...}

    By splitting them before LiteLLM sees them, the translation works cleanly.
    """
    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return

    new_messages: list[dict] = []
    changed = False

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            new_messages.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            new_messages.append(msg)
            continue

        tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        other_blocks = [b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")]

        if not tool_results or not other_blocks:
            # Pure tool_result or pure text — no split needed
            new_messages.append(msg)
            continue

        # Split: tool_result blocks first, then remaining content
        changed = True
        new_messages.append({"role": "user", "content": tool_results})
        new_messages.append({"role": "user", "content": other_blocks})

    if changed:
        body["messages"] = new_messages


def _strip_content_block_types(
    body: dict,
    block_types: frozenset[str],
) -> None:
    """Remove content blocks of the given types from assistant messages."""
    for msg in body.get("messages", []):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        msg["content"] = [
            blk for blk in content
            if not (isinstance(blk, dict) and blk.get("type") in block_types)
        ]
