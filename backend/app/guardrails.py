"""Non-negotiable behavior shared by every profile.

1. The agent discloses that it is an AI at the start of the call.
2. The agent never asks for, and never accepts, a password, PIN, one-time
   passcode or full payment card number.

Mirrors frontend/src/runtime/guardrails.js so the offline (browser) runtime and
the server runtime behave identically.
"""

import re

AI_DISCLOSURE = "Hello, this is an AI assistant."

NEVER_ASK = (
    "For your security I will never ask for your password, PIN, "
    "one-time passcode or full card number."
)

SENSITIVE_REFUSAL = (
    "For your security, please don't say that out loud. I can never take "
    "passwords, PINs, one-time passcodes or full card numbers on a call, so "
    "I've discarded it. Let's carry on without it."
)

AGENT_SOLICITATION_REPLACEMENT = (
    "I'm sorry, I can't help with that on a call. I never ask for passwords, "
    "PINs, one-time passcodes or card numbers."
)

_SENSITIVE_PATTERNS = [
    # 13-19 digit run, optionally spaced or dashed: a full card number
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    # a secret keyword followed closely by digits
    re.compile(
        r"\b(?:pin|otp|password|passcode|cvv|cvc|one[- ]time (?:password|passcode|code))\b"
        r"[^.\n]{0,30}?\b\d{3,8}\b",
        re.IGNORECASE,
    ),
    # a secret keyword followed by "is" ("my pin is ...")
    re.compile(
        r"\b(?:my|the)\s+(?:pin|otp|password|passcode|cvv|cvc)\s+(?:is|was)\b[^.\n]*",
        re.IGNORECASE,
    ),
]

# Last line of defence on agent output, whichever brain produced it.
_SOLICITATION = re.compile(
    r"\b(?:tell|share|give|provide|enter|say|read|send|confirm|type)\b[^.?!\n]{0,40}"
    r"\b(?:password|pin|otp|passcode|one[- ]time|cvv|cvc|card number)\b",
    re.IGNORECASE,
)

_YES = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|confirm|confirmed|go ahead|please do|do it|okay|ok"
    r"|proceed|correct|that's right)\b",
    re.IGNORECASE,
)
_NO = re.compile(
    r"^(?:no|nope|nah|cancel|stop|don't|do not|never mind|nevermind|not now|abort)\b",
    re.IGNORECASE,
)

# Things a caller says while thinking, which are neither consent nor a request.
# The whole utterance must be made of these: "wait, what's my balance" is a request.
_FILLER = re.compile(
    r"^(?:(?:h+m+|u+h+|u+m+|er+m*|wait|hold on|one moment|just a (?:sec|second|moment)"
    r"|let me think|maybe|i think|not sure|sorry|pardon|repeat|say that again|what|okay|ok)"
    r"[\s,.!?]*)+$",
    re.IGNORECASE,
)


_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
# Four or more digit-words in a row: "four one one one".
_SPOKEN_DIGITS = re.compile(rf"\b(?:(?:{'|'.join(_DIGIT_WORDS)})\b[\s,.-]*){{4,}}", re.IGNORECASE)


def spoken_digits_to_numerals(text: str) -> str:
    """Turn "my pin is one two three four" into "my pin is 1234".

    Speech recognition sometimes writes digits as words. The secret detector looks
    for numerals, so without this a card number or PIN read out loud would slip past
    it and be kept in the transcript. Used on anything that comes from speech.
    """

    def convert(match: re.Match) -> str:
        return "".join(_DIGIT_WORDS[word.lower()] for word in re.findall(r"[A-Za-z]+", match.group(0))) + " "

    return _SPOKEN_DIGITS.sub(convert, text).strip()


def detect_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS)


def redact_sensitive(text: str) -> str:
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def solicits_secret(text: str) -> bool:
    return bool(_SOLICITATION.search(text))


def classify_confirmation(text: str) -> str:
    value = text.strip()

    if _NO.match(value):
        return "no"
    if _YES.match(value):
        return "yes"
    return "other"


_IDENTITY_YES = re.compile(
    r"^(?:speaking|this is (?:he|she|him|her)|that's me|it's me|it is me|this is me|i am|i'm)\b",
    re.IGNORECASE,
)


def classify_identity(text: str) -> str:
    """Did the person who answered confirm they are who we asked for?

    "no" ends the call without disclosing anything: it is safer to hang up on
    someone who said "no, who is this?" than to keep talking to a wrong party.
    """
    if _IDENTITY_YES.match(text.strip()):
        return "yes"
    return classify_confirmation(text)


def is_filler(text: str) -> bool:
    """True if an utterance is not a real request: a hesitation or 1-2 words.

    While an action awaits confirmation, filler is answered by asking again.
    Anything longer is treated as the caller moving on to a new request, which
    lets the unconfirmed action lapse.
    """
    value = text.strip()

    return bool(_FILLER.match(value)) or len(value.split()) <= 2
