from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import Response

from app.api.routes.ask import router as ask_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.house import router as house_router
from app.api.routes.tickets import router as tickets_router
from app.api.routes.identities import router as identities_router
from app.api.routes.operator_session import router as operator_session_router
from app.api.operator_auth import validate_security_configuration
from app.api.security_headers import SECURITY_HEADERS
from app.container import ApplicationContainer
from app.services.clock_scheduler import BoundedClockScheduler, ClockJob
from app.services.discord.bot import DiscordJarvisBot


def _report_discord_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"Discord bot connection task exited with error: {exc}")


def _report_clock_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"Clock scheduler task exited with error: {exc}")


@asynccontextmanager
async def _lifespan(application: FastAPI):
    container: ApplicationContainer = application.state.container
    settings = container.settings
    validate_security_configuration()
    discord_bot: DiscordJarvisBot | None = None
    discord_task: asyncio.Task[None] | None = None
    clock_task: asyncio.Task[None] | None = None
    durable_write_task: asyncio.Task[None] | None = None
    clock_jobs: list[ClockJob] = []
    poll_intervals: list[float] = []
    await asyncio.to_thread(container.durable_write_service.recover_startup)
    durable_write_task = asyncio.create_task(
        container.durable_write_service.run_forever(),
        name="jarvis-durable-write-worker",
    )
    if container.calendar_inbox_service is not None:
        clock_jobs.append(
            ClockJob(
                name="calendar_inbox.reconcile",
                callback=container.calendar_inbox_service.run_due,
            )
        )
        poll_intervals.append(settings.calendar_inbox_poll_seconds)
    if container.email_agent_service is not None and settings.email_agent_sync_enabled:
        clock_jobs.append(
            ClockJob(
                name="email.sync",
                callback=container.email_agent_service.run_due,
            )
        )
        poll_intervals.append(settings.email_agent_scheduler_poll_seconds)
    if clock_jobs:
        clock_scheduler = BoundedClockScheduler(
            jobs=clock_jobs,
            poll_seconds=min(poll_intervals) if poll_intervals else 60.0,
        )
        clock_task = asyncio.create_task(
            clock_scheduler.run_forever(),
            name="jarvis-clock-scheduler",
        )
        clock_task.add_done_callback(_report_clock_task_result)
    if settings.discord_enabled:
        token = settings.discord_bot_token.strip()
        if not token:
            raise RuntimeError("DISCORD_ENABLED=true but DISCORD_BOT_TOKEN is empty.")
        discord_bot = DiscordJarvisBot(
            command_prefix=settings.discord_command_prefix,
            command_channel_id=settings.discord_command_channel_id,
            command_guild_id=settings.discord_command_guild_id,
            permissions_path=settings.discord_permissions_path,
            private_notes_service=container.private_notes_service,
            turn_service=container.turn_service,
        )
        await discord_bot.login(token)
        discord_task = asyncio.create_task(discord_bot.connect(reconnect=True))
        discord_task.add_done_callback(_report_discord_task_result)
    try:
        yield
    except asyncio.CancelledError:
        # Avoid noisy shutdown traces when Ctrl+C cancels pending tasks.
        return
    finally:
        if discord_bot is not None:
            with suppress(Exception):
                await discord_bot.close()
        if discord_task is not None:
            discord_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await discord_task
        if clock_task is not None:
            clock_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await clock_task
        if durable_write_task is not None:
            durable_write_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await durable_write_task


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    application = FastAPI(title="Jarvis v2 POC", version="0.1.0", lifespan=_lifespan)
    application.state.container = container or ApplicationContainer.from_default_runtime()

    @application.middleware("http")
    async def _apply_security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @application.middleware("http")
    async def _handle_request_cancellation(request: Request, call_next):
        try:
            return await call_next(request)
        except asyncio.CancelledError:
            # Treat request cancellation during shutdown/client disconnect as expected.
            return Response(status_code=499)

    application.include_router(health_router)
    application.include_router(ask_router)
    application.include_router(house_router)
    application.include_router(tickets_router)
    application.include_router(identities_router)
    application.include_router(operator_session_router)
    application.include_router(dashboard_router)
    return application


app = create_app()
