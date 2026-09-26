import { useEffect, useRef } from "react";
import { subscribeScroll } from "./hooks/scrollTicker.js";

// A quiet "where am I in the story" marker: chapter number and name, and how far down the page
// the visitor is. Its text and bar are written straight to the DOM from the scroll ticker.
export default function Chapters() {
  const numberRef = useRef(null);
  const nameRef = useRef(null);
  const rootRef = useRef(null);
  const last = useRef("");

  useEffect(
    () =>
      subscribeScroll({
        read: ({ y, vh }) => {
          const sections = [...document.querySelectorAll("[data-chapter]")];
          const middle = vh * 0.5;
          let index = 0;

          sections.forEach((section, at) => {
            if (section.getBoundingClientRect().top <= middle) index = at;
          });

          const total = document.documentElement.scrollHeight - vh;

          return { index, count: sections.length, name: sections[index]?.dataset.chapter ?? "", page: total > 0 ? Math.min(1, y / total) : 0 };
        },
        write: ({ index, count, name, page }) => {
          const key = `${index}/${count}/${name}`;

          rootRef.current?.style.setProperty("--page", page.toFixed(4));

          if (key === last.current) return;

          last.current = key;
          numberRef.current.textContent = `${String(index + 1).padStart(2, "0")} / ${String(count).padStart(2, "0")}`;
          nameRef.current.textContent = name;
        },
      }),
    [],
  );

  return (
    <div ref={rootRef} className="lp-chapter" aria-hidden="true">
      <b ref={numberRef}>01 / 01</b>
      <span className="lp-chapter-bar">
        <i />
      </span>
      <span ref={nameRef}>Intro</span>
    </div>
  );
}
