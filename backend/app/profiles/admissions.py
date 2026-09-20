from app.profiles.base import (
    ActionResult,
    ActionSpec,
    OutboundProfile,
    Profile,
    ToolSpec,
    make_greeting,
    object_schema,
    rupees,
)

_SLOTS = ["Thursday at 10 AM", "Thursday at 4 PM", "Friday at 6 PM"]


def _get_application_status(data, args):
    application = data["application"]

    return {
        "application_id": application["id"],
        "program": application["program"],
        "status": application["status"],
        "interview": application["interview"],
    }


def _get_checklist(data, args):
    checklist = data["application"]["checklist"]

    return {
        "received": [entry["item"] for entry in checklist if entry["done"]],
        "pending": [entry["item"] for entry in checklist if not entry["done"]],
    }


def _get_program_details(data, args):
    program = data["program"]

    return {
        "program": data["application"]["program"],
        "duration": program["duration"],
        "fee": rupees(program["fee"]),
        "intake": program["intake"],
        "format": program["format"],
    }


def _get_deadlines(data, args):
    return data["deadlines"]


def _validate_booking(data, args):
    if args.get("slot") not in _SLOTS:
        return f"The available slots are {', '.join(_SLOTS)}."
    return None


def _describe_booking(args, data):
    return f"book a counselor call for you on {args['slot']}"


def _execute_booking(args, data):
    booking = {
        "booking_id": f"CNS-{710 + len(data['counselor_calls'])}",
        "slot": args["slot"],
        "application_id": data["application"]["id"],
    }
    data["counselor_calls"].append(booking)

    return ActionResult(
        result=booking,
        summary=f"Counselor call {booking['booking_id']} booked for {args['slot']}",
        reply=(
            f"Done. Your counselor call is booked for {args['slot']}. The confirmation "
            f"reference is {booking['booking_id']}."
        ),
        ref=f"Booking: {booking['booking_id']}",
    )


profile = Profile(
    id="admissions",
    name="Admissions Office",
    counterparty="applicant",
    persona=(
        "You are the virtual assistant for the Lakeview University admissions office, "
        "speaking with the applicant Kavya Reddy about her application, documents, "
        "program details and deadlines."
    ),
    greeting=make_greeting(
        "I'm the virtual assistant for the Lakeview University admissions office, "
        "speaking with Kavya Reddy.",
        "I can help with your application status, your document checklist, program "
        "details, or booking a counselor call. What would you like to know?",
    ),
    next_steps=[
        "Upload the remaining documents",
        "Prepare for the interview",
        "Attend the scheduled counselor call",
    ],
    data={
        "applicant": "Kavya Reddy",
        "application": {
            "id": "ADM-2026-0412",
            "program": "MSc Data Science",
            "status": "documents verified, awaiting interview",
            "checklist": [
                {"item": "transcripts", "done": True},
                {"item": "statement of purpose", "done": True},
                {"item": "recommendation letter", "done": False},
                {"item": "English proficiency score", "done": True},
            ],
            "interview": "not yet scheduled",
        },
        "program": {
            "duration": "two years",
            "fee": 385000,
            "intake": "January 2027",
            "format": "on campus with an optional industry placement",
        },
        "deadlines": [
            {"label": "Document submission", "date": "30 September 2026"},
            {"label": "Interviews", "date": "the first two weeks of October 2026"},
            {"label": "Fee payment after offer", "date": "15 November 2026"},
        ],
        "counselor_calls": [],
    },
    tools={
        "get_application_status": ToolSpec(
            name="get_application_status",
            topic="Application status",
            description="Get the application's program, status and interview state.",
            parameters=object_schema({}),
            run=_get_application_status,
            ref=lambda data, args: f"Application: {data['application']['id']}",
        ),
        "get_checklist": ToolSpec(
            name="get_checklist",
            topic="Document checklist",
            description="Get which documents have been received and which are still pending.",
            parameters=object_schema({}),
            run=_get_checklist,
            ref=lambda data, args: f"Application: {data['application']['id']}",
        ),
        "get_program_details": ToolSpec(
            name="get_program_details",
            topic="Program details",
            description="Get the program's duration, fee, intake and format.",
            parameters=object_schema({}),
            run=_get_program_details,
            ref=lambda data, args: f"Program: {data['application']['program']}",
        ),
        "get_deadlines": ToolSpec(
            name="get_deadlines",
            topic="Deadlines",
            description="Get the key admissions dates.",
            parameters=object_schema({}),
            run=_get_deadlines,
            ref=lambda data, args: "Admissions calendar",
        ),
    },
    actions={
        "schedule_counselor_call": ActionSpec(
            name="schedule_counselor_call",
            topic="Counselor call",
            label="Schedule counselor call",
            description=(
                "Book a call with an admissions counselor. Use when the applicant asks to "
                "talk to or book a counselor. If they give no time, propose Thursday at 4 PM."
            ),
            parameters=object_schema(
                {"slot": {"type": "string", "enum": _SLOTS, "description": "Chosen slot."}},
                required=["slot"],
            ),
            validate=_validate_booking,
            describe=_describe_booking,
            execute=_execute_booking,
        ),
    },
    outbound=OutboundProfile(
        organisation="Lakeview University admissions",
        persona=(
            "You are calling an applicant on behalf of the Lakeview University admissions "
            "office about the status of their application."
        ),
        objective=(
            "Tell them where their application stands, which documents are still pending, and "
            "offer to book a call with a counselor."
        ),
        actions=["schedule_counselor_call"],
    ),
    demo_name="Kavya Reddy",
)
