export type Segment = { kind: "text"; text: string } | { kind: "cite"; labels: string[] };

const CITATION = /\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]/g;

/** Split an answer into plain text and citation groups such as "[S1]" or "[S2, S3]". */
export function splitCitations(text: string): Segment[] {
  const segments: Segment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(CITATION)) {
    const index = match.index ?? 0;
    if (index > cursor) segments.push({ kind: "text", text: text.slice(cursor, index) });
    segments.push({ kind: "cite", labels: (match[1] ?? "").split(/[,;]/).map((label) => label.trim()) });
    cursor = index + match[0].length;
  }
  if (cursor < text.length) segments.push({ kind: "text", text: text.slice(cursor) });
  return segments;
}

export const STAGE_LABELS: Record<string, string> = {
  condense: "Reading the conversation so far",
  embed: "Understanding your question",
  decompose: "Breaking the question into parts",
  retrieve: "Searching your documents",
  "reformulate:rewrite": "Rephrasing the search",
  "reformulate:broaden": "Looking more widely",
  generate: "Writing the answer",
  verify: "Checking every claim has a source",
};
