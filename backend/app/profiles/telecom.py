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

_PLANS = {
    "Postpaid 599": {"price": 599, "data": "60 GB", "extras": "unlimited calls"},
    "Postpaid 799": {"price": 799, "data": "120 GB", "extras": "unlimited calls and a streaming bundle"},
    "Postpaid 999": {
        "price": 999,
        "data": "250 GB",
        "extras": "unlimited calls, a streaming bundle and international roaming",
    },
}


def _get_usage(data, args):
    usage = data["usage"]

    return {
        "used_gb": usage["used_gb"],
        "limit_gb": usage["limit_gb"],
        "remaining_gb": usage["limit_gb"] - usage["used_gb"],
        "resets_on": usage["resets_on"],
    }


def _get_bill(data, args):
    bill = data["bill"]

    return {"amount": rupees(bill["amount"]), "due_date": bill["due_date"], "status": bill["status"]}


def _get_plan(data, args):
    plan = data["plan"]

    return {
        "plan": plan,
        "monthly_price": rupees(data["plans"][plan]["price"]),
        "renews_on": data["usage"]["resets_on"],
    }


def _list_plans(data, args):
    return {
        "current_plan": data["plan"],
        "plans": [
            {"name": name, "monthly_price": rupees(plan["price"]), "data": plan["data"], "includes": plan["extras"]}
            for name, plan in data["plans"].items()
        ],
    }


def _validate_upgrade(data, args):
    plan = args.get("plan")

    if plan not in data["plans"]:
        return f"I can upgrade you to one of: {', '.join(data['plans'])}."
    if plan == data["plan"]:
        return f"You're already on {plan}."
    return None


def _describe_upgrade(args, data):
    plan = data["plans"][args["plan"]]

    return (
        f"upgrade you from {data['plan']} to {args['plan']}, which is "
        f"{rupees(plan['price'])} a month with {plan['data']}. The new price applies "
        "from your next bill"
    )


def _execute_upgrade(args, data):
    previous = data["plan"]
    data["plan"] = args["plan"]

    return ActionResult(
        result={"previous_plan": previous, "new_plan": args["plan"]},
        summary=f"Plan upgraded from {previous} to {args['plan']}",
        reply=f"Done. You're now on {args['plan']}. The new price applies from your next bill.",
        ref=f"Number {data['number']}",
    )


profile = Profile(
    id="telecom",
    name="Telecom Support",
    counterparty="subscriber",
    persona=(
        "You are the virtual assistant for Vertex Mobile, speaking with the subscriber "
        "Meera Iyer about her data usage, bill, plan renewal and upgrades."
    ),
    greeting=make_greeting(
        "I'm the virtual assistant for Vertex Mobile, speaking with Meera Iyer.",
        "I can help with your data usage, your bill, plan renewal, or an upgrade. "
        "What can I do for you?",
    ),
    next_steps=[
        "Pay the outstanding bill before the due date",
        "Check the new plan benefits in the app",
        "Review usage again before the next renewal",
    ],
    data={
        "subscriber": "Meera Iyer",
        "number": "ending 0210",
        "plan": "Postpaid 599",
        "usage": {"used_gb": 42, "limit_gb": 60, "resets_on": "28 September 2026"},
        "bill": {"amount": 599, "due_date": "25 September 2026", "status": "unpaid"},
        "plans": _PLANS,
    },
    tools={
        "get_usage": ToolSpec(
            name="get_usage",
            topic="Data usage",
            description="Get data used, remaining and when the allowance resets.",
            parameters=object_schema({}),
            run=_get_usage,
            ref=lambda data, args: f"Number {data['number']}",
        ),
        "get_bill": ToolSpec(
            name="get_bill",
            topic="Current bill",
            description="Get the current bill amount, due date and payment status.",
            parameters=object_schema({}),
            run=_get_bill,
            ref=lambda data, args: f"Number {data['number']}",
        ),
        "get_plan": ToolSpec(
            name="get_plan",
            topic="Plan renewal",
            description="Get the current plan, its monthly price and renewal date.",
            parameters=object_schema({}),
            run=_get_plan,
            ref=lambda data, args: f"Number {data['number']}",
        ),
        "list_plans": ToolSpec(
            name="list_plans",
            topic="Available plans",
            description="List the plans available to switch to, with price, data and inclusions.",
            parameters=object_schema({}),
            run=_list_plans,
            ref=lambda data, args: "Plan catalogue",
        ),
    },
    actions={
        "upgrade_plan": ActionSpec(
            name="upgrade_plan",
            topic="Plan upgrade",
            label="Upgrade plan",
            description=(
                "Change the subscriber to a different plan. Use when they ask to upgrade "
                "or switch plans. If they don't say which plan, propose Postpaid 799."
            ),
            parameters=object_schema(
                {"plan": {"type": "string", "enum": list(_PLANS), "description": "Target plan."}},
                required=["plan"],
            ),
            validate=_validate_upgrade,
            describe=_describe_upgrade,
            execute=_execute_upgrade,
        ),
    },
    outbound=OutboundProfile(
        organisation="Vertex Mobile",
        persona=(
            "You are calling a subscriber on behalf of Vertex Mobile about their plan renewal "
            "and data usage."
        ),
        objective=(
            "Tell them their renewal date and how much data they have used, and offer an upgrade "
            "only if they are close to their limit or ask about one."
        ),
        actions=["upgrade_plan"],
    ),
    demo_name="Meera Iyer",
)
