// One badge for every status in the product (consent, workflow state, call/job outcome).
// `tone` is semantic - ok | info | warn - never the brand accent, so a status colour always
// means the same thing on every screen.
export default function StatusBadge({ tone = "info", children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
