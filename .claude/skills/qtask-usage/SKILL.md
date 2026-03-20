---
name: qtask-usage
description: Provide practical usage guidance for the qtask_list distributed queue library in this repository, including SmartQueue APIs, Worker handlers, result_queue pipelines, retry/DLQ/delay behavior, crash recovery, and CLI operations. Use when users ask how to install, configure, run, monitor, or troubleshoot qtask_list workflows, or request runnable examples for queue producers/consumers.
---

# qtask_list Usage

Use this skill to answer usage questions and produce runnable code for this repository's queue system.

## Execute Workflow

1. Confirm runtime assumptions first:
   - Python `3.10+`
   - Redis reachable via `REDIS_URL` (default `redis://localhost:6379/0`)
   - Editable install done (`pip install -e .`)
2. Choose the smallest valid pattern:
   - `SmartQueue` for producer/consumer basics
   - `Worker` for action handlers and concurrency
   - `result_queue` for multi-stage pipelines
   - CLI for ops and observability
3. Return concrete commands and code snippets that run in this repo.
4. Include verification commands (`python -m cli status ...`, `pytest ...`) when relevant.

## Apply Core Patterns

- Create queues with explicit namespace and queue name.
- Push tasks as dictionaries with clear `action` fields.
- In workers, register handlers with `@worker.on("<action>")`.
- Acknowledge success through normal handler completion; rely on retry/DLQ behavior on failure.
- For pipeline tasks, return the next stage payload and configure `result_queue`.
- Start downstream workers before upstream producers.

## Use Repository Conventions

- Prefer examples and commands from this repo over generic Redis queue advice.
- Use queue key model consistently:
  - `{namespace}:{queue}` main queue
  - `{namespace}:{queue}:processing`
  - `{namespace}:{queue}:retry`
  - `{namespace}:{queue}:dlq`
  - `{namespace}:{queue}:delay`
- Respect FIFO retry behavior and crash recovery flows.

## Load Extra Context On Demand

- Read `references/cli-commands.md` for CLI syntax and operator playbooks.
- Read `references/pipeline-example.md` when building multi-stage flows.
- Read `examples/simple_worker.py` for minimal worker scaffolding.

## Keep Responses Practical

- Prefer short, copy-runnable snippets.
- State startup order explicitly for pipeline setups.
- Include failure handling notes (retry, requeue, recover) for production-oriented questions.
