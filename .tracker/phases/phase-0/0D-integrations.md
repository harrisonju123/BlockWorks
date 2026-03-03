# 0D — Initial Integrations

**Status:** done
**Owner:** be2
**Target:** Weeks 3–7
**Dependencies:** 0A (callback handler working)
**Blocks:** none (enables adoption)

## Objective

Validate that the LiteLLM proxy + callback approach works with major AI agent frameworks.

## Tasks

- [x] **0D-1** Claude Code integration guide + validation — `be2` (done 2026-03-03)
- [x] **0D-2** OpenCode integration guide + validation — `be2` (done 2026-03-03)
- [x] **0D-3** LangChain integration via LiteLLM proxy — `be2` (done 2026-03-03)
- [x] **0D-4** CrewAI integration + validation — `be2` (done 2026-03-03)
- [x] **0D-5** Automated integration test suite — `be2` (done 2026-03-03)

## Technical Notes

- All 4 frameworks: Claude Code, OpenCode, LangChain, CrewAI
- LangChain: proxy approach (ChatOpenAI) recommended over ChatAnthropic
- CrewAI: just 2 env vars since it uses LiteLLM internally
- Framework tests auto-skip when services/packages unavailable
- Validation script covers 5-step end-to-end verification

## Blockers

_None_
