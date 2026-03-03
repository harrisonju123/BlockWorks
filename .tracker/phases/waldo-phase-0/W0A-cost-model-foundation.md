# W0A — Cost Model Foundation

**Status:** not started
**Owner:** hj
**Target:** Week 1–2
**Dependencies:** none
**Blocks:** W0B, W0C, W1A, W1B, W1C

## Objective

Define the cost model that turns raw Talkdesk/Zendesk data into dollar values. This is the single most important artifact — every downstream report and dashboard depends on it.

## Tasks

- [ ] **W0A-1** Define cost bands per ring group / role level. Work with HR/Finance to get anonymized average hourly rates for ~6-8 role bands (e.g., Benefits Specialist, CSM, Admin Support, Payments Specialist, etc.)
- [ ] **W0A-2** Define the cost formula: `call_cost = (agent_rate × talk_time_hrs) + (agent_rate × hold_time_hrs) + (staffing_cost × wait_time_hrs_for_abandoned)`. Document edge cases: transfers (multi-agent), callbacks, voicemails.
- [ ] **W0A-3** Validate the formula against a sample month (Jan 2026: 15,851 calls) — produce a sanity-check total cost estimate and get stakeholder gut-check.
- [ ] **W0A-4** Define the "cost of abandoned call" model — caller wait time × staffing cost + estimated re-call cost (% of abandoned that call back × avg cost of a completed call).
- [ ] **W0A-5** Document the cost model as a shared spec that analytics, dashboards, and reports all reference. Store in `.tracker/decisions/`.

## Acceptance Criteria

- A written cost model spec with formulas, assumptions, and band rates
- One validated sample month with total cost breakdown by ring group
- Stakeholder sign-off that the numbers pass the smell test

## Open Questions

- Do we include after-call work (ACW) time? Talkdesk may have this in USER_STATUS or handle_time.
- Should we weight transfers differently (e.g., warm transfer vs. cold transfer)?
- Do we want to include ticket resolution cost in the per-call cost, or keep them separate?

## Blockers

_None_
