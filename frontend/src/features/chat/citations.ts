export type Segment = { kind: "text"; text: string; flagged?: true } | { kind: "cite"; labels: string[] };

const CITATION = /\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]/g;

/** Split an answer into plain text and citation groups such as "[S1]" or "[S2, S3]";
 * text inside any of the `flagged` sentences is marked. */
export function splitCitations(text: string, flagged: string[] = []): Segment[] {
  const ranges = flagged
    .filter((sentence) => sentence && text.includes(sentence))
    .map((sentence) => [text.indexOf(sentence), text.indexOf(sentence) + sentence.length] as const)
    .sort((a, b) => a[0] - b[0]);
  const segments: Segment[] = [];
  const pushText = (from: number, to: number) => {
    let at = from;
    for (const [start, end] of ranges) {
      if (end <= at || start >= to) continue;
      if (start > at) segments.push({ kind: "text", text: text.slice(at, start) });
      const stop = Math.min(end, to);
      segments.push({ kind: "text", text: text.slice(Math.max(start, at), stop), flagged: true });
      at = stop;
    }
    if (at < to) segments.push({ kind: "text", text: text.slice(at, to) });
  };
  let cursor = 0;
  for (const match of text.matchAll(CITATION)) {
    const index = match.index ?? 0;
    if (index > cursor) pushText(cursor, index);
    segments.push({ kind: "cite", labels: (match[1] ?? "").split(/[,;]/).map((label) => label.trim()) });
    cursor = index + match[0].length;
  }
  if (cursor < text.length) pushText(cursor, text.length);
  return segments;
}

export const STAGE_LABELS: Record<string, string> = {
  condense: "Reading the conversation so far",
  route: "Choosing where to look",
  embed: "Understanding your question",
  decompose: "Breaking the question into parts",
  retrieve: "Searching your documents",
  "reformulate:rewrite": "Rephrasing the search",
  "reformulate:broaden": "Looking more widely",
  generate: "Writing the answer",
  verify: "Checking the answer against its sources",
};
