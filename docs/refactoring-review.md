# Refactoring Review

Status: 2026-09-30

This document records the current refactoring state of `personal-agent` and
`training-coach-agent`, the remaining implementation work, and deferred
improvements.

## Current Architecture

- `personal-agent` owns routing between general conversation, Home Assistant,
  and the Training Coach API.
- `training-coach-agent` owns training memory, tools, injury assessment,
  synchronization, Telegram, and the FastAPI API.
- `plan_generator` and `ntc_catalog` remain independent submodules.
- Shared Intervals.icu access and generic pgvector memory are consumed as
  separate external packages.
- The router and Training Coach bridge expose OpenAI-compatible interfaces for
  Open WebUI.

The previous architecture plan deliberately rejected a single `agent-toolkit`
monorepo. This review keeps that decision: extraction is justified only when a
capability is genuinely shared and has a stable interface.

## Completed Refactoring

- `personal-agent`, `plan_generator`, and `ntc_catalog` have clear repository
  boundaries.
- Intervals.icu access was extracted into `intervals-icu-client`.
- Generic Postgres/pgvector connection and semantic-search behavior was
  extracted into `pgvector-agent-memory`.
- Training-specific adapters remain in `training-coach-agent`.
- The plan generator has the strongest regression coverage and an end-to-end
  Makefile workflow.

## Pending Implementation

### P1: Service ownership and ordering

`stack.sh` manages five user services, but this repository tracks only the
Training Coach API and MCP bridge unit files. The router, Training Coach
bridge, and Telegram units are installed locally but are external to this
checkout. The MCP unit also names `personal-agent-router.service` as an
ordering dependency.

Action:

- Keep the external-unit model explicit in the README.
- Add `After=personal-agent-router.service` and `After=training-coach-api.service`
  only where the unit owns a real dependency.
- Keep readiness ordering in `stack.sh` as the source of truth for startup.
- Add a lightweight check that reports missing external units clearly instead
  of implying that this repository can install them.

Acceptance:

- `systemctl --user list-unit-files` shows the five expected installed units.
- `./stack.sh check` reports actionable failures.
- README setup instructions distinguish tracked units from externally managed
  units.

### Completed: API resource cleanup

`training-coach-agent/api.py` closes `coach_tools.memory` during FastAPI
shutdown, but `injury_agent.py` owns a separate lazy memory singleton. The
lifespan must close both objects and remain safe when either was never
initialized.

Acceptance:

- Both cleanup functions are called once on normal shutdown.
- Cleanup remains exception-safe and idempotent.
- No database connection is created merely by importing the API.

The router, Training Coach API, injury agent, and temporary NTC suggestion
memory now have explicit cleanup coverage. No unified pool abstraction was
introduced because current evidence does not justify one.

### Completed: Characterization coverage

Offline tests now cover the highest-risk behavior with external services
mocked:

- Router route detection, fallback classification, utility filtering, and
  conversation IDs.
- Training Coach API authentication, chat history, reset, sync errors, and
  lifespan cleanup.
- Injury signal extraction and memory lifecycle.
- Bridge OpenAI-compatible model and completion responses.
- Sync pipeline cleanup and Telegram authorization, commands, and message
  orchestration.

Telegram handler coverage is now included in the focused suite. Isolated
database integration tests remain future work.

The existing `plan_generator/tests` suite remains a regression gate and should
not be rewritten as part of this work.

The repository now provides `requirements-dev.txt` files for installing pytest
without adding test tooling to production runtime requirements.

### Completed: Shared conversation utilities

`app.py` and `training-coach-bridge/bridge.py` now share the header names,
utility markers, filtering, and stable-ID resolution through
`personal_agent_common/conversation.py`. Existing prefixes and precedence are
covered by tests.

### Completed: Database ownership fixes

The coach tools use an import-time singleton, the injury agent uses a lazy
singleton, and plan/NTC tools create per-call database objects. Long-lived
objects close through application lifespans, and temporary NTC memory now
closes immediately. Per-operation plan-generator cleanup is retained.
No speculative pool abstraction was added.

### Completed: Centralized submodule imports

`plan_tools.py` and `ntc_tools.py` now use the shared `import_utils.py` helper
for the required submodule path setup.

### Completed: Transient OpenAI retries

The personal-agent route retries transport failures and transient HTTP statuses
with bounded exponential backoff, while client errors remain non-retryable.

### Completed: MCP authentication

The MCP bridge enforces `MCP_BRIDGE_TOKEN` when configured, keeps `/health`
public, and the stack protocol check loads `.env` so it authenticates
consistently with the systemd service.

## Nice-to-Have Backlog

- Formal OpenAI-compatible API contract and generated examples.
- Metrics and alerts for latency, database usage, sync backlog, embedding
  failures, and fact-recall hit rate.
- Cross-agent fact consistency validation.
- Isolated database integration tests and full Open WebUI/Telegram smoke tests.
- Conversation-ID collision telemetry or longer IDs if operational evidence
  warrants it.
- A broader shared-memory abstraction only if the schemas begin to overlap.

## Validation Commands

```bash
python -m compileall app.py fact_extraction.py memory.py mcp_bridge.py training-coach-bridge
python -m compileall training-coach-agent
python -m unittest discover -s tests -v
cd training-coach-agent/plan_generator && python -m pytest -q
cd ../.. && bash -n stack.sh && ./stack.sh check
```

Focused tests should run with OpenAI, Supabase, Intervals.icu, Home Assistant,
and Telegram mocked. Live stack checks are environment-dependent and should be
reported separately from offline test results.

## Explicit Non-Goals

- No new `agent-toolkit` repository.
- No database schema migration.
- No broad router rewrite.
- No monitoring infrastructure in the core refactor.
- No removal of existing Telegram commands or public API fields.