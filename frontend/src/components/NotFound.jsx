import Logo from "./Logo.jsx";
import { useSignupOpen } from "../auth/context.js";
import Link from "../router/Link.jsx";

export default function NotFound() {
  const signupOpen = useSignupOpen();

  return (
    <div className="gate">
      <div className="gate-card">
        <Logo size={28} />
        <h1>Page not found</h1>
        <p>There is nothing at this address.</p>
        <div className="gate-actions">
          <Link to="/" className="gate-btn gate-btn--primary">
            Back to the site
          </Link>
          <Link to="/signin" className="gate-btn">
            Sign in
          </Link>
          {signupOpen && (
            <Link to="/signup" className="gate-btn">
              Sign up
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
