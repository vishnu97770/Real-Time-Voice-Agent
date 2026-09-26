import { useEffect, useId, useRef, useState } from "react";
import Icon from "./Icon";

// The signed-in user's menu: account pages, appearance and sign out. Settings
// lives here rather than in the sidebar. It shows only what the server actually says about the
// person (their email and role); no name or picture is made up.
const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");
export default function ProfileMenu({ user, theme, onToggleTheme, onNavigate, onSignOut }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const menuId = useId();

  const email = user?.email ?? null;
  const role = capitalize(user?.role);
  const isDark = theme === "dark";

  useEffect(() => {
    if (!open) return undefined;

    rootRef.current?.querySelector('[role="menuitem"]')?.focus();

    const onPointerDown = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);

    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const onKeyDown = (event) => {
    if (!open) return;

    if (event.key === "Escape") {
      event.stopPropagation();
      setOpen(false);
      buttonRef.current?.focus();
      return;
    }

    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;

    const items = [...rootRef.current.querySelectorAll('[role="menuitem"]')];
    const at = items.indexOf(document.activeElement);
    const step = event.key === "ArrowDown" ? 1 : -1;

    event.preventDefault();
    items[(at + step + items.length) % items.length]?.focus();
  };

  const choose = (action) => () => {
    setOpen(false);
    action();
  };

  return (
    <div className="profile-menu" ref={rootRef} onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={buttonRef}
        className="profile-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(!open)}
      >
        <span className="avatar">{email ? email[0].toUpperCase() : <Icon name="user" size={16} />}</span>
        <span className="profile-name">{email ?? "Local session"}</span>
        <Icon name="chevronDown" size={16} />
      </button>

      {open && (
        <div className="profile-dropdown" id={menuId} role="menu" aria-label="Account">
          <div className="profile-dropdown-head">
            <strong>{email ?? "Local session"}</strong>
            <span>{email ? role : "Not signed in (offline mode)"}</span>
          </div>

          <button type="button" role="menuitem" onClick={choose(() => onNavigate("profile"))}>
            <Icon name="user" size={18} />
            Profile
          </button>

          <button type="button" role="menuitem" onClick={choose(() => onNavigate("settings"))}>
            <Icon name="settings" size={18} />
            Settings
          </button>

          <button type="button" role="menuitem" onClick={choose(onToggleTheme)}>
            <Icon name={isDark ? "moon" : "sun"} size={18} />
            Appearance
            <span className="profile-item-hint">{isDark ? "Dark" : "Light"}</span>
          </button>

          {onSignOut && (
            <button type="button" role="menuitem" className="profile-signout" onClick={choose(onSignOut)}>
              <Icon name="signOut" size={18} />
              Sign Out
            </button>
          )}
        </div>
      )}
    </div>
  );
}
