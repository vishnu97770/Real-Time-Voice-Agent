// A full stop only ends a sentence when whitespace follows, so "0.28" and
// "₹84,250.75" stay in one piece. Same rule as the server's SentenceSplitter.
const BOUNDARY = /[.!?]+["')\]]*\s+/;

export function splitSentences(text) {
  const sentences = [];
  let rest = text;
  let match;

  while ((match = BOUNDARY.exec(rest))) {
    const end = match.index + match[0].length;

    sentences.push(rest.slice(0, end).trim());
    rest = rest.slice(end);
  }

  if (rest.trim()) sentences.push(rest.trim());

  return sentences;
}
