"""Step 6: the scheduler drives the workflow engine and then the dispatcher, on an interval.

Same rig as the dispatcher tests: a real migrated file database (foreign keys enforced, real threads),
a fake Twilio, and a pinned clock. Ticks are called directly with run_once(now); the background loop is
stepped with a gate in place of sleeping, so nothing here waits for a real interval."""

import ast
import asyncio
import json
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Contact, Workflow
from app.outbound import Callee, CallJobRequest
from app.workflows import (
    DispatchStatus,
    JobDispatcher,
    Reason,
    SchedulerConfig,
    WorkflowEngine,
    WorkflowOutcome,
    WorkflowScheduler,
)
from tests.test_dispatcher import NOW, PHONE, World, clock, world  # noqa: F401  (clock and world are fixtures)
from tests.test_telephony_routes import TOKEN

D = DispatchStatus
WINDOW_CLOSED = {"start": "14:00", "end": "18:00", "timezone": "Asia/Kolkata"}


def scheduler_for(w: World, sleep=None, **config) -> WorkflowScheduler:
    """A scheduler over the world's service, with its own engine and dispatcher, as another process would have."""
    kwargs = {"sleep": sleep} if sleep else {}
    return WorkflowScheduler(
        WorkflowEngine(w.service), JobDispatcher(w.service), SchedulerConfig(**config), clock=lambda: w.clock.now, **kwargs
    )


async def tick(scheduler: WorkflowScheduler, w: World, when=NOW):
    w.clock.to(when)
    return await scheduler.run_once(when)


async def until(predicate, timeout: float = 5.0):
    """Wait, in tiny real steps, for something a background task should do."""
    deadline = time.monotonic() + timeout

    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


class Gate:
    """Stands in for sleeping between ticks: each wait blocks until the test releases it."""

    def __init__(self):
        self.waiting = asyncio.Queue()
        self.sleeps = 0

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        await self.waiting.get()

    def release(self) -> None:
        self.waiting.put_nowait(None)


def scheduler_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "workflow-scheduler" and not t.done()]


# === one tick ========================================================================================


async def test_a_tick_with_nothing_to_do_reports_so(world):
    scheduler = scheduler_for(world)
    report = await tick(scheduler, world)

    assert (report.workflows_seen, report.workflows_processed, report.workflows_failed, report.jobs_created) == (0, 0, 0, 0)
    assert report.workflows_truncated is False and report.workflow_results == ()
    assert report.dispatch_report.selected == 0 and report.ok and report.duration_seconds >= 0
    assert report.tick_at == NOW and scheduler.last_report is report


async def test_one_tick_creates_a_workflows_job_and_places_it_in_the_same_tick(world):
    world.seed()
    report = await tick(scheduler_for(world), world)

    (result,) = report.workflow_results
    assert result.ok and [o.reason for o in result.outcomes] == [Reason.CALL_JOB_CREATED]
    assert report.jobs_created == 1 and report.workflows_processed == 1
    assert (report.dispatch_report.selected, report.dispatch_report.dispatched) == (1, 1), "dispatched after being scheduled, not a tick later"

    (job,) = world.repo.list_jobs(10)
    assert (job["status"], job["twilio_call_sid"]) == ("ringing", "CA_fake_1") and len(world.twilio.calls) == 1


async def test_every_active_workflow_is_run(world):
    for _ in range(3):
        world.seed()
    report = await tick(scheduler_for(world), world)

    assert (report.workflows_seen, report.workflows_processed, report.jobs_created) == (3, 3, 3)
    assert report.dispatch_report.dispatched == 3 and len(world.twilio.calls) == 3


@pytest.mark.parametrize("status", ["draft", "paused", "archived"])
async def test_workflows_that_are_not_active_are_not_run(world, status):
    active, idle = world.seed(), world.seed()
    world.change(Workflow, idle.workflow, status=status)
    scheduler, ran = scheduler_for(world), []
    original = scheduler.engine.run

    async def spy(workflow_id, now):
        ran.append(workflow_id)
        return await original(workflow_id, now)

    scheduler.engine.run = spy
    report = await tick(scheduler, world)

    assert ran == [active.workflow] and report.workflows_seen == 1
    assert [j["workflow_id"] for j in world.repo.list_jobs(10)] == [active.workflow], "the idle workflow's due contact got no job"


def test_active_workflows_are_listed_by_id_and_bounded(tmp_path, clock):
    w = World(tmp_path, clock)
    ids = [w.seed().workflow for _ in range(5)]
    w.change(Workflow, ids[1], status="paused")
    w.change(Workflow, ids[3], status="archived")

    assert w.repo.list_active_workflow_ids(100) == [ids[0], ids[2], ids[4]]
    assert w.repo.list_active_workflow_ids(2) == [ids[0], ids[2]]
    w.repo.engine.dispose()


async def test_both_phases_get_the_same_now_and_the_workflows_run_before_the_dispatch(world):
    world.seed(), world.seed()
    scheduler, calls = scheduler_for(world, dispatch_limit=7), []
    run, dispatch = scheduler.engine.run, scheduler.dispatcher.dispatch_scheduled_jobs

    async def run_spy(workflow_id, now):
        calls.append(("run", workflow_id, now))
        return await run(workflow_id, now)

    async def dispatch_spy(now, *, limit):
        calls.append(("dispatch", limit, now))
        return await dispatch(now, limit=limit)

    scheduler.engine.run, scheduler.dispatcher.dispatch_scheduled_jobs = run_spy, dispatch_spy
    await tick(scheduler, world)

    assert [c[0] for c in calls] == ["run", "run", "dispatch"], "workflows first, then the dispatch"
    assert all(c[2] is NOW for c in calls), "one and the same instant for every call"
    assert calls[-1][1] == 7, "and the configured dispatch limit"


async def test_a_naive_now_is_refused_before_anything_happens(world):
    world.seed()

    with pytest.raises(ValueError, match="timezone-aware"):
        await scheduler_for(world).run_once(NOW.replace(tzinfo=None))

    assert world.repo.list_jobs(10) == []


async def test_more_active_workflows_than_the_limit_is_reported_not_hidden(world, caplog):
    caplog.set_level(logging.WARNING, logger="voice_agent")
    for _ in range(3):
        world.seed()

    cut = await tick(scheduler_for(world, workflow_limit=2), world)
    fits = await tick(scheduler_for(world, workflow_limit=3), world)

    assert (cut.workflows_seen, cut.workflows_truncated) == (2, True)
    assert (fits.workflows_seen, fits.workflows_truncated) == (3, False), "a list that exactly fits is not truncated"
    assert "workflow_limit=2" in caplog.text


async def test_the_scheduler_leaves_manual_phone_calls_alone(world):
    manual, _ = await world.service.create_job(CallJobRequest(
        reference="manual-1", profile_id="bank", channel="phone", callee=Callee(name="P", phone=PHONE), reason="x",
    ))
    before = world.repo.get_job(manual["job_id"])
    world.seed()
    await tick(scheduler_for(world), world)

    assert world.repo.get_job(manual["job_id"]) == before and len(world.twilio.calls) == 2


# === failures stay in their place =========================================================================


async def test_a_workflow_that_raises_does_not_stop_the_others_or_the_dispatch(world, caplog):
    caplog.set_level(logging.INFO, logger="voice_agent")
    broken, fine, also = world.seed(), world.seed(), world.seed()
    waiting = await world.scheduled(broken)  # a job already scheduled for the workflow that is about to fail
    scheduler = scheduler_for(world)
    original = scheduler.engine.run

    async def run(workflow_id, now):
        if workflow_id == broken.workflow:
            raise RuntimeError("something nobody planned for")

        return await original(workflow_id, now)

    scheduler.engine.run = run
    report = await tick(scheduler, world)

    results = {r.workflow_id: r for r in report.workflow_results}
    assert (results[broken.workflow].ok, results[broken.workflow].error) == (False, "RuntimeError")
    assert results[fine.workflow].ok and results[also.workflow].ok, "the other workflows were still run"
    assert (report.workflows_seen, report.workflows_processed, report.workflows_failed, report.jobs_created) == (3, 2, 1, 2)
    assert report.dispatch_report.dispatched == 3, "and the dispatcher still ran, placing even the failed workflow's waiting job"
    assert world.repo.get_job(waiting["id"])["status"] == "ringing"

    assert report.ok is False
    failure = [r for r in caplog.records if f"workflow {broken.workflow} failed" in r.getMessage()]
    assert failure and failure[0].levelno == logging.ERROR and failure[0].exc_info, "logged with its traceback"
    assert "something nobody planned for" not in json.dumps(report.as_dict()), "the report has the class name, not the message"
    finished = [r for r in caplog.records if "scheduler tick finished" in r.getMessage()]
    assert finished[-1].levelno == logging.ERROR and "ok=False" in finished[-1].getMessage()


async def test_a_failure_to_list_the_workflows_is_visible_and_the_dispatch_still_runs(world, caplog):
    job = await world.scheduled()
    scheduler = scheduler_for(world)

    def down(limit):
        raise OperationalError("SELECT ...", {}, Exception("database is down"))

    scheduler.engine.repo.list_active_workflow_ids = down
    report = await tick(scheduler, world)

    assert report.ok is False and report.list_error == "OperationalError" and report.workflows_seen == 0
    assert report.dispatch_report.dispatched == 1 and world.repo.get_job(job["id"])["status"] == "ringing"
    assert any(r.levelno == logging.ERROR and "list the active workflows" in r.getMessage() for r in caplog.records)
    assert "database is down" not in json.dumps(report.as_dict())


async def test_a_failing_dispatch_is_visible_and_does_not_undo_the_workflow_phase(world, caplog):
    world.seed()
    scheduler = scheduler_for(world)

    async def down(now, *, limit):
        raise OperationalError("UPDATE ...", {}, Exception("database is down"))

    scheduler.dispatcher.dispatch_scheduled_jobs = down
    report = await tick(scheduler, world)

    assert report.ok is False and report.dispatch_error == "OperationalError" and report.dispatch_report is None
    assert (report.workflows_processed, report.jobs_created) == (1, 1), "the job was still scheduled"
    assert world.repo.list_jobs(10)[0]["status"] == "scheduled" and world.twilio.calls == []
    assert any(r.levelno == logging.ERROR and "dispatch run failed" in r.getMessage() for r in caplog.records)


async def test_when_the_database_fails_everywhere_nothing_reports_success(world, caplog):
    for _ in range(2):
        world.seed()
    scheduler = scheduler_for(world)

    async def down(*args, **kwargs):
        raise OperationalError("SELECT ...", {}, Exception("database is down"))

    scheduler.engine.run, scheduler.dispatcher.dispatch_scheduled_jobs = down, down
    report = await tick(scheduler, world)

    assert report.ok is False and (report.workflows_failed, report.workflows_processed) == (2, 0)
    assert report.dispatch_error == "OperationalError"
    assert all(r.error == "OperationalError" for r in report.workflow_results)
    assert [r.levelno for r in caplog.records if "scheduler tick finished" in r.getMessage()] == [logging.ERROR]


async def test_the_loop_survives_a_tick_that_crashes_and_says_so(world, caplog):
    gate = Gate()
    scheduler = scheduler_for(world, sleep=gate.sleep)
    real, seen = scheduler.run_once, []

    async def flaky(now):
        seen.append(now)

        if len(seen) == 1:
            raise RuntimeError("crash")

        return await real(now)

    scheduler.run_once = flaky
    scheduler.start()
    await until(lambda: gate.sleeps == 1)
    gate.release()
    await until(lambda: len(seen) == 2 and gate.sleeps == 2)
    await scheduler.stop()

    assert any("scheduler tick crashed" in r.getMessage() and r.exc_info for r in caplog.records)
    assert scheduler.last_report is not None, "and the next tick ran normally"


# === running it again and at once creates nothing twice ===================================================


async def test_ticking_again_creates_no_second_job_and_places_no_second_call(world):
    world.seed()
    scheduler = scheduler_for(world)
    first = await tick(scheduler, world, NOW)
    second = await tick(scheduler, world, NOW)
    third = await tick(scheduler, world, NOW + timedelta(minutes=1))

    assert first.jobs_created == 1 and first.dispatch_report.dispatched == 1
    for again in (second, third):
        assert again.jobs_created == 0 and again.dispatch_report.selected == 0
        assert [o.reason for o in again.workflow_results[0].outcomes] == [Reason.ALREADY_SCHEDULED]
    assert len(world.repo.list_jobs(10)) == 1 and len(world.twilio.calls) == 1


async def test_a_job_waiting_for_a_contacts_window_is_not_duplicated_and_is_placed_once_when_it_opens(world):
    job = await world.scheduled()
    world.change(Contact, world.ids(job).contact, preferred_contact_time=WINDOW_CLOSED)
    scheduler = scheduler_for(world)

    for minute in (5, 6, 7):
        report = await tick(scheduler, world, NOW + timedelta(minutes=minute))
        assert (report.dispatch_report.selected, report.dispatch_report.skipped, report.dispatch_report.dispatched) == (1, 1, 0)

    assert len(world.repo.list_jobs(10)) == 1 and world.twilio.calls == [] and world.repo.get_job(job["id"]) == job

    opened = await tick(scheduler, world, NOW.replace(hour=14))
    assert opened.dispatch_report.dispatched == 1
    later = await tick(scheduler, world, NOW.replace(hour=14, minute=1))
    assert later.dispatch_report.selected == 0 and len(world.twilio.calls) == 1 and len(world.repo.list_jobs(10)) == 1


async def test_two_schedulers_ticking_at_once_make_one_job_and_place_one_call(world):
    """Both evaluate the same workflow at the same moment (a barrier holds them just before they would
    create the job). The unique reference gives the second one the first one's job."""
    world.seed()
    first, second = scheduler_for(world), scheduler_for(world)
    barrier, arrivals, lock, real = threading.Barrier(2, timeout=15), [], threading.Lock(), world.repo.get_job_by_reference

    def held(reference):
        found = real(reference)

        with lock:
            arrivals.append(reference)
            hold = len(arrivals) <= 2  # only each scheduler's first look; later lookups pass straight through

        if hold:
            barrier.wait()

        return found

    world.repo.get_job_by_reference = held
    reports = await asyncio.gather(tick(first, world), tick(second, world))

    outcomes = [o.reason for r in reports for res in r.workflow_results for o in res.outcomes]
    assert sorted(outcomes) == sorted([Reason.CALL_JOB_CREATED, Reason.ALREADY_SCHEDULED])
    assert len(world.repo.list_jobs(10)) == 1
    assert sum(r.dispatch_report.dispatched for r in reports) == 1 and len(world.twilio.calls) == 1


async def test_two_schedulers_dispatching_the_same_job_place_one_call(world):
    """The job already exists, so both schedulers get to the dispatch phase and both select it. A barrier
    holds them after they have loaded it and before either can claim it."""
    job = await world.scheduled()
    first, second = scheduler_for(world), scheduler_for(world)
    barrier, arrivals, lock, real = threading.Barrier(2, timeout=15), [], threading.Lock(), world.repo.get_contact

    def held(contact_id):
        contact = real(contact_id)

        with lock:
            arrivals.append(contact_id)
            hold = len(arrivals) <= 2

        if hold:
            barrier.wait()

        return contact

    world.repo.get_contact = held
    reports = await asyncio.gather(tick(first, world), tick(second, world))

    dispatch = [o for r in reports for o in r.dispatch_report.outcomes]
    assert sorted(o.status for o in dispatch) == sorted([D.DISPATCHED, D.SKIPPED])
    assert {o.reason for o in dispatch if o.status == D.SKIPPED} == {Reason.ALREADY_CLAIMED}
    assert len(world.twilio.calls) == 1 and world.repo.get_job(job["id"])["status"] == "ringing"
    assert len(world.repo.list_jobs(10)) == 1


# === head-of-line blocking (known, documented, not redesigned) ===========================================


async def test_a_dispatch_limit_below_the_number_of_waiting_jobs_can_starve_a_ready_job(world):
    """Selection is oldest-first. Three older jobs are waiting for their contacts' windows; a ready job
    is behind them. With a limit of 3 the same three are picked, skipped and picked again, forever. A
    larger limit reaches it. This documents the limit; a cursor is future work."""
    ids = world.seed()
    contacts = [ids.contact] + [world.add_contact(ids, name=f"Person {i}") for i in range(1, 4)]
    jobs = [await world.scheduled(ids, c, when=NOW + timedelta(seconds=i)) for i, c in enumerate(contacts)]

    for blocked in contacts[:3]:
        world.change(Contact, blocked, preferred_contact_time=WINDOW_CLOSED)

    later = NOW + timedelta(minutes=5)

    for _ in range(3):  # tick after tick, nothing changes
        report = await tick(scheduler_for(world, dispatch_limit=3), world, later)
        assert (report.dispatch_report.selected, report.dispatch_report.skipped, report.dispatch_report.dispatched) == (3, 3, 0)

    assert world.repo.get_job(jobs[3]["id"])["status"] == "scheduled" and world.twilio.calls == [], "the ready job is starved"

    report = await tick(scheduler_for(world, dispatch_limit=4), world, later)
    assert (report.dispatch_report.selected, report.dispatch_report.dispatched) == (4, 1)
    assert world.repo.get_job(jobs[3]["id"])["status"] == "ringing"


# === the background loop ===================================================================================


async def test_start_ticks_at_once_then_after_each_wait_and_stop_ends_the_task(world):
    gate, reports = Gate(), []
    scheduler = scheduler_for(world, sleep=gate.sleep)
    real = scheduler.run_once

    async def recording(now):
        reports.append(await real(now))
        return reports[-1]

    scheduler.run_once = recording
    scheduler.start()

    assert scheduler.is_running and len(scheduler_tasks()) == 1
    await until(lambda: len(reports) == 1 and gate.sleeps == 1)  # the first tick does not wait for an interval

    world.clock.to(NOW + timedelta(minutes=1))
    gate.release()
    await until(lambda: len(reports) == 2 and gate.sleeps == 2)
    assert [r.tick_at for r in reports] == [NOW, NOW + timedelta(minutes=1)], "each tick reads the clock afresh"

    await scheduler.stop()
    assert not scheduler.is_running and scheduler_tasks() == [], "no task left behind"


async def test_stop_is_safe_to_repeat_to_call_early_and_start_is_safe_to_repeat(world):
    scheduler = scheduler_for(world, sleep=Gate().sleep)

    await scheduler.stop()  # never started
    scheduler.start()
    scheduler.start()  # already running: no second task
    assert len(scheduler_tasks()) == 1
    await scheduler.stop()
    await scheduler.stop()
    assert scheduler_tasks() == []

    scheduler.last_report = None
    scheduler.start()  # and it can be started again
    await until(lambda: scheduler.last_report is not None)
    await scheduler.stop()
    assert scheduler_tasks() == []


async def test_stop_does_not_wait_out_the_interval(world):
    scheduler = scheduler_for(world)  # the real asyncio.sleep, and a 60-second interval
    scheduler.start()
    await until(lambda: scheduler.last_report is not None)

    began = time.monotonic()
    await scheduler.stop()

    assert time.monotonic() - began < 2 and scheduler_tasks() == []


async def test_stop_lets_a_tick_that_is_running_finish_and_then_ticks_no_more(world):
    gate = Gate()
    world.seed()
    scheduler = scheduler_for(world, sleep=gate.sleep)
    dispatch, inside, hold = scheduler.dispatcher.dispatch_scheduled_jobs, asyncio.Event(), asyncio.Event()

    async def slow(now, *, limit):
        inside.set()
        await hold.wait()
        return await dispatch(now, limit=limit)

    scheduler.dispatcher.dispatch_scheduled_jobs = slow
    scheduler.start()
    await inside.wait()

    stopping = asyncio.create_task(scheduler.stop())
    await asyncio.sleep(0.1)
    assert not stopping.done(), "stop waits for the tick in flight instead of killing it"

    hold.set()
    await stopping

    assert scheduler.last_report.dispatch_report.dispatched == 1, "the tick completed, job and call included"
    assert gate.sleeps == 0 and scheduler_tasks() == [], "and it did not go round again"


async def test_stop_cancels_a_tick_that_will_not_finish_after_the_grace_period(world, caplog):
    scheduler = scheduler_for(world, stop_timeout_seconds=0.2)
    inside = asyncio.Event()

    async def never(now, *, limit):
        inside.set()
        await asyncio.Event().wait()

    scheduler.dispatcher.dispatch_scheduled_jobs = never
    scheduler.start()
    await inside.wait()

    began = time.monotonic()
    await scheduler.stop()

    assert 0.15 < time.monotonic() - began < 3 and not scheduler.is_running and scheduler_tasks() == []
    assert "did not finish within" in caplog.text


async def test_the_loop_logs_its_lifecycle_and_each_tick(world, caplog):
    caplog.set_level(logging.INFO, logger="voice_agent")
    world.seed()
    scheduler = scheduler_for(world, sleep=Gate().sleep)
    scheduler.start()
    await until(lambda: scheduler.last_report is not None)
    await scheduler.stop()

    text = caplog.text
    for expected in ("workflow scheduler started", "interval_s=60", "scheduler tick started", "scheduler tick finished",
                     "duration_s=", "jobs_created=1", "dispatched=1", "workflow scheduler stopped"):
        assert expected in text, expected


# === logs carry no secrets ==================================================================================


async def test_the_scheduler_never_logs_phone_numbers_tokens_keys_or_free_text_details(world, caplog):
    caplog.set_level(logging.DEBUG, logger="voice_agent")
    caplog.set_level(logging.DEBUG, logger="app")
    world.seed(callback=True)
    scheduler = scheduler_for(world)
    real = scheduler.engine.run

    async def run(workflow_id, now):
        outcomes = await real(workflow_id, now)
        # An engine outcome whose free-text detail happens to contain personal data: it must not reach the log.
        return [*outcomes, WorkflowOutcome(workflow_id, 0, True, True, False, Reason.CALL_JOB_REJECTED,
                                           detail=f"input_value='{PHONE}' answer_token=leaky")]

    scheduler.engine.run = run
    report = await tick(scheduler, world)
    await world.service.drain()

    job = world.repo.get_job(report.workflow_results[0].outcomes[0].job_id)
    assert job["status"] == "ringing"
    secrets_ = [PHONE, PHONE.lstrip("+"), "9876543210", job["answer_token"], "test-key", TOKEN, "X-Signature", "leaky"]
    leaked = [s for s in secrets_ if s in caplog.text]
    assert leaked == [], leaked
    assert "scheduler tick finished" in caplog.text


# === the application and its configuration ===================================================================


def test_the_scheduler_is_off_unless_switched_on_and_the_test_suite_pins_it_off(monkeypatch):
    import app.main

    assert Settings.model_fields["workflow_scheduler_enabled"].default is False
    assert app.main.app.state.scheduler is None, "the app built at import time has no scheduler"
    assert Settings(database_url="sqlite://").workflow_scheduler_enabled is False, "conftest pins it off, whatever .env says"

    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED")
    assert Settings(database_url="sqlite://", _env_file=None).workflow_scheduler_enabled is False


def test_the_documented_environment_variables_configure_it(monkeypatch):
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_SCHEDULER_INTERVAL_SECONDS", "120")
    monkeypatch.setenv("WORKFLOW_SCHEDULER_WORKFLOW_LIMIT", "7")
    monkeypatch.setenv("WORKFLOW_SCHEDULER_DISPATCH_LIMIT", "9")
    settings = Settings(database_url="sqlite://", _env_file=None)

    assert settings.workflow_scheduler_enabled is True
    assert SchedulerConfig.from_settings(settings) == SchedulerConfig(interval_seconds=120, workflow_limit=7, dispatch_limit=9)


def test_the_scheduler_can_never_run_faster_than_once_a_minute():
    from app.workflows.scheduler import MIN_INTERVAL_SECONDS

    assert MIN_INTERVAL_SECONDS == 60 == Settings.model_fields["workflow_scheduler_interval_seconds"].default

    with pytest.raises(ValueError, match="at least 60"):
        SchedulerConfig(interval_seconds=59.9)

    with pytest.raises(ValueError):
        Settings(database_url="sqlite://", workflow_scheduler_interval_seconds=30)

    for bad in ({"workflow_limit": 0}, {"dispatch_limit": 0}, {"stop_timeout_seconds": 0}):
        with pytest.raises(ValueError):
            SchedulerConfig(**bad)


def test_an_app_with_the_scheduler_off_starts_no_scheduler():
    app = create_app(None, Settings(_env_file=None, database_url="sqlite://", workflow_scheduler_enabled=False))

    with TestClient(app):
        assert app.state.scheduler is None


def test_an_app_with_the_scheduler_on_starts_it_with_the_app_and_stops_it_with_the_app():
    app = create_app(None, Settings(_env_file=None, database_url="sqlite://", workflow_scheduler_enabled=True))

    with TestClient(app):
        scheduler = app.state.scheduler
        assert isinstance(scheduler, WorkflowScheduler) and scheduler.is_running
        deadline = time.monotonic() + 5

        while scheduler.last_report is None:  # the first tick runs at once, on an empty database
            assert time.monotonic() < deadline
            time.sleep(0.01)

        assert scheduler.last_report.ok

    assert not scheduler.is_running and scheduler._task is None, "stopped with the app: no task is left behind"


def test_repeatedly_building_apps_with_the_scheduler_leaks_no_task():
    for _ in range(3):
        app = create_app(None, Settings(_env_file=None, database_url="sqlite://", workflow_scheduler_enabled=True))

        with TestClient(app):
            assert app.state.scheduler.is_running

        assert not app.state.scheduler.is_running


# === what the scheduler is not ================================================================================


def test_the_scheduler_only_orchestrates_the_engine_and_the_dispatcher():
    tree = ast.parse(Path(__file__).parents[1].joinpath("app/workflows/scheduler.py").read_text())
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)

    forbidden_modules = ("telephony", "twilio", "deepgram", "gemini", "brains", "livekit", "pipecat", "httpx", "fastapi",
                         "apscheduler", "celery", "redis", "rq", "croniter", "sqlalchemy", "app.service", "eligibility", "triggers")
    assert not [m for m in imported if any(word in m for word in forbidden_modules)], imported

    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    business = {"_dial", "_finish_without_call", "create_job", "transition", "update_job", "evaluate_workflow",
                "check_contact_eligibility", "evaluate_trigger", "workflow_reference", "answer_token", "consent_status", "date_offset"}
    assert not used & business, used & business
