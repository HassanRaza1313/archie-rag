import { describe, expect, it } from "vitest";
import { unseenBuild } from "../components/Layout";
import { centreOf, loadConversation, originWithin, passageParts } from "../features/chat/ChatPage";
import { splitCitations } from "../features/chat/citations";
import { qualityVerdict, TEMPLATES } from "../features/documents/DocumentsPage";
import { parseGolden } from "../features/eval/EvalPage";
import { openingChatbot } from "../features/kbs/HomePage";
import { rangeProblem } from "../features/setup/SchemaField";
import { type JsonSchema, type KbStatus, type KnowledgeBase, parseSseBlock, readSse } from "./api";
import { formatMs, formatPercent, pages, relativeTime } from "./format";
import { applyPreset, buildStages, changedFields, groupStages, plainError, primaryAction, setField } from "./schema";

const schema: JsonSchema = {
  properties: {
    source: { $ref: "#/$defs/SourceConfig" },
    chunking: { $ref: "#/$defs/ChunkingConfig" },
  },
  $defs: {
    SourceConfig: {
      title: "Data source",
      summary: "Where documents come from.",
      rebuild: true,
      properties: {
        type: { title: "Source type", enum: ["local_folder", "uploads"], type: "string", tier: "essential" },
        file_types: { title: "File types", type: "array", items: { enum: [".pdf", ".md"] } },
        include: { title: "Include", type: "array", items: { type: "string" }, tier: "advanced" },
      },
    },
    ChunkingConfig: {
      title: "Chunking",
      properties: {
        chunk_size: { title: "Chunk size", type: "integer", minimum: 64, maximum: 4096, default: 512 },
        section_headers: { title: "Headers", type: "boolean", tier: "advanced" },
        system_prompt: { title: "Prompt", type: "string", default: "short" },
      },
    },
  },
};

describe("buildStages", () => {
  const stages = buildStages(schema);

  it("keeps stage order and metadata", () => {
    expect(stages.map((s) => s.key)).toEqual(["source", "chunking"]);
    expect(stages[0]).toMatchObject({ title: "Data source", rebuild: true, summary: "Where documents come from." });
    expect(stages[1]?.rebuild).toBe(false);
  });

  it("maps JSON Schema types to controls", () => {
    const kinds = Object.fromEntries(stages.flatMap((s) => s.fields).map((f) => [f.key, f.kind]));
    expect(kinds).toEqual({
      type: "enum",
      file_types: "multi",
      include: "list",
      chunk_size: "int",
      section_headers: "bool",
      system_prompt: "longtext",
    });
  });

  it("reads tiers, options and ranges", () => {
    const fields = stages.flatMap((s) => s.fields);
    expect(fields.find((f) => f.key === "include")?.tier).toBe("advanced");
    expect(fields.find((f) => f.key === "type")?.tier).toBe("essential");
    expect(fields.find((f) => f.key === "file_types")?.options).toEqual([".pdf", ".md"]);
    expect(fields.find((f) => f.key === "chunk_size")).toMatchObject({ min: 64, max: 4096, defaultValue: 512 });
  });
});

describe("config helpers", () => {
  const base = { source: { path: "a" }, chunking: { chunk_size: 512, overlap_pct: 12 } };

  it("setField is immutable", () => {
    const next = setField(base, "chunking", "chunk_size", 256);
    expect(next.chunking?.chunk_size).toBe(256);
    expect(base.chunking.chunk_size).toBe(512);
    expect(next.source).toBe(base.source);
  });

  it("changedFields lists differing paths, including arrays", () => {
    const next = setField(setField(base, "chunking", "chunk_size", 256), "source", "include", ["*.pdf"]);
    expect([...changedFields(base, next)].sort()).toEqual(["chunking.chunk_size", "source.include"]);
    expect(changedFields(base, base).size).toBe(0);
    expect(changedFields(undefined, base).size).toBe(0);
  });

  it("applyPreset keeps the data source", () => {
    const preset = { source: { path: "preset-path" }, chunking: { chunk_size: 400 } };
    const merged = applyPreset(base, preset);
    expect(merged.source).toEqual({ path: "a" });
    expect(merged.chunking).toEqual({ chunk_size: 400, overlap_pct: 12 });
  });
});

describe("server-sent events", () => {
  it("parses event and multi-line data", () => {
    expect(parseSseBlock('event: token\ndata: {"a":1}')).toEqual({ event: "token", data: '{"a":1}' });
    expect(parseSseBlock("data: one\ndata: two")).toEqual({ event: "message", data: "one\ntwo" });
    expect(parseSseBlock(": comment only")).toBeNull();
  });

  it("streams events split across chunks", async () => {
    const encoder = new TextEncoder();
    const chunks = ['event: token\ndata: {"text":"He', 'llo"}\n\nevent: done\r\n', 'data: {"type":"done"}\r\n\r\n'];
    const body = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    const events: unknown[] = [];
    for await (const event of readSse(new Response(body))) events.push(event);
    expect(events).toEqual([{ text: "Hello" }, { type: "done" }]);
  });
});

describe("splitCitations", () => {
  it("separates text from citation groups", () => {
    expect(splitCitations("Ratio is 3:1 [S1]. Turn weekly [S2, S3].")).toEqual([
      { kind: "text", text: "Ratio is 3:1 " },
      { kind: "cite", labels: ["S1"] },
      { kind: "text", text: ". Turn weekly " },
      { kind: "cite", labels: ["S2", "S3"] },
      { kind: "text", text: "." },
    ]);
    expect(splitCitations("No sources")).toEqual([{ kind: "text", text: "No sources" }]);
    expect(splitCitations("[S1;S4]")).toEqual([{ kind: "cite", labels: ["S1", "S4"] }]);
  });
});

describe("parseGolden", () => {
  it("accepts JSONL and reports the first bad line", () => {
    expect(parseGolden('{"question":"a"}\n\n{"question":"b","expected_sources":["x.md"]}').items).toHaveLength(2);
    expect(parseGolden('{"question":"a"}\nnot json').error).toBe("line 2: not valid JSON");
    expect(parseGolden('{"q":"a"}').error).toBe('line 1: missing "question"');
    expect(parseGolden("  ").error).toBe("add at least one question");
  });
});

describe("format", () => {
  it("formats durations, percentages and page spans", () => {
    expect(formatMs(null)).toBe("–");
    expect(formatMs(240)).toBe("240 ms");
    expect(formatMs(2410)).toBe("2.41 s");
    expect(formatMs(12_340)).toBe("12.3 s");
    expect(formatPercent(0.256)).toBe("26%");
    expect(pages(3, 3)).toBe("p. 3");
    expect(pages(3, 5)).toBe("pp. 3–5");
    expect(pages(null, null)).toBe("");
  });

  it("formats relative time", () => {
    const now = 1_000_000_000_000;
    expect(relativeTime(now / 1000 - 10, now)).toBe("just now");
    expect(relativeTime(now / 1000 - 7200, now)).toMatch(/2 hours ago/);
    expect(relativeTime(null, now)).toBe("–");
  });
});

describe("setup journey", () => {
  const stages = [
    { key: "source", title: "Data source", summary: "", rebuild: true, fields: [] },
    { key: "retrieval", title: "Retrieval", summary: "", rebuild: false, fields: [] },
    { key: "privacy", title: "Privacy", summary: "", rebuild: false, fields: [] },
  ];
  const index = new Set(["source"]);

  it("groups stages by what a change costs", () => {
    expect(groupStages(stages).map((g) => [g.title, g.stages.map((s) => s.key)])).toEqual([
      ["What it reads", ["source"]],
      ["How it answers", ["retrieval"]],
      ["Safety and records", ["privacy"]],
    ]);
  });

  it("turns validator text into plain words", () => {
    expect(plainError("Input should be less than or equal to 2")).toBe("Use 2 or less.");
    expect(plainError("Input should be greater than or equal to 64")).toBe("Use 64 or more.");
    expect(plainError("Input should be a valid integer, got a number with a fractional part")).toBe(
      "Use a whole number.",
    );
    expect(plainError("Value error, prompt template must contain {sources}")).toBe(
      "Prompt template must contain {sources}",
    );
    expect(plainError("'qwen' is not available at ollama")).toBe("'qwen' is not available at ollama");
  });

  it("offers one honest primary action", () => {
    const none = new Set<string>();
    expect(primaryAction({ problems: 2, unsaved: none, indexStages: index, status: "ready" })).toMatchObject({
      kind: "blocked",
      reason: "Fix 2 problems first",
    });
    expect(
      primaryAction({ problems: 0, unsaved: new Set(["retrieval.candidates"]), indexStages: index, status: "ready" })
        .label,
    ).toBe("Save changes");
    expect(
      primaryAction({ problems: 0, unsaved: new Set(["source.path"]), indexStages: index, status: "ready" }).label,
    ).toBe("Save and rebuild");
    expect(
      primaryAction({ problems: 0, unsaved: new Set(["retrieval.candidates"]), indexStages: index, status: "draft" })
        .label,
    ).toBe("Save and build");
    expect(primaryAction({ problems: 0, unsaved: none, indexStages: index, status: "draft" }).label).toBe("Build");
    expect(primaryAction({ problems: 0, unsaved: none, indexStages: index, status: "stale" }).label).toBe("Rebuild");
    expect(primaryAction({ problems: 0, unsaved: none, indexStages: index, status: "ready" }).kind).toBe("none");
  });

  it("flags out-of-range numbers before the server does", () => {
    const field = {
      stage: "chunking",
      key: "chunk_size",
      label: "Chunk size",
      help: "",
      tier: "essential" as const,
      kind: "int" as const,
      options: [],
      min: 64,
      max: 4096,
      defaultValue: 512,
    };
    expect(rangeProblem(field, 512)).toBeNull();
    expect(rangeProblem(field, 10)).toBe("Use 64 or more.");
    expect(rangeProblem(field, 9000)).toBe("Use 4096 or less.");
    expect(rangeProblem(field, 1.5)).toBe("Use a whole number.");
    expect(rangeProblem(field, Number.NaN)).toBe("Enter a number.");
    expect(rangeProblem({ ...field, kind: "text" }, "x")).toBeNull();
  });
});

describe("home and sidebar", () => {
  const kb = (id: string, status: KbStatus, last_job: KnowledgeBase["last_job"] = null) =>
    ({
      id,
      name: "Handbook",
      status,
      created_at: 0,
      active_version: 1,
      built_version: null,
      documents: 0,
      chunks: 0,
      active_job: null,
      last_job,
    }) as KnowledgeBase;

  it("opens the chatbot used last, or the first one", () => {
    const list = [kb("a", "ready"), kb("b", "draft")];
    expect(openingChatbot(list, "b")?.id).toBe("b");
    expect(openingChatbot(list, "gone")?.id).toBe("a");
    expect(openingChatbot([], null)).toBeUndefined();
  });

  it("keeps a finished build lit until it has been seen", () => {
    const done = kb("a", "ready", { id: "job_2", status: "completed" });
    expect(unseenBuild(done, "job_1")).toBe(true);
    expect(unseenBuild(done, "job_2")).toBe(false);
    expect(unseenBuild(kb("a", "building", { id: "job_3", status: "running" }), null)).toBe(false);
    expect(unseenBuild(kb("a", "draft"), null)).toBe(false);
  });
});

describe("quick look", () => {
  it("reads markdown tables in a passage as tables", () => {
    const text = "Greens and browns.\n| Material | Type |\n| --- | --- |\n| Peelings | Green |\nTurn weekly.";
    expect(passageParts(text)).toEqual([
      { kind: "text", text: "Greens and browns." },
      {
        kind: "table",
        rows: [
          ["Material", "Type"],
          ["Peelings", "Green"],
        ],
      },
      { kind: "text", text: "Turn weekly." },
    ]);
    expect(passageParts("Just prose.")).toEqual([{ kind: "text", text: "Just prose." }]);
    const glued = "Add greens. | Material | Type | Notes |\n| --- | --- | --- |\n| Peelings | Green | Chop |";
    expect(passageParts(glued)).toEqual([
      { kind: "text", text: "Add greens." },
      {
        kind: "table",
        rows: [
          ["Material", "Type", "Notes"],
          ["Peelings", "Green", "Chop"],
        ],
      },
    ]);
  });

  it("grows from the centre of what opened it, measured inside its own box", () => {
    const chip = { getBoundingClientRect: () => ({ left: 100, top: 300, width: 40, height: 20 }) } as Element;
    const centre = centreOf(chip);
    expect(centre).toEqual({ x: 120, y: 310 });
    expect(originWithin(centre, { left: 400, top: 200 })).toBe("-280px 110px");
  });
});

describe("chat transcript", () => {
  it("restores the saved conversation and keeps its session id", () => {
    sessionStorage.setItem(
      "archie.chat.kb_x",
      JSON.stringify({
        sessionId: "abc",
        messages: [{ id: "1", role: "assistant", text: "Hi", sources: [], citations: [], streaming: true }],
      }),
    );
    const restored = loadConversation("kb_x");
    expect(restored.sessionId).toBe("abc");
    expect(restored.messages[0]?.streaming).toBe(false);
    expect(restored.messages[0]?.error).toBeUndefined();
    sessionStorage.setItem(
      "archie.chat.kb_cut",
      JSON.stringify({
        sessionId: "c",
        messages: [{ id: "2", role: "assistant", text: "", sources: [], citations: [], streaming: true }],
      }),
    );
    expect(loadConversation("kb_cut").messages[0]?.error).toBe("the page closed before it finished");
    expect(loadConversation("kb_none").messages).toEqual([]);
    sessionStorage.setItem("archie.chat.kb_bad", "{not json");
    expect(loadConversation("kb_bad").sessionId).toMatch(/[0-9a-f-]{36}/);
  });
});

describe("documents", () => {
  it("turns a parse-quality score into plain words", () => {
    expect(qualityVerdict(0.95)).toEqual({ tone: "ok", text: "Read well" });
    expect(qualityVerdict(0.7).text).toBe("Worth a look");
    expect(qualityVerdict(0.2).tone).toBe("bad");
    expect(qualityVerdict(undefined).text).toBe("Not scored");
    expect(TEMPLATES.rows).toBe("Groups of table rows");
  });
});
