# 0B — Task Classification Engine

**Status:** in progress
**Owner:** ml
**Target:** Weeks 1–5
**Dependencies:** none (integrates with 0A around W4)
**Blocks:** 1A, 1B, 0C (waste score)

## Objective

Classify every LLM call by task type so the system can evaluate whether the right model was used. This is the foundation of waste detection — you can't say "this didn't need Opus" without knowing what the task was.

## Tasks

- [x] **0B-1** Define task taxonomy: code generation, classification, summarization, extraction, reasoning, conversation, tool selection — `ml` (done 2026-03-03)
- [x] **0B-2** Build rules-based heuristic classifier on prompt structure (system prompt patterns, tool call presence, output format hints) — `ml` (done 2026-03-03)
- [ ] **0B-3** Evaluate fine-tuned DistilBERT vs rules-based approach — target <5ms classification latency — `ml`
- [ ] **0B-4** Build training/eval dataset from synthetic prompts across each task category — `ml`
- [x] **0B-5** Integration point: classifier hooks into callback pipeline from 0A — `ml + be1` (done 2026-03-03)

## Technical Notes

- Rules-based classifier shipped and wired into the callback pipeline
- Extracts structural signals: tool presence, code fences, JSON schema, keyword matching, token ratios
- Confidence scoring normalized to 0-1 range, falls back to UNKNOWN below 0.2 threshold
- 7 unit tests passing for rules classifier
- DistilBERT evaluation (0B-3) and synthetic dataset (0B-4) still needed to determine if ML approach adds value

## Blockers

_None_
