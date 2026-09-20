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

_CARD_TYPES = ["debit", "credit"]


def _card(data, args):
    return next((card for card in data["cards"] if card["type"] == args.get("card")), None)


def _describe_card(card) -> str:
    return f"{card['type']} card ending {card['last4']}"


def _get_balance(data, args):
    account = data["account"]

    return {
        "account_type": account["type"],
        "account_ending": account["last4"],
        "balance": rupees(account["balance"]),
    }


def _format_transaction(item) -> dict:
    return {
        "date": item["date"],
        "merchant": item["merchant"],
        "amount": rupees(item["amount"]),
        "card": item["card"],
        **({"flagged": True} if item.get("flagged") else {}),
    }


def _get_recent_transactions(data, args):
    limit = max(1, min(int(args.get("limit") or 3), 5))

    return [_format_transaction(item) for item in data["transactions"][:limit]]


def _get_flagged_activity(data, args):
    return [_format_transaction(item) for item in data["transactions"] if item.get("flagged")]


def _get_cards(data, args):
    return [{"card": _describe_card(card), "status": card["status"]} for card in data["cards"]]


def _validate_freeze(data, args):
    card = _card(data, args)

    if not card:
        return "Which card should be frozen: debit or credit?"
    if card["status"] == "frozen":
        return f"Your {_describe_card(card)} is already frozen."
    return None


def _describe_freeze(args, data):
    card = _card(data, args)

    return (
        f"freeze your {_describe_card(card)}. It will be declined everywhere "
        "until you unfreeze it"
    )


def _execute_freeze(args, data):
    card = _card(data, args)
    card["status"] = "frozen"
    description = _describe_card(card)

    return ActionResult(
        result={"card": description, "status": "frozen"},
        summary=f"{description[0].upper()}{description[1:]} frozen",
        reply=f"Done. Your {description} is now frozen. You can unfreeze it any time from the app.",
        ref=f"Card ending {card['last4']}",
    )


profile = Profile(
    id="bank",
    name="Bank Customer Care",
    counterparty="customer",
    persona=(
        "You are the virtual assistant for Northbridge Bank, speaking with the customer "
        "Priya Sharma about her account, transactions, unusual activity and cards."
    ),
    greeting=make_greeting(
        "I'm the virtual assistant for Northbridge Bank, speaking with Priya Sharma.",
        "I can help with your balance, recent transactions, unusual activity, or "
        "freezing a card. What can I do for you?",
    ),
    next_steps=[
        "Review the flagged transaction in the banking app",
        "Order a replacement card if one was frozen",
        "Follow up with the fraud team if charges are disputed",
    ],
    data={
        "customer": "Priya Sharma",
        "account": {"type": "savings", "last4": "4821", "balance": 84250.75},
        "cards": [
            {"type": "debit", "last4": "7712", "status": "active"},
            {"type": "credit", "last4": "3390", "status": "active"},
        ],
        "transactions": [
            {"date": "18 Sep", "merchant": "Fresh Basket Groceries", "amount": 2140, "card": "debit"},
            {"date": "17 Sep", "merchant": "Metro Rail Recharge", "amount": 500, "card": "debit"},
            {
                "date": "17 Sep",
                "merchant": "TechMart Online, Singapore",
                "amount": 18999,
                "card": "credit",
                "flagged": True,
            },
            {"date": "15 Sep", "merchant": "City Pharmacy", "amount": 860, "card": "debit"},
            {"date": "14 Sep", "merchant": "StreamPlus Subscription", "amount": 649, "card": "credit"},
        ],
    },
    tools={
        "get_balance": ToolSpec(
            name="get_balance",
            topic="Account balance",
            description="Get the customer's account balance.",
            parameters=object_schema({}),
            run=_get_balance,
            ref=lambda data, args: f"Account ending {data['account']['last4']}",
        ),
        "get_recent_transactions": ToolSpec(
            name="get_recent_transactions",
            topic="Recent transactions",
            description="Get the customer's most recent transactions, newest first.",
            parameters=object_schema(
                {"limit": {"type": "integer", "description": "How many to return, 1 to 5. Default 3."}}
            ),
            run=_get_recent_transactions,
            ref=lambda data, args: f"Account ending {data['account']['last4']}",
        ),
        "get_flagged_activity": ToolSpec(
            name="get_flagged_activity",
            topic="Unusual activity",
            description="Get transactions flagged as unusual or suspicious.",
            parameters=object_schema({}),
            run=_get_flagged_activity,
            ref=lambda data, args: "Flagged transaction alert",
        ),
        "get_cards": ToolSpec(
            name="get_cards",
            topic="Card status",
            description="List the customer's cards and whether each is active or frozen.",
            parameters=object_schema({}),
            run=_get_cards,
            ref=lambda data, args: "Card list",
        ),
    },
    actions={
        "freeze_card": ActionSpec(
            name="freeze_card",
            topic="Card freeze",
            label="Freeze card",
            description=(
                "Freeze one of the customer's cards. Use when they ask to freeze, block or "
                "lock a card. If it is unclear which card, ask instead of calling this."
            ),
            parameters=object_schema(
                {"card": {"type": "string", "enum": _CARD_TYPES, "description": "Which card."}},
                required=["card"],
            ),
            validate=_validate_freeze,
            describe=_describe_freeze,
            execute=_execute_freeze,
        ),
    },
    outbound=OutboundProfile(
        organisation="Northbridge Bank",
        persona=(
            "You are calling a customer on behalf of the Northbridge Bank fraud team about "
            "unusual activity on their account."
        ),
        objective=(
            "Tell them about the flagged transaction and ask whether they made it. If they say "
            "they did not, offer to freeze the affected card. Do not discuss balances unless asked."
        ),
        actions=["freeze_card"],
    ),
    demo_name="Priya Sharma",
)
