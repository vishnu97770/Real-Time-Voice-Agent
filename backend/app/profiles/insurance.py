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

_CLAIM_TYPES = ["hospitalisation", "pharmacy reimbursement", "diagnostics"]


def _get_policy(data, args):
    policy = data["policy"]

    return {
        "policy_id": policy["id"],
        "type": policy["type"],
        "sum_insured": rupees(policy["sum_insured"]),
        "covers": policy["covers"],
    }


def _get_renewal(data, args):
    policy = data["policy"]

    return {
        "policy_id": policy["id"],
        "renewal_date": policy["renewal_date"],
        "days_to_renewal": policy["days_to_renewal"],
        "premium": rupees(policy["premium"]),
    }


def _get_claim_status(data, args):
    return [
        {
            "claim_id": claim["id"],
            "type": claim["type"],
            "status": claim["status"],
            "amount": rupees(claim["amount"]) if claim["amount"] else None,
            "pending_documents": claim["pending_docs"],
        }
        for claim in data["claims"]
    ]


def _validate_claim(data, args):
    if args.get("type") not in _CLAIM_TYPES:
        return "What is the claim for: hospitalisation, pharmacy reimbursement, or diagnostics?"
    return None


def _describe_claim(args, data):
    return f"file a new {args['type']} claim under policy {data['policy']['id']}"


def _execute_claim(args, data):
    claim = {
        "id": f"CLM-{5530 + len(data['claims']) - 1}",
        "type": args["type"],
        "status": "registered",
        "amount": 0,
        "pending_docs": ["claim form", "supporting bills"],
    }
    data["claims"].insert(0, claim)

    return ActionResult(
        result=claim,
        summary=f"Claim {claim['id']} filed ({claim['type']})",
        reply=(
            f"Done. I've filed claim {claim['id']} for {claim['type']}. You'll need to "
            f"send the {list_sentence(claim['pending_docs'])}, and the claims team will "
            "contact you."
        ),
        ref=f"Claim: {claim['id']}",
    )


profile = Profile(
    id="insurance",
    name="Insurance Desk",
    counterparty="policyholder",
    persona=(
        "You are the virtual assistant for Shieldwell Insurance, speaking with the "
        "policyholder Arjun Nair about his policy, renewal and claims."
    ),
    greeting=make_greeting(
        "I'm the virtual assistant for Shieldwell Insurance, speaking with Arjun Nair.",
        "I can help with your renewal, an existing claim, your coverage, or filing a "
        "new claim. What would you like to do?",
    ),
    next_steps=[
        "Send any documents still pending on your open claim",
        "Pay the renewal premium before the due date",
        "Track any newly filed claim in the portal",
    ],
    data={
        "policyholder": "Arjun Nair",
        "policy": {
            "id": "POL-88213",
            "type": "family health",
            "sum_insured": 500000,
            "premium": 14200,
            "renewal_date": "14 October 2026",
            "days_to_renewal": 25,
            "covers": [
                "hospitalisation",
                "day-care procedures",
                "pre and post hospitalisation costs",
                "ambulance charges",
            ],
        },
        "claims": [
            {
                "id": "CLM-5521",
                "type": "hospitalisation",
                "status": "under assessment",
                "amount": 62000,
                "pending_docs": ["discharge summary"],
            }
        ],
    },
    tools={
        "get_policy": ToolSpec(
            name="get_policy",
            topic="Coverage",
            description="Get the policy type, sum insured and what it covers.",
            parameters=object_schema({}),
            run=_get_policy,
            ref=lambda data, args: f"Policy: {data['policy']['id']}",
        ),
        "get_renewal": ToolSpec(
            name="get_renewal",
            topic="Policy renewal",
            description="Get the renewal date, days remaining and renewal premium.",
            parameters=object_schema({}),
            run=_get_renewal,
            ref=lambda data, args: f"Policy: {data['policy']['id']}",
        ),
        "get_claim_status": ToolSpec(
            name="get_claim_status",
            topic="Claim status",
            description="Get the status and pending documents of the policyholder's claims, newest first.",
            parameters=object_schema({}),
            run=_get_claim_status,
            ref=lambda data, args: f"Claim: {data['claims'][0]['id']}",
        ),
    },
    actions={
        "file_claim": ActionSpec(
            name="file_claim",
            topic="New claim",
            label="File claim",
            description=(
                "File a new insurance claim. Use when the user wants to file, raise or "
                "register a claim. If the claim type is unclear, ask instead of calling this."
            ),
            parameters=object_schema(
                {"type": {"type": "string", "enum": _CLAIM_TYPES, "description": "Kind of claim."}},
                required=["type"],
            ),
            validate=_validate_claim,
            describe=_describe_claim,
            execute=_execute_claim,
        ),
    },
    outbound=OutboundProfile(
        organisation="Shieldwell Insurance",
        persona=(
            "You are calling a policyholder on behalf of Shieldwell Insurance about their "
            "policy renewal or an open claim."
        ),
        objective=(
            "Remind them of the renewal date and premium, update them on any open claim, and "
            "offer to file a new claim only if they ask."
        ),
        actions=["file_claim"],
    ),
    demo_name="Arjun Nair",
)
