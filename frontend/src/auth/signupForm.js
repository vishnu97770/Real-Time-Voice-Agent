// Checks for the sign-up form, kept apart from the screen so the rules can be tested. They mirror
// what the server enforces (backend/app/main.py SignupRequest, security.hash_password), so most
// mistakes are caught before a request is made; the server still has the last word.

// The backend's password policy (backend/app/security.py MIN_PASSWORD_LENGTH).
export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 200;
export const MAX_WORKSPACE_LENGTH = 120;
const MAX_EMAIL_LENGTH = 254;

// Something@something.tld, no spaces: deliberately loose (the server decides what is deliverable).
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Characters as a person counts them, and as the server does (code points, not UTF-16 units).
export const lengthOf = (text) => [...text].length;

// { workspace, email, password } -> { field: message } for each field that is not acceptable. An
// empty object means the form may be sent.
export function validateSignup({ workspace = "", email = "", password = "" }) {
  const errors = {};
  const name = workspace.trim();
  const address = email.trim();

  if (!name) errors.workspace = "Give your workspace a name.";
  else if (lengthOf(name) > MAX_WORKSPACE_LENGTH) errors.workspace = `Keep it under ${MAX_WORKSPACE_LENGTH} characters.`;

  if (!address) errors.email = "Enter your email address.";
  else if (address.length > MAX_EMAIL_LENGTH || !EMAIL.test(address)) errors.email = "That does not look like an email address.";

  if (!password) errors.password = "Choose a password.";
  else if (lengthOf(password) < MIN_PASSWORD_LENGTH) errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  else if (lengthOf(password) > MAX_PASSWORD_LENGTH) errors.password = `Use at most ${MAX_PASSWORD_LENGTH} characters.`;

  return errors;
}
