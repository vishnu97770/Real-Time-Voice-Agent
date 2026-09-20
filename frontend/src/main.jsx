import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";

// Apply the saved theme before the first paint so a dark console never flashes white.
try {
  document.documentElement.setAttribute("data-theme", localStorage.getItem("voice-agent-theme") || "dark");
} catch {
  document.documentElement.setAttribute("data-theme", "dark"); // storage blocked
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);