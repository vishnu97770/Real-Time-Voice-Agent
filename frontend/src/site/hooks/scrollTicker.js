// One passive scroll/resize listener and one requestAnimationFrame for the whole landing page.
//
// Every scroll-driven effect subscribes with a `read` and a `write`. All reads (layout
// measurements) run first for every subscriber, then all writes (style changes), so a page full
// of scroll effects still costs one layout pass per frame instead of one per effect.

const subscribers = new Set();
let frame = 0;
let listening = false;

function tick() {
  frame = 0;

  const view = { y: window.scrollY, vh: window.innerHeight, vw: window.innerWidth };
  const reads = [];

  for (const subscriber of subscribers) reads.push(subscriber.read(view));

  let index = 0;

  for (const subscriber of subscribers) subscriber.write(reads[index++], view);
}

function request() {
  if (!frame) frame = requestAnimationFrame(tick);
}

export function subscribeScroll(subscriber) {
  subscribers.add(subscriber);

  if (!listening) {
    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    listening = true;
  }

  request();

  return () => {
    subscribers.delete(subscriber);

    if (subscribers.size === 0 && listening) {
      window.removeEventListener("scroll", request);
      window.removeEventListener("resize", request);
      cancelAnimationFrame(frame);
      frame = 0;
      listening = false;
    }
  };
}

// Ask for a fresh pass (a section's height just changed, a lazy section mounted).
export const requestScrollPass = request;
