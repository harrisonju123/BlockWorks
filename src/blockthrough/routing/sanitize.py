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
