// The landing page's icon set: 24px, 1.6 stroke, drawn to sit with the mono labels. One
// component, looked up by name, so sections stay short.
const PATHS = {
  user: (
    <>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M4.5 20c.6-4 3.5-6.2 7.5-6.2s6.9 2.2 7.5 6.2" />
    </>
  ),
  waves: <path d="M3 12h1.5M7 8v8M11 4.5v15M15 8.5v7M19 10.5v3M21 12h.01" />,
  agent: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M8 12h.01M10.7 9v6M13.3 10.5v3M16 12h.01" />
    </>
  ),
  ear: (
    <>
      <path d="M8 9.5a4 4 0 1 1 8 0c0 2.2-1.6 2.9-2.5 4-.7.9-.5 1.9-1.1 2.8-.5.8-1.5 1.2-2.4.9-1.1-.4-1.6-1.4-1.6-2.4" />
      <path d="M11.5 9.5a1 1 0 1 1 2 0c0 .8-.7 1-1 1.5" />
    </>
  ),
  branch: (
    <>
      <circle cx="6" cy="5.5" r="2" />
      <circle cx="6" cy="18.5" r="2" />
      <circle cx="18" cy="9" r="2" />
      <path d="M6 7.5v9M18 11c0 3.2-2.6 4-6 4H8" />
    </>
  ),
  bolt: <path d="M13.2 2.8 5 13.4h6l-.8 7.8L19 10.4h-6.2z" />,
  check: <path d="m4.8 12.6 4.6 4.6L19.4 7" />,
  calendar: (
    <>
      <rect x="3.5" y="5" width="17" height="15" rx="2.4" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </>
  ),
  bell: (
    <>
      <path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.7H5z" />
      <path d="M10 20.5a2.2 2.2 0 0 0 4 0" />
    </>
  ),
  broadcast: (
    <>
      <circle cx="12" cy="12" r="1.8" />
      <path d="M7.7 7.7a6 6 0 0 0 0 8.6M16.3 7.7a6 6 0 0 1 0 8.6M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2" />
    </>
  ),
  funnel: <path d="M3.5 5h17l-6.5 7.6V19l-4 1.8v-8.2z" />,
  workflow: (
    <>
      <rect x="3" y="3.5" width="7" height="5.4" rx="1.6" />
      <rect x="14" y="3.5" width="7" height="5.4" rx="1.6" />
      <rect x="8.5" y="15" width="7" height="5.4" rx="1.6" />
      <path d="M6.5 8.9v2.6h11V8.9M12 11.5V15" />
    </>
  ),
  phone: <path d="M7.4 3.5 5 5.4c-1.2 1 .8 5.6 4.2 9s8 5.4 9 4.2l1.9-2.4-3.9-2.8-1.8 1.8c-1.1-.8-2.6-2.1-3.5-3.5l1.8-1.8z" />,
  mic: (
    <>
      <rect x="8.5" y="3" width="7" height="11.5" rx="3.5" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7" />
    </>
  ),
  stream: <path d="M3 8h4M9 8h4M15 8h6M3 12h6M11 12h4M17 12h4M3 16h4M9 16h6M17 16h4" />,
  brain: (
    <>
      <path d="M9.4 4a3 3 0 0 0-3 3 3.2 3.2 0 0 0-2 5.2 3.3 3.3 0 0 0 1.5 5.4A3 3 0 0 0 9.4 20V4Z" />
      <path d="M14.6 4a3 3 0 0 1 3 3 3.2 3.2 0 0 1 2 5.2 3.3 3.3 0 0 1-1.5 5.4 3 3 0 0 1-3.5 2.4V4Z" />
    </>
  ),
  tools: (
    <>
      <path d="M5 19 13 11M15.5 4.5a3.8 3.8 0 0 1 4.6 4.6l-2.3-.3-1.9-1.9-.4-2.4z" />
    </>
  ),
  speaker: (
    <>
      <path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" />
      <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11" />
    </>
  ),
  chart: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  contacts: (
    <>
      <circle cx="9" cy="8.5" r="3.2" />
      <path d="M3 19.5c.5-3.3 2.9-5.2 6-5.2s5.5 1.9 6 5.2M16.5 5.8a3 3 0 0 1 0 5.4M18.5 14.6c1.6.7 2.6 2.2 2.9 4.5" />
    </>
  ),
  layout: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2.4" />
      <path d="M9 4v16M3 9h6" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="2.8" />
      <path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6" />
    </>
  ),
  rocket: (
    <>
      <path d="M12 3c3.5 2 5.5 5.5 5.5 9.5L12 18l-5.5-5.5C6.5 8.5 8.5 5 12 3z" />
      <circle cx="12" cy="10" r="1.6" />
      <path d="M8.5 15.5 6 18.5l3-1M15.5 15.5l2.5 3-3-1" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3v4M15 3v4M6.5 7h11v3.5a5.5 5.5 0 0 1-11 0zM12 16v5" />
    </>
  ),
  sliders: <path d="M4 7h9M17 7h3M4 17h3M11 17h9M13 4.5v5M7 14.5v5" />,
};

export default function SiteIcon({ name, size = 24, className = "" }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}
