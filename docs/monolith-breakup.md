# Monolith Breakup Completion Record

Status: locally complete and release-candidate verified on 2026-08-24. Ubuntu promotion is still a separate operational gate.

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

The local release candidate passed:

- Python compilation for `app`, `scripts`, and `tests`;
- fatal Ruff checks (`E9`, `F63`, `F7`, `F82`);
- all 460 tests;
- `git diff --check`; and
- a sanitized clean export checked by `scripts/export_clean_repo.py`.

The clean export used for this pass was created as a disposable sibling directory. It is a verification
artifact, not a deployment source or a second canonical checkout.

## Remaining Risks And Promotion Gate

The breakup is not permission to cut over production without parity checks:

- `app.runtime` remains a compatibility composition root for existing workers and tests. HTTP adapters no
  longer depend on it, but a future pass can move worker construction into container factories and retire
  the global aliases.
- The router still exposes compatibility wrappers used by existing tests and coordinator ports. The size
  ratchet prevents regrowth; future changes should narrow those ports instead of adding router policy.
- Memory is the only asynchronous generic durable-write job today. Other acknowledged writes are committed
  synchronously before the response is returned; new asynchronous write paths must register a durable job
  handler and expose delivery state before they may return success.
- Local Windows verification is not production verification. Promotion must occur on the authoritative
  Ubuntu deployment checkout and every Compose command must include `--env-file .env`.

Before cutover on Ubuntu:

1. Back up the runtime database and protected configuration.
2. Promote the reviewed commit to the configured Ubuntu deployment checkout without copying Windows
   runtime state.
3. Run compile, fatal Ruff, architecture, and full test gates on Ubuntu.
4. Run the migration against a disposable database, then a backed-up copy of production state.
5. Start Compose with `docker compose --env-file .env -f deploy/docker/compose.yaml ...`.
6. Verify `/health`, one explicit Discord `!` Micro action, one unprefixed Main conversation, one Main action
   commitment, one clarification continuation, durable-write worker heartbeat, and ticket review heartbeat.
7. Observe Ubuntu before changing or removing any Windows rollback runtime.
