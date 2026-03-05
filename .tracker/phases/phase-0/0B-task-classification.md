# 0B — Task Classification Engine

**Status:** done
**Owner:** ml
**Target:** Weeks 1–5
**Dependencies:** none (integrates with 0A around W4)
**Blocks:** 1A, 1B, 0C (waste score)

## Objective

Classify every LLM call by task type so the system can evaluate whether the right model was used.

## Tasks

- [x] **0B-1** Define task taxonomy: code generation, classification, summarization, extraction, reasoning, conversation, tool selection — `ml` (done 2026-03-03)
- [x] **0B-2** Build rules-based heuristic classifier on prompt structure — `ml` (done 2026-03-03)
- [x] **0B-3** Evaluate classifier accuracy — target >75% — `ml` (done 2026-03-03)
- [x] **0B-4** Build training/eval dataset from synthetic prompts across each task category — `ml` (done 2026-03-03)
- [x] **0B-5** Integration point: classifier hooks into callback pipeline from 0A — `ml + be1` (done 2026-03-03)

## Technical Notes

- Rules-based classifier achieved 86.6% accuracy on 82-example synthetic dataset
- Per-type recall: tool_selection 100%, code_gen 92%, classification 91%, conversation 91%, extraction 85%, reasoning 77%, summarization 70%
- Confidence separation: correct predictions avg 0.538, incorrect avg 0.255
- At confidence threshold >0.5, accuracy reaches 100%
- Decision: rules-based is sufficient for Phase 0. DistilBERT evaluation deferred — rules already exceed the 75% target
- `blockthrough evaluate` CLI command runs the eval harness

## Blockers

_None_
