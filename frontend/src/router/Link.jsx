import { useRouter } from "./context.js";

// An <a> that navigates in-app. Modified clicks (new tab, download) and external targets keep
// their browser behaviour, so it never breaks middle-click or "copy link address".
export default function Link({ to, replace = false, transition = false, onClick, children, ...rest }) {
  const { navigate } = useRouter();

  const handle = (event) => {
    onClick?.(event);

    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      (rest.target && rest.target !== "_self")
    ) {
      return;
    }

    event.preventDefault();
    navigate(to, { replace, transition });
  };

  return (
    <a href={to} onClick={handle} {...rest}>
      {children}
    </a>
  );
}
