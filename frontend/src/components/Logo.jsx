// The mark: five bars of a voice signal, tallest in the middle. Same silhouette as the splash.
export default function Logo({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="2" y="9" width="2.6" height="6" rx="1.3" fill="currentColor" opacity="0.55" />
      <rect x="6.6" y="5.5" width="2.6" height="13" rx="1.3" fill="currentColor" opacity="0.8" />
      <rect x="10.7" y="2" width="2.6" height="20" rx="1.3" fill="var(--brand)" />
      <rect x="14.8" y="6.5" width="2.6" height="11" rx="1.3" fill="currentColor" opacity="0.8" />
      <rect x="19.4" y="9.5" width="2.6" height="5" rx="1.3" fill="currentColor" opacity="0.55" />
    </svg>
  );
}
