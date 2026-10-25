import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Trace } from "../lib/api";
import { Funnel, TraceView } from "./TracePanel";
import { GalleryLabel, StatusBadge, Switch, statusText, width } from "./ui";

afterEach(cleanup);

const trace: Trace = {
  id: "4bf92f3577b34da6a3ce929d0e0e4736",
  kb_id: "kb_1",
  config_version: 3,
  session_id: "s",
  created_at: 1,
  outcome: "answered",
  total_ms: 2400,
  ttft_ms: 600,
  cache_hit: 0,
  confidence: 0.58,
  retries: 1,
  verify: "pass",
  query: "What ratio of browns to greens?",
  data: {
    spans: [
      { name: "embed", start_ms: 0, duration_ms: 40, attributes: {} },
      { name: "retrieve", start_ms: 40, duration_ms: 200, attributes: {} },
      { name: "generate", start_ms: 240, duration_ms: 2100, attributes: {} },
    ],
    decisions: ["searched the documents", "retry 1: rewrite (confidence 0.21)"],
    funnel: { dense: 30, keyword: 28, fused: 42, relevant: 9, deduplicated: 7, context: 5, context_tokens: 1840 },
    candidates: [
      {
        rank: 1,
        chunk_id: "c1",
        file: "guide.md",
        section: "Greens and browns",
        page: 2,
        dense_rank: 1,
        keyword_rank: 3,
        fused: 0.03,
        relevance: 0.91,
      },
    ],
    tokens: { prompt: 1200, completion: 80 },
    error: null,
    prompt: null,
  },
};

describe("TraceView", () => {
  it("explains the request in plain words", () => {
    render(<TraceView trace={trace} />);
    expect(screen.getByText("What ratio of browns to greens?")).toBeTruthy();
    expect(screen.getByText("Answered")).toBeTruthy();
    expect(screen.getByText("Every claim cites a source")).toBeTruthy();
    expect(screen.getByText("retry 1: rewrite (confidence 0.21)")).toBeTruthy();
    expect(screen.getByText("Write the answer")).toBeTruthy();
    expect(screen.getByText("2.10 s")).toBeTruthy();
    expect(screen.getByText("58%")).toBeTruthy();
    expect(screen.getByText("guide.md")).toBeTruthy();
    expect(screen.getByText("#1 · 91% relevant")).toBeTruthy();
    expect(screen.getByText(`Request ${trace.id}`)).toBeTruthy();
  });

  it("names the failing step", () => {
    render(
      <TraceView
        trace={{
          ...trace,
          outcome: "error",
          verify: null,
          data: { ...trace.data, error: { stage: "generate", type: "ProviderError", message: "model crashed" } },
        }}
      />,
    );
    expect(screen.getByText("Write the answer: model crashed")).toBeTruthy();
    expect(screen.getByText("Error")).toBeTruthy();
  });
});

describe("Funnel", () => {
  it("renders steps in pipeline order with proportional bars", () => {
    const { container } = render(<Funnel funnel={{ context: 5, dense: 20, fused: 40 }} />);
    const labels = [...container.querySelectorAll("span.text-slate")].map((el) => el.textContent);
    expect(labels).toEqual(["Found by meaning", "Combined", "Given to the AI"]);
    const bars = [...container.querySelectorAll<HTMLDivElement>(".bg-accent")].map((el) => el.style.width);
    expect(bars).toEqual(["50%", "100%", "12.5%"]);
  });
});

describe("primitives", () => {
  it("switch toggles and exposes state", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Reranking" />);
    const control = screen.getByRole("switch", { name: "Reranking" });
    expect(control.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(control);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("status badges use plain words", () => {
    render(<StatusBadge status="completed_with_errors" />);
    expect(screen.getByText("Finished with problems")).toBeTruthy();
    expect(statusText("stale")).toBe("Needs rebuild");
    expect(statusText("draft")).toBe("Not built yet");
    expect(statusText("something_new")).toBe("something new");
    expect(statusText(null)).toBe("");
  });

  it("gallery label shows mark, title and provenance", () => {
    render(
      <GalleryLabel mark="S1" title="guide.md">
        Greens and browns · p. 2
      </GalleryLabel>,
    );
    expect(screen.getByText("S1")).toBeTruthy();
    expect(screen.getByText("guide.md")).toBeTruthy();
    expect(screen.getByText("Greens and browns · p. 2")).toBeTruthy();
  });

  it("controls fill their container unless a width is given", () => {
    expect(width(undefined)).toBe("w-full");
    expect(width("font-mono")).toBe("w-full");
    expect(width("w-56")).toBe("");
    expect(width("max-w-md font-mono")).toBe("w-full");
    expect(width("font-mono md:w-40")).toBe("");
  });
});
