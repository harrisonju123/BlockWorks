# 1A — Cross-Provider Benchmarking Engine

**Status:** done
**Owner:** ml + be2
**Target:** Weeks 6–12
**Dependencies:** 0A (data pipeline), 0B (task classifier)
**Blocks:** 1B, 1C, 2B, 3A, 3B, 3D

## Objective

Silently benchmark alternative models against production traffic. Build a model-task fitness matrix that answers: for each task type, which models deliver acceptable quality at what cost?

## Tasks

- [x] **1A-1** Configure LiteLLM traffic mirroring — configurable sample rate per org — `be2` (done 2026-03-03)
- [x] **1A-2** Build LLM-as-judge evaluation framework (Haiku scoring outputs on task-specific rubric) — `ml` (done 2026-03-03)
- [x] **1A-3** Design rubric schema per task type — 7 rubrics covering all TaskTypes — `ml` (done 2026-03-03)
- [x] **1A-4** Build model-task fitness matrix data structure and continuous aggregate pipeline — `ml` (done 2026-03-03)
- [x] **1A-5** Anonymization layer — content hashing via existing hasher, org-level aggregation — `be2` (done 2026-03-03)
- [x] **1A-6** Cross-org anonymous aggregation — fitness_matrix continuous aggregate — `be2` (done 2026-03-03)

## Technical Notes

- `schema_benchmarks.sql`: benchmark_results hypertable + fitness_matrix continuous aggregate
- `benchmarking/judge.py`: 7 task-specific rubrics, Haiku as judge, structured JSON parsing with fence-stripping
- `benchmarking/mirror.py`: should_sample() gate, asyncio.gather for concurrent model replays, BenchmarkWorker async queue
- `api/routes/benchmarks.py`: fitness matrix, paginated results, config read/write endpoints
- 39 unit tests covering judge rubrics, sampling logic, fitness matrix queries
- Benchmark model replays now run concurrently via asyncio.gather (fixed in simplify)
- BenchmarkWorker writes wrapped in transaction (fixed in simplify)

## Blockers

_None_
