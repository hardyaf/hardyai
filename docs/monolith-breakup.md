# Monolith Breakup Completion Record

Status: complete and verified on the authoritative Ubuntu deployment on 2026-08-24.

## Outcome

The breakup is successful at the behavioral and dependency boundaries that matter most:

- `JarvisRouter.route` is now a two-line facade over `RequestFlowCoordinator`.
- Main action commitment, Main repair, pending clarification, conversational follow-up, plan execution,
  context assembly, session transitions, action execution, and turn finalization have separate owners.
- Domain entity normalization and clarification policy live in skill context contracts rather than router
  intent branches.
- Direct HTTP actions use the same registry-authorized executor, ticket path, receipt generation, and
  finalization boundary as conversational actions.
- FastAPI routes resolve services through `ApplicationContainer`; route imports no longer construct or
  import the global runtime.
- SQLite connection configuration, schema DDL, ordered migrations, and reusable transaction policy live
  under `app/db`.
- Core runtime, skill catalog, and scheduler callers use bounded persistence adapters rather than receiving
  the broad SQLite store directly at the composition root.
- Every finalized response now reports write delivery states. Synchronous writes report `committed`, and
  the durable memory outbox reports `queued|committed|failed` with stable job and operation identifiers.

## Size And Shape

Before this completion pass, `app/core/router.py` was approximately 5,000 lines and contained a 775-line
top-level route method. It is now approximately 1,300 lines, mostly construction, compatibility wrappers,
and small policy ports; the router facade is two lines. `RequestFlowCoordinator.route` is 21 lines and its
prepare, guard, interpret, route, tool, and Main stages are independently bounded to at most 200 lines.

`app/api/routes/house.py` is under 50 lines and contains no registry lookup, hard-coded skill ID, ticket
or receipt construction, or direct mutation dispatch.

The former baseline schema block was removed from `SQLiteStore`. All application DDL is constrained to
`app/db`, and all direct `sqlite3.connect` calls are constrained to `app/db/connection.py`.

## Runtime Flow

```text
HTTP / Discord adapter
        |
        v
ApplicationContainer -> TurnService -> RequestFlowCoordinator
                                         |        |        |
                                         v        v        v
                                  Main repair  Clarify  Conversation
                                         \        |        /
                                          ActionExecutionService
                                                    |
                                             TurnFinalizer
                                                    |
                                      ticket/session/event/history
                                                    |
                                      durable memory outbox receipt
```

## Enforcement

`tests/unit/test_architecture_boundaries.py` and the dedicated CI step enforce these ratchets:

- HTTP adapters and dependency providers cannot import `app.runtime`.
- schema DDL cannot leave `app/db`;
- direct SQLite connection creation cannot leave `app/db/connection.py`;
- the router and house route cannot grow past their current size budgets; and
- `JarvisRouter.route` cannot grow beyond three lines, while no request-flow stage can exceed 200 lines.

## Verification

The final release passed:

- Python compilation for `app`, `scripts`, and `tests`;
- fatal Ruff checks (`E9`, `F63`, `F7`, `F82`);
- all 461 tests on Ubuntu;
- `git diff --check` and the public-tree publication scan;
- a checksum-verified sanitized release export and isolated Ubuntu image build;
- a pre-deploy source, environment, protected-configuration, durable-data, and online SQLite backup;
- post-start SQLite integrity, exact-source synchronization, container health, and HTTP `/health` checks;
- direct Qwen 2.5 7B and GPT-OSS 20B inference probes plus a model-backed authenticated Main turn;
- live SearXNG, Google Calendar read, Main clarification, and idempotent direct-house action checks; and
- Discord connection plus a read-only explicit-envelope Micro routing check.

The deployment test found and closed two gaps that the earlier assertions did not catch. The production
verifier now authenticates its loopback smoke turn without sending the operator key over plain remote HTTP.
Explicit Discord commands also refresh deterministic ownership after domain entity normalization, so a
normalized complete read command executes through `micro_tool`; unprefixed, incomplete, ambiguous, and
Main-only mutating requests still fail closed to Main.

The clean exports used for this pass were disposable release artifacts, not deployment authorities or
second canonical checkouts.

## Remaining Risks

The breakup is complete, with these bounded follow-up concerns:

- `app.runtime` remains a compatibility composition root for existing workers and tests. HTTP adapters no
  longer depend on it, but a future pass can move worker construction into container factories and retire
  the global aliases.
- The router still exposes compatibility wrappers used by existing tests and coordinator ports. The size
  ratchet prevents regrowth; future changes should narrow those ports instead of adding router policy.
- Memory is the only asynchronous generic durable-write job today. Other acknowledged writes are committed
  synchronously before the response is returned; new asynchronous write paths must register a durable job
  handler and expose delivery state before they may return success.
- The production image still reports the known Starlette/httpx and Python `audioop` deprecation warnings.
- The explicit Discord Micro check used the production command-envelope and router path without posting a
  synthetic message into a real household channel. The live bot connection and adapter policy loaded
  successfully; the next organic `!` command remains the end-to-end transport confirmation.
- Windows rollback state was not changed. Observe the Ubuntu deployment before any separately approved
  rollback-runtime retirement.
