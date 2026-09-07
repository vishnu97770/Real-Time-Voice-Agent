// import Icon from "./Icon";

// export default function TopNavbar() {
//   return (
//     <header className="top-navbar">
//       <div className="brand">
//         <div className="brand-logo">
//           <span>AI</span>
//         </div>

//         <div>
//           <h1>Real-Time Voice Agent</h1>
//           <p>Credit Underwriting Workspace</p>
//         </div>
//       </div>

//       <div className="navbar-right">
//         <div className="online-status">
//           <span className="online-dot" />
//           Online
//         </div>

//         <button className="icon-button" aria-label="Notifications">
//           <Icon name="bell" size={21} />
//         </button>

//         <button
//           className="theme-toggle"
//           onClick={onToggleTheme}
//           aria-label={
//             isDark ? "Switch to light mode" : "Switch to dark mode"
//           }
//           title={isDark ? "Light mode" : "Dark mode"}
//         >
//           <span className={`thee-icon ${!isDark ? "active" : ""}`}>
//             ☀
//           </span>

//           <span className={`thee-icon ${!isDark ? "active" : ""}`}>
//             ☾
//           </span>
//         </button>

//         <div className="profile">
//           <div className="avatar">VV</div>

//           <span>V. Vishnu</span>

//           <span className="dropdown">⌄</span>
//         </div>
//       </div>
//     </header>
//   );
// }




import Icon from "./Icon";

export default function TopNavbar({ theme, onToggleTheme }) {
  const isDark = theme === "dark";

  return (
    <header className="top-navbar">
      {/* Brand */}
      <div className="brand">
        <div className="brand-logo">
          <span>AI</span>
        </div>

        <div>
          <h1>Real-Time Voice Agent</h1>
          <p>Credit Underwriting Workspace</p>
        </div>
      </div>

      {/* Right Side */}
      <div className="navbar-right">
        {/* Online Status */}
        <div className="online-status">
          <span className="online-dot" />
          <span>Online</span>
        </div>

        {/* Notifications */}
        <button
          type="button"
          className="icon-button"
          aria-label="Notifications"
          title="Notifications"
        >
          <Icon name="bell" size={21} />
        </button>

        {/* Light / Dark Mode */}
        <button
          type="button"
          className="theme-toggle"
          onClick={onToggleTheme}
          aria-label={
            isDark ? "Switch to light mode" : "Switch to dark mode"
          }
          title={isDark ? "Light mode" : "Dark mode"}
        >
          <span
            className={`theme-icon ${
              !isDark ? "active" : ""
            }`}
          >
            ☀
          </span>

          <span
            className={`theme-icon ${
              isDark ? "active" : ""
            }`}
          >
            ☾
          </span>
        </button>

        {/* User Profile */}
        <div className="profile">
          <div className="avatar">VV</div>

          <span>V. Vishnu</span>

          <span className="dropdown">⌄</span>
        </div>
      </div>
    </header>
  );
}