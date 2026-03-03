# AgentProof

## Development Workflow

### Task Lifecycle

Every task follows this flow:

1. **Plan** — Use the principal-architect-planner agent before writing code. Identify files to create/modify, interfaces, dependencies, and what can be parallelized.
2. **Build** — Implement the task. Maximize parallel work across independent pieces.
3. **Simplify** — Run `/simplify` after implementation to review for reuse, quality, and efficiency.
4. **Code Review** — Run `/code-review` after simplify to catch issues with severity-based reporting.
5. **Update Tracker** — Mark task done, update board, note any follow-up work or blockers discovered.

Steps 3 and 4 are mandatory before marking any task complete. Do not skip them.

### Parallelism Rules

- Always identify independent work that can happen simultaneously
- When multiple initiative tasks have no dependency between them, work them in parallel using background agents
- Within a single initiative, tasks are often sequential — respect the ordering unless explicitly independent
- Use worktree isolation when parallel streams touch overlapping files

### Environment Constraints

- **Local-first**: Everything runs on localhost via Docker Compose. No cloud deployments, no external services, no auth, no multi-tenancy in Phase 0.
- **Single `docker compose up`** should bring up the full stack: TimescaleDB, LiteLLM proxy, API server.
- Dashboard runs via `pnpm dev` on localhost:5173, API on localhost:8100, LiteLLM proxy on localhost:4000, Postgres on localhost:5432.

### Architecture Decisions

Store architecture decisions in `.tracker/decisions/` as `ADR-NNN-<slug>.md`. Reference these from initiative files when relevant. Format:
- **Context** — why this decision was needed
- **Decision** — what we chose
- **Consequences** — tradeoffs accepted

## Project Tracking System

This project uses a lightweight markdown-based tracker in `.tracker/`. Treat it as the source of truth for project status.

### Structure

```
.tracker/
  board.md                        # Master board — high-level view of all initiatives
  phases/
    phase-N/
      <id>-<slug>.md              # One file per initiative with tasks, owners, status
```

### How to Use the Tracker

**When the user asks about project status:**
- Read `.tracker/board.md` for the overview
- Read specific initiative files for task-level detail

**When the user asks to update a task or initiative:**
- Edit the specific initiative file (update checkbox, status, notes, blockers)
- Update `.tracker/board.md` to reflect any status changes
- Always update both files to keep them in sync

**When the user asks to add new work:**
- Create a new initiative file following the existing format
- Add a row to `board.md`

### Task Status Conventions

In initiative files, tasks use checkboxes:
- `- [ ]` — not started
- `- [~]` — in progress (add `**IN PROGRESS**` and owner name)
- `- [x]` — done
- `- [-]` — blocked (add `**BLOCKED:** reason`)

Initiative-level statuses in `board.md`:
- `not started` — no tasks begun
- `in progress` — at least one task active
- `blocked` — cannot proceed, note reason
- `done` — all tasks complete
- `cut` — descoped

### Conventions

- Owners are first names: `hj`, `TBD`, or team role placeholders (`infra`, `be1`, `be2`, `fe`, `ml`, `web3`)
- Dependencies reference initiative IDs like `0A`, `1C`
- Blockers go in the `## Blockers` section of each initiative file with a date
- When completing a task, add the date in `(done YYYY-MM-DD)` format
- Keep `board.md` and initiative files in sync — never update one without the other

### Quick Commands

Users may say things like:
- "show me the board" — read and display `board.md`
- "status on 1A" — read the specific initiative file
- "mark 0A-3 done" — check off that task, update board if initiative status changed
- "block 1B on 1A" — update blocker notes
- "add a task to 0C" — append a new task line to that initiative
- "what's next for [person]" — scan board for their assigned in-progress/not-started work
- "weekly standup" — summarize what's in progress, what's blocked, what finished recently
