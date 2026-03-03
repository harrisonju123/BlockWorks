# 0D — Initial Integrations

**Status:** not started
**Owner:** be2
**Target:** Weeks 3–7
**Dependencies:** 0A (callback handler working)
**Blocks:** none (enables adoption)

## Objective

Validate that the LiteLLM proxy + callback approach works seamlessly with the major AI agent frameworks. Write integration guides for each.

## Tasks

- [ ] **0D-1** Claude Code integration guide + validation (via LiteLLM proxy config) — `be2`
- [ ] **0D-2** OpenCode integration guide + validation (OpenAI-compatible endpoint) — `be2`
- [ ] **0D-3** LangChain integration via LiteLLM Python SDK callbacks — `be2`
- [ ] **0D-4** CrewAI integration + validation — `be2`
- [ ] **0D-5** Automated integration test suite — spin up each framework, run sample workflow, verify traces captured — `be2`

## Technical Notes

- Claude Code and OpenCode: just point at LiteLLM proxy URL — should be config-only, no code changes
- LangChain: may need to use LiteLLM's Python SDK callback in addition to proxy approach
- Each guide should include: setup steps, sample config, expected output, troubleshooting
- Integration tests should run in CI (Docker-based)

## Blockers

_None_
