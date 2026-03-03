# ADR-002 — Where's Waldo: Data Availability Analysis & Initiative Design

**Date:** 2026-03-03
**Status:** Draft
**Authors:** hj
**Scope:** Mapping the "Where's Waldo" PRD against available Talkdesk/Zendesk/Snowflake data; defining what's buildable now vs. what needs new data sources

---

## Context

The "Where's Waldo?" PRD proposes a Meeting ROI & Intelligence Engine with three pillars: Live Cost Calculator, Pre-Call Intelligence, and Resolution Tracker. The PRD targets calendar-based Sales/CS meetings (onboarding, upsell), but our richest available data is from Talkdesk (phone support) and Zendesk (ticketing). This analysis maps what we have, what's missing, and what we can build.

---

## 1. Data Points We Have

### Talkdesk — Phone Call Data (PROD_SOURCE_DB.TALKDESK)

| Data Point | Table.Column | PRD Relevance |
|-----------|-------------|---------------|
| Call duration (talk, wait, hold) | CALLS.talk_time, wait_time, hold_time | **Direct** — core input for cost calculation |
| Call type | CALLS.type (inbound/outbound/abandoned) | **Direct** — segment cost by interaction type |
| Ring group (team routing) | CALLS.ring_groups | **Direct** — proxy for team/level cost bands |
| Agent identity | CALLS.user_name, user_email | **Direct** — can map to level/band |
| Call outcome quality | CALLS.csat_score, disposition_code, mood | **Direct** — ties cost to outcome quality |
| Transfer flag | CALLS.is_transfer | **Direct** — transfers multiply cost (2+ agents) |
| Abandon timing | CALLS.abandon_time, CONTACTS.abandon_time | **Direct** — cost of wasted wait staffing |
| SLA compliance | CONTACTS.inside_service_level | **Direct** — ties cost to service quality |
| Agent availability | USER_STATUS | **Indirect** — idle time = invisible cost |
| IVR flow data | STUDIO_FLOW_EXECUTION | **Indirect** — self-service deflection rates |

### Zendesk — Ticket Data (PROD_SOURCE_DB.ZENDESK)

| Data Point | Table.Column | PRD Relevance |
|-----------|-------------|---------------|
| Ticket details | TICKETS.subject, description, status, priority | **Pre-Call Intel** — surface recent issues before call |
| AI-detected intent | TICKETS.custom_intent | **Pre-Call Intel** — predict why they're calling |
| Sentiment | TICKETS.custom_agatha_sentiment | **Pre-Call Intel** — caller mood context |
| Root cause taxonomy | TICKET_FIELD_ROOT_CAUSES | **Resolution Tracker** — categorize follow-up type |
| AI root causes | TICKET_FIELD_AI_ROOT_CAUSES | **Resolution Tracker** — auto-classify resolution |
| Full conversation thread | TICKET_COMMENTS | **Pre-Call Intel** — history of what was discussed |
| Tags (call origin) | TICKET_TAGS | **Linking** — talkdesk_call, talkdesk_abandoned, etc. |
| CSAT ratings | SATISFACTION_RATINGS | **Outcome** — did spending more time = better result? |
| Resolution metrics | ZENDESK__TICKET_METRICS | **Resolution Tracker** — first reply time, resolution time, wait time (business minutes) |

### Analytics Layer — Already-Built Enrichments (PROD_ANALYTICS_DB)

| Data Point | Table | PRD Relevance |
|-----------|-------|---------------|
| Enriched tickets (channel, CSAT, assignee) | CSO.FACT_CSO_ZENDESK_TICKET_DETAILS | **Direct** — Phone/Chat/Async classification already done |
| SLA breach/compliance per ticket | CSO.FACT_CSO_SLA_TRIGGERS | **Direct** — cost of SLA failures |
| Call → Ticket → Company bridge | TRANSCRIPTS.BRIDGE_TALKDESK_ZENDESK_COMPANIES | **Critical** — links cost to specific customer accounts |
| Enriched tickets with tags + roles | ZENDESK.ZENDESK__TICKET_ENRICHED | **Pre-Call Intel** — submitter role context |
| Business-minute metrics | ZENDESK.ZENDESK__TICKET_METRICS | **Resolution Tracker** — already calculated in business hours |

### Existing Redash Queries (Reusable)

| Query ID | What It Gives Us |
|---------|-----------------|
| 18398 | Daily call counts + avg abandon/wait — base for daily cost model |
| 10621 | Hourly volume by ring group — staffing cost optimization |
| 12138 | Per-agent daily breakdown — individual cost attribution |
| 17944 | Zendesk + Talkdesk + employee joined — **closest to PRD's multi-signal view** |
| 8709 | All tickets with root cause taxonomy — resolution cost categorization |
| 18922 | Transcript keyword search — discovery time analysis |
| 12227 | SLA compliance by ring group + date — service cost benchmarking |

---

## 2. Data Gaps (What We Don't Have)

| Gap | PRD Dependency | Severity | Workaround |
|-----|---------------|----------|------------|
| **Salary bands / HRIS levels** | Live Calculator needs $/hr per participant | **High** | Use anonymized "Average Cost per Level" by ring group as PRD suggests (e.g., Benefits Specialist = $X/hr, Admin = $Y/hr). HR provides ~6-8 band averages, not individual salaries. |
| **Calendar integration** | PRD targets scheduled meetings (onboarding, upsell) | **Medium** | Phase 1 focuses on Talkdesk calls (already captured). Calendar integration = Phase 2 scope for Sales/CS scheduled meetings. |
| **Product usage signals** | Pre-Call Intelligence wants "what they've been doing in-app" | **Medium** | Substitute with Zendesk history (recent tickets, root causes, sentiment). Product analytics integration = future phase. |
| **Call purpose tagging** | PRD wants Onboarding vs. Upsell segmentation | **Medium** | Ring group is a rough proxy. Can enrich with: Zendesk ticket tags, company tenure (new vs. existing), disposition codes. ML classification on call notes is a future option. |
| **Company revenue/size data** | Cost-to-serve ROI needs revenue context | **Low** | Bridge table links to companies. Company metadata (plan type, employee count, MRR) likely exists in another source system. |
| **Engineering follow-up costs** | Resolution Tracker needs "this bug fix = 4 eng hours" | **Low** | Out of scope for Phase 1. Can estimate from root cause category averages over time. |

---

## 3. What We Can Build — Mapped to PRD Pillars

### Pillar 1: The Cost Calculator → "Support Interaction Cost Engine"

**Adaptation:** Instead of calendar meetings, calculate the real cost of every support interaction — phone calls, tickets, and the hidden costs in between.

**Buildable Now:**
- **Per-call cost** = (agent_hourly_rate_by_ring_group × talk_time) + (agent_rate × hold_time) + (staffing_cost × wait_time_for_abandoned)
- **Per-ticket cost** = (agent_rate × handle_time) + (resolution_time × blended_rate)
- **Transfer cost multiplier** = calls with is_transfer=true cost 2x+ (multiple agents)
- **Abandoned call cost** = staffing cost burned while caller waited before abandoning
- **Cost by company** = aggregate via BRIDGE_TALKDESK_ZENDESK_COMPANIES
- **MoM/YoY trends** = 6 months of data already available
- **Cost by ring group** = immediate segmentation (payments calls cost more: 11 min avg talk + 2.3 min wait)

**Key Metric from Report Data:**
- ~48K calls/quarter × ~8.5 min avg talk time = ~6,800 agent-hours/quarter in talk time alone
- Add wait, hold, after-call work → likely 10,000+ agent-hours/quarter
- At even $40/hr blended rate, that's **$400K+/quarter in direct call costs**

### Pillar 2: Pre-Call Intelligence → "Caller Context Engine"

**Adaptation:** Instead of pre-meeting flash summaries, generate real-time caller context when an inbound call is routed to an agent.

**Buildable Now:**
- **Recent ticket history** for the caller's company (via bridge table)
- **Open/pending issues** from Zendesk with status, priority, root cause
- **AI-detected intent** from previous interactions (custom_intent field)
- **Sentiment trend** from Agatha sentiment scoring
- **Root cause patterns** — "This company's last 5 calls were all about payroll processing"
- **SLA context** — "This company has had 2 SLA breaches this month"

**Not Yet Buildable (needs new data):**
- Product usage signals ("they haven't logged into Time Tracking in 3 weeks")
- Scheduled meeting context (calendar integration)

### Pillar 3: Resolution Tracker → "Cost-of-Resolution Engine"

**Buildable Now:**
- **Resolution time tracking** — first reply time, full resolution time (already in ZENDESK__TICKET_METRICS, in business minutes)
- **Root cause categorization** — both human-tagged and AI-classified taxonomies exist
- **Follow-up volume** — count of ticket comments after initial call = proxy for follow-up burden
- **Channel escalation tracking** — did a call generate a ticket? Did a ticket generate a callback?
- **CSAT ↔ cost correlation** — do more expensive interactions produce better satisfaction?
- **SLA breach cost** — quantify the cost of missing SLA targets

---

## 4. High-Impact Opportunities from the Report Data

These are specific findings from the support report that the PRD's approach would directly address:

| Finding | Cost Implication | What the Tool Reveals |
|---------|-----------------|----------------------|
| Payments: 137s avg wait, 657s avg talk | Highest cost-per-interaction ring group | Quantifies the "payments is expensive" intuition into dollars |
| Monday: 784 calls, 110s wait, 4.81% abandon | Staffing mismatch = wasted cost | Shows dollar cost of Monday understaffing |
| December spike: 18.7K calls | Seasonal cost surge | Enables proactive budgeting for year-end |
| Time Tracking SLA: 56% → 39% | Degrading service = repeat calls = compounding cost | Shows the cost of not fixing the staffing gap |
| Spanish line: 28% abandon, 0 outbound | Underserved segment = hidden churn cost | Quantifies the cost of language gap |
| 1 PM peak: 283 abandoned calls | Peak-hour abandonment = wasted customer wait + lost resolution | Dollar value of optimizing lunch-hour coverage |
| Employee line: abandoned callers wait 8.3 min avg | Longest suffering before giving up | Cost of re-calls when these people call back |

---

## 5. Consequences & Tradeoffs

- **Talkdesk-first, Calendar-later**: We build on the richest data we have today. Calendar/meeting integration becomes a Phase 2 expansion.
- **Anonymized cost bands**: We use role-based averages, not individual salaries. Requires a one-time HR input of ~6-8 band rates.
- **Ring group as team proxy**: Not perfect (some calls route through multiple groups), but good enough for Phase 1.
- **No real-time integration yet**: Phase 1 is analytical/reporting. Real-time caller-context popups = Phase 2.
