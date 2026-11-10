# Archie

Turn a folder of documents into a chatbot that shows its sources. Archie is an agentic RAG framework: point it at a
folder, tune each pipeline stage in a UI, build, ask questions with cited answers, and see exactly what every request
retrieved and decided. (The Python package and CLI are called `ragkit`.)

- **Configurable pipeline, explained.** Source → parsing → chunking → embedding → index → hybrid retrieval → fusion
  (RRF/DBSF) → reranking → context → agent → generation → cache & memory → privacy → telemetry. Each stage shows
  its essential settings with one-line help; advanced settings are one click away; three presets cover most needs.
- **Bounded agent.** Condenses follow-ups, retries weak retrievals with a rewritten or broader search, abstains with
  the closest passages instead of guessing, and checks that answers cite real sources.
- **Reads what you actually have.** PDF, Word, PowerPoint, Excel, CSV, JSON, email, EPUB, OpenDocument, HTML,
  Markdown and text; images through OCR; audio and video transcribed on your own machine, cited by timestamp.
  A fast text reader
  handles simple files; complex ones (text columns, borderless tables, scans, Office files) go to an optional
  layout reader (Docling's layout and table-structure models). Each document is split by its shape: by headings,
  per section for FAQs and clauses, per slide, by table rows, or without the references list for papers.
- **Search that finds the right passage.** Optional build-time enrichment writes context, likely questions and
  keywords for each passage (contextual retrieval); answers get the text around each hit (small-to-big).
- **Inspect and fix ingestion.** A Documents page shows how every file was read and split, flags the ones likely
  misread, and lets you edit or exclude passages; edits are searchable at once and survive rebuilds.
- **Share it.** One switch gives a chatbot a link anyone can ask, and a one-line snippet puts it on any
  website as a chat button. Visitors can only ask questions, rate-limited; HTTPS comes with one profile.
- **Builds as jobs.** Incremental, resumable, cancellable, with a live stage timeline and zero-downtime index swaps.
- **Telemetry that explains.** Per request: candidate funnel, agent decisions, latency waterfall, top chunks with
  dense and keyword ranks. Per build: stage counts, failures and a corpus profile. Correlated logs, Prometheus
  metrics, SLOs with burn-rate alerts, and a Health page that says it in plain words.
- **Measured, not guessed.** Golden-set evaluation (hit@k, MRR, passage-level hits, abstain rates) per config
  version, compared side by side, plus an ingestion benchmark (`bench/`) on deliberately hard documents.
- **Runs anywhere.** Plain local code, split Docker services, or one all-in-one container. Ollama or any
  OpenAI-compatible model server. Embedded or server Qdrant. Several API processes on one machine, and
  `rag backup` / `rag restore` for one-file backups.

## Quick start

```bash
./scripts/rag.sh setup     # Windows: .\scripts\rag.ps1 setup
./scripts/rag.sh up        # Docker → http://localhost:8080   (or: rag dev → http://localhost:5173)
```

Then **Continue** → name your chatbot → choose its documents (the sample folder is filled in) → pick an answer
style → **Create**. Archie builds it and opens Ask. Requirements, every command, containers and troubleshooting:
[setup.md](setup.md).

## Documentation

| Document | Contents |
|---|---|
| [setup.md](setup.md) | Running locally or in Docker, the `rag` commands, configuration, security defaults |
| [architecture.md](architecture.md) | How it works, the agent, telemetry, API, measured performance, design deviations |
| [plan.md](plan.md) | Goals, decisions, milestones and status |
| [docs/extending.md](docs/extending.md) | Adding a data source, file format, model provider or PII detector |

## Development

```bash
cd backend && .venv/bin/python -m pytest          # 227 tests; ruff + mypy configured in pyproject.toml
cd frontend && npm test && npm run lint && npm run typecheck
```

CI runs both on Linux, Windows and macOS, builds every image and smoke-tests the all-in-one container.

## License

MIT, see [LICENSE](LICENSE). The sample corpus in `data/sources/sample` is original and free to use.
