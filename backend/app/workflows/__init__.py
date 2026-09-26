"""The workflow engine. See engine.py."""

from app.workflows.dispatcher import JobDispatcher
from app.workflows.eligibility import check_contact_eligibility
from app.workflows.engine import CallAction, WorkflowEngine, evaluate_workflow, workflow_reference
from app.workflows.scheduler import SchedulerConfig, WorkflowScheduler
from app.workflows.results import (
    DispatchOutcome,
    DispatchReport,
    DispatchStatus,
    EligibilityResult,
    Evaluation,
    Reason,
    SchedulerReport,
    TriggerResult,
    WorkflowOutcome,
    WorkflowRunResult,
)
from app.workflows.triggers import evaluate_trigger

__all__ = [
    "CallAction",
    "DispatchOutcome",
    "DispatchReport",
    "DispatchStatus",
    "EligibilityResult",
    "Evaluation",
    "JobDispatcher",
    "Reason",
    "SchedulerConfig",
    "SchedulerReport",
    "TriggerResult",
    "WorkflowEngine",
    "WorkflowOutcome",
    "WorkflowRunResult",
    "WorkflowScheduler",
    "check_contact_eligibility",
    "evaluate_trigger",
    "evaluate_workflow",
    "workflow_reference",
]
