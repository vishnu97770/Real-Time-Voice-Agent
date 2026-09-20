import re

from app.profiles.base import (
    ActionResult,
    ActionSpec,
    OutboundProfile,
    Profile,
    ToolSpec,
    list_sentence,
    make_greeting,
    object_schema,
    rupees,
)

_APPLICATION_ID = {
    "application_id": {
        "type": "string",
        "description": "Application number such as APP-1024. Omit for the application currently open.",
    }
}


def _application_id(data, args) -> str:
    raw = str(args.get("application_id") or data["current_id"])
    match = re.search(r"(\d{2,4})", raw)

    return f"APP-{match.group(1)}" if match else raw.upper()


def _lookup(data, args):
    application_id = _application_id(data, args)

    return application_id, data["applications"].get(application_id)


def _not_found(application_id: str) -> dict:
    return {"found": False, "application_id": application_id}


def _list_pending(data, args):
    return [
        {"id": key, "applicant": app["applicant"], "status": app["status"]}
        for key, app in data["applications"].items()
        if app["pending"]
    ]


def _get_application(data, args):
    application_id, app = _lookup(data, args)

    if not app:
        return _not_found(application_id)

    return {
        "found": True,
        "id": application_id,
        "applicant": app["applicant"],
        "product": app["product"],
        "amount": rupees(app["amount"]),
        "status": app["status"],
        "risk_score": app["risk_score"],
        "risk_band": app["risk_band"],
        "missing_documents": app["missing_docs"],
    }


def _get_risk(data, args):
    application_id, app = _lookup(data, args)

    if not app:
        return _not_found(application_id)

    return {
        "found": True,
        "id": application_id,
        "risk_score": app["risk_score"],
        "band": app["risk_band"],
        "factors": app["risk_factors"],
    }


def _get_missing_documents(data, args):
    application_id, app = _lookup(data, args)

    if not app:
        return _not_found(application_id)

    return {"found": True, "id": application_id, "missing": app["missing_docs"]}


def _ref_application(data, args):
    application_id, app = _lookup(data, args)

    return f"Application: {application_id}" if app else None


def _validate_followup(data, args):
    application_id, app = _lookup(data, args)

    return None if app else f"There is no application {application_id}."


def _describe_followup(args, data):
    application_id, app = _lookup(data, args)

    return (
        f"create an outbound follow-up call job to {app['applicant']} "
        f"about {application_id}"
    )


def _execute_followup(args, data):
    application_id, app = _lookup(data, args)
    missing = app["missing_docs"]
    job = {
        "job_id": f"JOB-{3001 + len(data['call_jobs'])}",
        "applicant": app["applicant"],
        "application_id": application_id,
        "reason": f"Collect {list_sentence(missing)}" if missing else "Status follow-up",
        "profile": "underwriting",
    }
    data["call_jobs"].append(job)

    return ActionResult(
        result=job,
        summary=f"Outbound call job {job['job_id']} created for {app['applicant']}",
        reply=(
            f"Done. I've created outbound call job {job['job_id']} to "
            f"{app['applicant']} regarding {application_id}. The call "
            "orchestration service will place the call."
        ),
        ref=f"Call job: {job['job_id']}",
    )


profile = Profile(
    id="underwriting",
    name="Credit Underwriting",
    counterparty="underwriter",
    persona=(
        "You are a credit underwriting copilot speaking with an underwriter. "
        "You help them review loan applications, risk, documents and follow-ups."
    ),
    greeting=make_greeting(
        "I'm your credit underwriting copilot.",
        "Ask me about the application you have open, the pending queue or its risk, "
        "or ask me to arrange a follow-up call with the applicant.",
    ),
    next_steps=[
        "Verify outstanding documents",
        "Continue underwriting review",
        "Confirm the follow-up call took place",
    ],
    data={
        "current_id": "APP-1024",
        "applications": {
            "APP-1024": {
                "applicant": "Ravi Menon",
                "product": "business loan",
                "amount": 1800000,
                "status": "under financial review",
                "risk_score": 0.28,
                "risk_band": "moderate",
                "missing_docs": ["latest bank statement"],
                "risk_factors": [
                    "a debt-to-income ratio of 41 percent",
                    "recent revenue variation",
                    "the pending bank statement",
                ],
                "pending": True,
            },
            "APP-1031": {
                "applicant": "Sneha Kulkarni",
                "product": "home loan",
                "amount": 4200000,
                "status": "awaiting documents",
                "risk_score": 0.41,
                "risk_band": "elevated",
                "missing_docs": ["PAN verification", "income proof"],
                "risk_factors": [
                    "a short employment history",
                    "two missed card payments last year",
                ],
                "pending": True,
            },
            "APP-1040": {
                "applicant": "Imran Sheikh",
                "product": "vehicle loan",
                "amount": 950000,
                "status": "ready for a decision",
                "risk_score": 0.19,
                "risk_band": "low",
                "missing_docs": [],
                "risk_factors": ["nothing significant"],
                "pending": False,
            },
        },
        "activities": [
            "09:10, income proof verified on APP-1040",
            "11:25, document request sent on APP-1031",
            "13:40, risk score recalculated on APP-1024",
        ],
        "call_jobs": [],
    },
    tools={
        "list_pending_applications": ToolSpec(
            name="list_pending_applications",
            topic="Pending applications",
            description="List applications that are still pending, with applicant and status.",
            parameters=object_schema({}),
            run=_list_pending,
        ),
        "get_application": ToolSpec(
            name="get_application",
            topic="Application summary",
            description="Get an application's applicant, product, amount, status, risk score and missing documents.",
            parameters=object_schema(_APPLICATION_ID),
            run=_get_application,
            ref=_ref_application,
        ),
        "get_risk_factors": ToolSpec(
            name="get_risk_factors",
            topic="Risk assessment",
            description="Get an application's risk score, risk band and the main risk factors.",
            parameters=object_schema(_APPLICATION_ID),
            run=_get_risk,
            ref=_ref_application,
        ),
        "get_missing_documents": ToolSpec(
            name="get_missing_documents",
            topic="Document checklist",
            description="Get the documents still outstanding for an application.",
            parameters=object_schema(_APPLICATION_ID),
            run=_get_missing_documents,
            ref=lambda data, args: "Document checklist",
        ),
        "get_todays_activity": ToolSpec(
            name="get_todays_activity",
            topic="Today's activity",
            description="Get today's underwriting activity log.",
            parameters=object_schema({}),
            run=lambda data, args: data["activities"],
        ),
    },
    actions={
        "schedule_followup_call": ActionSpec(
            name="schedule_followup_call",
            topic="Follow-up call",
            label="Schedule follow-up call",
            description=(
                "Create an outbound follow-up call job to the applicant of an application. "
                "Use when the user asks to call, phone, or schedule a follow-up with the applicant."
            ),
            parameters=object_schema(_APPLICATION_ID),
            validate=_validate_followup,
            describe=_describe_followup,
            execute=_execute_followup,
        ),
    },
    outbound=OutboundProfile(
        organisation="Meridian Finance",
        persona=(
            "You are calling an applicant on behalf of the credit team at Meridian Finance "
            "about their loan application. You are not an underwriter and cannot discuss "
            "anyone else's application."
        ),
        objective=(
            "Tell them what is still outstanding on their application, help them understand "
            "what to send, and answer questions about their own application."
        ),
        # Only this applicant's own record, whatever the model asks for.
        tools=["get_application", "get_missing_documents"],
        actions=[],
        fixed_args={
            "get_application": {"application_id": lambda data: data["current_id"]},
            "get_missing_documents": {"application_id": lambda data: data["current_id"]},
        },
    ),
    demo_name="Ravi Menon",
)
