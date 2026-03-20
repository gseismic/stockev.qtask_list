---
name: qtask-list-library
description: Use this skill when users ask how to use, integrate, debug, or document the qtask_list Redis task queue library, including SmartQueue/Worker APIs, CLI commands, retry and DLQ flows, delay tasks, and crash recovery operations.
---

# qtask_list Library

## Trigger
Use this skill for requests about `qtask_list` usage, API examples, worker setup, queue troubleshooting, operations playbooks, or writing docs/tutorials for this repository.

## Workflow
1. Confirm the request type:
- API usage (`SmartQueue`, `Worker`, `RemoteStorage`, `TaskHistory`)
- CLI operations (`python -m cli ...`)
- Ops/debugging (retry, DLQ, delay, recover, backlog, history cleanup)
- Documentation generation

2. Ground answers in repository evidence:
- Start with `README.md` for user-facing flows.
- Verify behavior in tests under `tests/` before stating edge-case semantics.
- Use exports in `qtask_list/__init__.py` to define public API boundaries.

3. Explain queue lifecycle with exact key model:
- `{namespace}:{queue_name}` (ready)
- `{namespace}:{queue_name}:processing`
- `{namespace}:{queue_name}:retry`
- `{namespace}:{queue_name}:dlq`
- `{namespace}:{queue_name}:delay` (sorted set)

4. Provide executable guidance:
- Include concrete install/test/worker/monitor commands.
- For code samples, keep Python 3.10+ compatibility and explicit type hints where practical.
- Use `loguru`-style error handling in examples and avoid bare `except:`.

5. Troubleshooting order (default):
- Verify `REDIS_URL` and Redis connectivity.
- Verify queue full name (`namespace:queue`) and worker startup order in pipelines.
- Inspect ready/processing/retry/dlq/delay counts.
- Apply `retry`, `requeue`, and `recover` commands based on queue state.
- Inspect task history (`history`, `clean-history`, archive/monitor when needed).

## Output Patterns
For quick-start asks:
- Give minimal install + enqueue + worker example.

For debugging asks:
- Ask for `REDIS_URL`, queue full name, worker command, one failed task sample, and current queue stats.
- Return a step-by-step checklist with exact CLI commands.

For documentation asks:
- Structure output as API surface -> lifecycle model -> CLI flows -> failure/recovery playbooks -> operational caveats.

## Constraints
- Prefer source-and-test-backed claims over assumptions.
- Keep advice compatible with this repository conventions:
- Redis `decode_responses=True`
- `rpush` for FIFO retry queue behavior
- Redis pipeline/Lua for batch or atomic transitions
- Shared `redis_client` for multi-worker scenarios

## References
Load [references/qtask-list-reference.md](references/qtask-list-reference.md) for condensed API/CLI/config/ops details.
