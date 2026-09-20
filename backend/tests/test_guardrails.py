from app.guardrails import (
    classify_confirmation,
    detect_sensitive,
    is_filler,
    redact_sensitive,
    solicits_secret,
)
from app.profiles import PROFILES


def test_every_profile_discloses_ai_and_promises_never_to_ask_for_secrets():
    for profile in PROFILES.values():
        assert "AI assistant" in profile.greeting, profile.id
        assert "never ask for your password, PIN" in profile.greeting, profile.id


def test_secrets_are_detected_and_redacted():
    for text in [
        "my pin is 4821",
        "the OTP is 482913",
        "my password is hunter2",
        "4111-1111-1111-1111",
        "card 4111 1111 1111 1111",
    ]:
        assert detect_sensitive(text), text
        assert "[redacted]" in redact_sensitive(text), text


def test_normal_requests_are_not_flagged():
    for text in [
        "What's my balance?",
        "Freeze my credit card ending 3390",
        "I forgot my password",
        "How do I reset my PIN?",
        "Call me on 9876543210",
        "Show me application APP-1024",
    ]:
        assert not detect_sensitive(text), text
        assert redact_sensitive(text) == text, text


def test_agent_solicitation_is_detected():
    assert solicits_secret("Please tell me your OTP to continue.")
    assert solicits_secret("Could you read out your card number?")
    assert not solicits_secret("Your balance is 84,250 rupees.")
    assert not solicits_secret("I will never ask for your password, PIN, one-time passcode or full card number.")


def test_confirmation_classifier():
    assert classify_confirmation("Yes, go ahead") == "yes"
    assert classify_confirmation("no thanks") == "no"
    assert classify_confirmation("what about my balance") == "other"
    assert classify_confirmation("yesterday I called") == "other"


def test_filler_is_not_a_request():
    assert is_filler("hmm let me think")
    assert is_filler("uh")
    assert is_filler("okay then")
    assert not is_filler("What's my balance?")
    assert not is_filler("show me recent transactions")
