# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two modes of use, confirmed:

- **Builders** set up a chatbot over a folder of their own documents, choose settings per pipeline stage, run
  builds, read traces and run evaluations. Many are first-time, non-technical users (operations, HR, support, team
  leads); some are AI/ML engineers who want density and depth.
- **Chatters** only talk to a finished chatbot and need answers they can trust, with sources.

The redesign must work first for a non-technical first-timer without slowing down an expert.

## Product Purpose

Archie turns a folder of documents into a chatbot that answers with citations, and shows exactly why it answered
(or declined to). Success: a first-time user goes from "new chatbot" to a first trustworthy, cited answer without
reading docs, and an expert can see and tune every stage of the pipeline.

## Positioning

A chatbot builder whose answers are transparent: every answer can be opened to show what was searched, what was
kept, what the agent decided and where the time went, and every setting is explained in plain language at the
stage it belongs to. Neighbouring "chat with your docs" tools hide this; RAG frameworks expose it only as code.

## Operating Context

- Local or self-hosted web app (desktop browser first, usable on phones), backed by the user's own documents and
  locally run models (Ollama) or any OpenAI-compatible model server.
- Journey: create chatbot → choose documents → (optional) tune stages → build (a background job with live progress)
  → chat → inspect traces → evaluate and compare settings versions → monitor telemetry, SLOs and alerts.
- Builds can take minutes; chat stays available on the previous index during a rebuild.

## Capabilities and Constraints

- Configurable stages: source, parsing, chunking, embedding, index, retrieval, fusion, reranking, context, agent,
  generation, cache & memory, privacy, telemetry; settings are tiered Essentials / Advanced, with presets
  (Balanced, High accuracy, Low latency). Index stages require a rebuild; answering stages apply live.
- Answers stream token by token, cite sources ([S1]), carry a citation-check verdict, and may abstain with the
  closest passages.
- Every request has a trace (funnel, decisions, latency waterfall, candidates); telemetry, SLOs and alerts exist.
- The UI is driven by the backend's config JSON Schema (labels, help, tiers come from the backend).
- Internal package and CLI keep the name `ragkit`; only the user-facing brand is Archie.

## Brand Commitments

- Name: **Archie**, a friendly persona over your archive ("Ask Archie").
- Must feel like a premium SaaS product: very aesthetic, user friendly and intuitive, especially for first-time
  non-technical users. Not a developer console, not a generic admin template.

## Evidence on Hand

- Sample corpus (`data/sources/sample`: composting guide, bicycle maintenance, remote-team handbook) and a golden
  set (`data/eval/sample-golden.jsonl`). No customers, testimonials, logos, benchmarks or pricing exist; none may be
  invented.

## Product Principles

1. Trust is visible: sources, verdicts and traces are part of the answer, never hidden behind polish.
2. Defaults first, depth on request: a first-timer can build with zero decisions; every setting is explained where
   it lives.
3. Plain language over jargon: technical terms appear only with an explanation.
4. Honest state: what the UI shows matches what the system knows (builds, validation, memory).
5. Fast to answer: nothing decorative slows down chatting or building.

## Accessibility & Inclusion

WCAG 2.2 AA contrast and keyboard operability; screen-reader announcements for streamed answers and job progress;
reduced-motion support.
