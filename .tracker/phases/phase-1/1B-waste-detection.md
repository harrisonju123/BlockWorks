# 1B — Waste Detection & Recommendations

**Status:** not started
**Owner:** ml + fe
**Target:** Weeks 10–14
**Dependencies:** 0B (classifier), 1A (fitness matrix)
**Blocks:** none (drives adoption)

## Objective

Analyze usage patterns and flag specific waste categories with dollar amounts. This is the feature that makes the "we saved you $X" claim concrete.

## Tasks

- [ ] **1B-1** **Model overkill detector** — compare task classification against fitness matrix, flag when expensive model used for task where cheaper model scores >90% — `ml`
- [ ] **1B-2** **Redundant call detector** — identify identical tool calls within a trace (hash input args, flag duplicates) — `ml`
- [ ] **1B-3** **Context bloat analyzer** — measure system prompt token count vs output influence (ablation heuristic or token-level analysis) — `ml`
- [ ] **1B-4** **Cache miss detector** — semantic similarity between requests within configurable time window, estimate savings from prompt caching — `ml`
- [ ] **1B-5** **Agent loop detector** — pattern match fix-break-fix cycles in coding agent traces (repeated similar edits to same file) — `ml`
- [ ] **1B-6** Weekly waste report generation — email/Slack-ready format with $ amounts per category — `fe`

## Technical Notes

- Each detector outputs: category, severity, affected traces, estimated monthly savings
- Waste score = weighted sum of detector outputs normalized to 0–100
- Reports should be actionable: "Switch these 340 classification calls from Opus to Haiku → save $1,200/month"
- Agent loop detection: look for repeated tool calls to same file/function with rollbacks between them

## Blockers

_None_
