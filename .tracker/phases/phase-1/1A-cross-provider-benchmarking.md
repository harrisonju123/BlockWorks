# 1A — Cross-Provider Benchmarking Engine

**Status:** not started
**Owner:** ml + be2
**Target:** Weeks 6–12
**Dependencies:** 0A (data pipeline), 0B (task classifier)
**Blocks:** 1B, 1C, 2B, 3A, 3B, 3D

## Objective

Silently benchmark alternative models against production traffic. Build a model-task fitness matrix that answers: for each task type, which models deliver acceptable quality at what cost?

## Tasks

- [ ] **1A-1** Configure LiteLLM traffic mirroring — configurable sample rate per org — `be2`
- [ ] **1A-2** Build LLM-as-judge evaluation framework (Haiku scoring outputs on task-specific rubric) — `ml`
- [ ] **1A-3** Design rubric schema per task type (code gen: correctness + style, classification: accuracy, summarization: completeness + conciseness, etc.) — `ml`
- [ ] **1A-4** Build model-task fitness matrix data structure and continuous update pipeline — `ml`
- [ ] **1A-5** Anonymization layer for benchmark requests (content hashing, org-level aggregation) — `be2`
- [ ] **1A-6** Cross-org anonymous aggregation for network-wide fitness data (opt-in) — `be2`

## Technical Notes

- Users must opt into benchmarking and control sample rate
- Benchmark requests use hashed/anonymized content
- Fitness matrix: task_type × model → {quality_score, avg_cost, avg_latency, sample_size}
- LLM-as-judge: use Haiku for cost efficiency — design rubrics to minimize judge bias
- Store benchmark results in separate TimescaleDB table, linked to original trace by hash

## Blockers

_None_
