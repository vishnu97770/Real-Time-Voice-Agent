"""The scheduler: on an interval, run the active workflows and then place the calls that are due.

    every interval:  active workflows -> WorkflowEngine.run(id, now)   creates scheduled jobs
                     then             -> JobDispatcher.dispatch_scheduled_jobs(now)   places them

It is orchestration and nothing else. It does not judge eligibility, read a contact, compute a date,
claim a job, create a job or touch telephony: the engine and the dispatcher do all of that, and are
the only source of truth for it. What it adds is the order, the one shared `now`, error isolation,
a bounded amount of work per tick, and a lifecycle.

Several schedulers (several processes, or one that overlaps another) need no lock. If two evaluate the
same workflow at once, `call_jobs.reference` is unique, so the engine still makes one job; if two try to
place the same job at once, the dispatcher's conditional UPDATE lets one claim it. That is deliberate:
there is no other coordination to get wrong.

Known limits, on purpose left as they are (see the README):
  * WorkflowEngine.run walks every contact of a workflow's organization in one go (no paging).
  * Selection is oldest-first and bounded, so a dispatch limit smaller than the number of jobs that are
    waiting for a contact's window can keep re-selecting the same jobs (head-of-line blocking).
  * If more workflows are active than the workflow limit, the highest ids are not run; the report says so.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.workflows.clock import require_aware
from app.workflows.dispatcher import JobDispatcher
from app.workflows.engine import WorkflowEngine
from app.workflows.results import DispatchReport, SchedulerReport, WorkflowRunResult

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger("voice_agent.scheduler")

# A polling loop, not a trigger system: never faster than once a minute.
MIN_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class SchedulerConfig:
    interval_seconds: float = 60.0  # the pause after a tick finishes (ticks never overlap)
    workflow_limit: int = 100  # active workflows run per tick
    dispatch_limit: int = 100  # scheduled jobs looked at per tick
    # On stop, how long a tick that is running may take to finish before it is cancelled.
    stop_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.interval_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval_seconds must be at least {MIN_INTERVAL_SECONDS:g}")
        if self.workflow_limit < 1 or self.dispatch_limit < 1:
            raise ValueError("workflow_limit and dispatch_limit must be at least 1")
        if self.stop_timeout_seconds <= 0:
            raise ValueError("stop_timeout_seconds must be positive")

    @classmethod
    def from_settings(cls, settings: "Settings") -> "SchedulerConfig":
        return cls(
            interval_seconds=settings.workflow_scheduler_interval_seconds,
            workflow_limit=settings.workflow_scheduler_workflow_limit,
            dispatch_limit=settings.workflow_scheduler_dispatch_limit,
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowScheduler:
    def __init__(
        self,
        engine: WorkflowEngine,
        dispatcher: JobDispatcher,
        config: SchedulerConfig | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`clock` says what time it is (timezone-aware) and `sleep` waits between ticks; both are
        injectable so tests need neither the real time nor real waiting."""
        self.engine = engine
        self.dispatcher = dispatcher
        self.config = config or SchedulerConfig()
        self.clock = clock
        self._sleep = sleep
        self._stopping = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.last_report: SchedulerReport | None = None

    # --- one tick ----------------------------------------------------------------------------------------

    async def run_once(self, now: datetime) -> SchedulerReport:
        """Exactly one orchestration tick, at `now` (timezone-aware). Both phases use this same `now`.

        A workflow that raises is recorded and the tick carries on with the next one; the dispatch run
        happens whatever the workflows did. Nothing is hidden: any failure is logged with its traceback
        and appears in the report, whose `ok` is then False."""
        require_aware(now)
        began = time.monotonic()
        log.info("scheduler tick started tick_at=%s", now.isoformat())

        # 1. Evaluate the active workflows, which creates whatever jobs are due.
        limit = self.config.workflow_limit
        list_error, ids = None, []

        try:
            # One more than the limit, so a truncated list can be told from one that just fits.
            ids = await asyncio.to_thread(self.engine.repo.list_active_workflow_ids, limit + 1)
        except Exception as error:
            log.exception("scheduler could not list the active workflows")
            list_error = type(error).__name__

        truncated = len(ids) > limit
        ids = ids[:limit]

        if truncated:
            log.warning("more active workflows than workflow_limit=%d: the rest were not run this tick", limit)

        results: list[WorkflowRunResult] = []

        for workflow_id in ids:
            try:
                results.append(WorkflowRunResult(workflow_id, tuple(await self.engine.run(workflow_id, now))))
            except Exception as error:  # this workflow only: the others and the dispatch run still happen
                log.exception("workflow %s failed during the scheduler tick", workflow_id)
                results.append(WorkflowRunResult(workflow_id, error=type(error).__name__))

        # 2. Then place what is due, including what step 1 has just scheduled.
        dispatch_report: DispatchReport | None = None
        dispatch_error = None

        try:
            dispatch_report = await self.dispatcher.dispatch_scheduled_jobs(now, limit=self.config.dispatch_limit)
        except Exception as error:
            log.exception("the scheduler's dispatch run failed")
            dispatch_error = type(error).__name__

        report = SchedulerReport(
            tick_at=now,
            workflows_seen=len(ids),
            workflows_processed=sum(1 for r in results if r.ok),
            workflows_failed=sum(1 for r in results if not r.ok),
            workflows_truncated=truncated,
            workflow_results=tuple(results),
            dispatch_report=dispatch_report,
            list_error=list_error,
            dispatch_error=dispatch_error,
            duration_seconds=round(time.monotonic() - began, 3),
        )
        self.last_report = report
        self._log_finished(report)
        return report

    @staticmethod
    def _log_finished(report: SchedulerReport) -> None:
        """Counts and ids only: never a phone number, token, key, signature or free-text detail."""
        dispatch = report.dispatch_report
        line = (
            "scheduler tick finished tick_at=%s ok=%s duration_s=%s workflows_seen=%d workflows_processed=%d "
            "workflows_failed=%d jobs_created=%d dispatch_selected=%s dispatched=%s skipped=%s cancelled=%s failed=%s"
        )
        args = (
            report.tick_at.isoformat(), report.ok, report.duration_seconds, report.workflows_seen,
            report.workflows_processed, report.workflows_failed, report.jobs_created,
            *(
                (dispatch.selected, dispatch.dispatched, dispatch.skipped, dispatch.cancelled, dispatch.failed)
                if dispatch else ("n/a",) * 5
            ),
        )
        (log.info if report.ok else log.error)(line, *args)

    # --- the background loop -------------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start ticking in the background. Call it from inside the running event loop. Starting a
        scheduler that is already running does nothing."""
        if self.is_running:
            return

        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="workflow-scheduler")

    async def stop(self) -> None:
        """Stop ticking and wait until the loop has ended. A tick that is running is allowed to finish
        (up to `stop_timeout_seconds`); only then is it cancelled. Safe to call twice, or without start()."""
        task = self._task

        if task is None:
            return

        self._stopping.set()

        try:
            await asyncio.wait_for(asyncio.shield(task), self.config.stop_timeout_seconds)
        except asyncio.TimeoutError:
            # Cancelling stops the coroutine at its next await. A database call already handed to a worker
            # thread still runs to its commit or rollback. What could be left half done is a job claimed
            # but not yet dialed, which the ring-timeout sweeper ends as no_answer (a missed call, never two).
            log.warning("a scheduler tick did not finish within %ss; cancelling it", self.config.stop_timeout_seconds)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:  # whoever is stopping us is being cancelled too: do not leave the task behind
            task.cancel()
            raise

        if self._task is task:
            self._task = None

    async def _run(self) -> None:
        log.info(
            "workflow scheduler started interval_s=%s workflow_limit=%d dispatch_limit=%d",
            self.config.interval_seconds, self.config.workflow_limit, self.config.dispatch_limit,
        )

        try:
            while True:
                try:
                    await self.run_once(self.clock())
                except Exception:  # run_once records what it can; this is only so the loop outlives anything
                    log.exception("the scheduler tick crashed")

                if self._stopping.is_set():
                    break

                await self._pause()

                if self._stopping.is_set():
                    break
        finally:
            log.info("workflow scheduler stopped")

    async def _pause(self) -> None:
        """Wait one interval, or less if stop() is called."""
        nap = asyncio.ensure_future(self._sleep(self.config.interval_seconds))
        stop = asyncio.ensure_future(self._stopping.wait())

        try:
            await asyncio.wait({nap, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in (nap, stop):
                waiter.cancel()

            await asyncio.gather(nap, stop, return_exceptions=True)
