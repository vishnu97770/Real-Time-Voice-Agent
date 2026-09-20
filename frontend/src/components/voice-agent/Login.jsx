import { useState } from "react";
import { login } from "../../runtime/auth.js";

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      onSignedIn(await login(email.trim(), password));
    } catch (failure) {
      setError(failure.message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="callee-page">
      <header className="callee-header">
        <div className="brand-logo">
          <span>AI</span>
        </div>
        <h1>Real-Time Voice Agent</h1>
      </header>

      <main className="callee-card">
        <h2>Sign in</h2>
        <p className="callee-note">Operators only. Ask an administrator for an account.</p>

        <form className="login-form" onSubmit={submit}>
          <label htmlFor="login-email">Email</label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />

          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />

          {error && (
            <p className="voice-notice" role="alert">
              {error}
            </p>
          )}

          <button className="callee-answer login-button" type="submit" disabled={busy || !email || !password}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </main>
    </div>
  );
}
