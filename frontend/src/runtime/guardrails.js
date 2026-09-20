// Non-negotiable behavior shared by every profile.
// 1. The agent discloses that it is an AI at the start of the call.
// 2. The agent never asks for, and never accepts, a password, PIN, one-time
//    passcode or full payment card number.

export const AI_DISCLOSURE = "Hello, this is an AI assistant.";

export const NEVER_ASK =
  "For your security I will never ask for your password, PIN, one-time passcode or full card number.";

export const SENSITIVE_REFUSAL =
  "For your security, please don't say that out loud. I can never take passwords, PINs, one-time passcodes or full card numbers on a call, so I've discarded it. Let's carry on without it.";

const SENSITIVE_PATTERNS = [
  // 13-19 digit run, optionally spaced or dashed: a full card number
  /\b(?:\d[ -]?){13,19}\b/g,
  // a secret keyword followed closely by digits
  /\b(?:pin|otp|password|passcode|cvv|cvc|one[- ]time (?:password|passcode|code))\b[^.\n]{0,30}?\b\d{3,8}\b/gi,
  // a secret keyword followed by "is" ("my pin is ...")
  /\b(?:my|the)\s+(?:pin|otp|password|passcode|cvv|cvc)\s+(?:is|was)\b[^.\n]*/gi,
];

export function detectSensitive(text) {
  return SENSITIVE_PATTERNS.some((pattern) => {
    pattern.lastIndex = 0;
    return pattern.test(text);
  });
}

// Used for anything that is displayed, logged or stored.
export function redactSensitive(text) {
  return SENSITIVE_PATTERNS.reduce(
    (value, pattern) => value.replace(pattern, "[redacted]"),
    text
  );
}

// Last line of defence on agent output, for any brain (rule-based or LLM):
// an agent utterance that solicits a secret is replaced before it is spoken.
const SOLICITATION =
  /\b(?:tell|share|give|provide|enter|say|read|send|confirm|type)\b[^.?!\n]{0,40}\b(?:password|pin|otp|passcode|one[- ]time|cvv|cvc|card number)\b/i;

export function sanitizeAgentText(text) {
  if (!SOLICITATION.test(text)) return { text, blocked: false };

  return {
    text: "I'm sorry, I can't help with that on a call. I never ask for passwords, PINs, one-time passcodes or card numbers.",
    blocked: true,
  };
}

const YES =
  /^(?:yes|yeah|yep|yup|sure|confirm|confirmed|go ahead|please do|do it|okay|ok|proceed|correct|that's right)\b/i;
const NO =
  /^(?:no|nope|nah|cancel|stop|don't|do not|never mind|nevermind|not now|abort)\b/i;

export function classifyConfirmation(text) {
  const value = text.trim();

  if (NO.test(value)) return "no";
  if (YES.test(value)) return "yes";
  return "other";
}
