# Archie

Turn a folder of documents into a chatbot that shows its sources. Archie is an agentic RAG framework: point it at a
folder, tune each pipeline stage in a UI, build, ask questions with cited answers, and see exactly what every request
retrieved and decided. (The Python package and CLI are called `ragkit`.)

- **Configurable pipeline, explained.** Source → parsing → chunking → embedding → index → hybrid retrieval → fusion
  (RRF/DBSF) → reranking → context → agent → generation → cache & memory → privacy → telemetry. Each stage shows
  its essential settings with one-line help; advanced settings are one click away; three presets cover most needs.
- **Bounded agent.** Condenses follow-ups, retries weak retrievals with a rewritten or broader search, abstains with
  the closest passages instead of guessing, and checks that answers cite real sources.
- **Builds as jobs.** Incremental, resumable, cancellable, with a live stage timeline and zero-downtime index swaps.
- **Telemetry that explains.** Per request: candidate funnel, agent decisions, latency waterfall, top chunks with
  dense and keyword ranks. Per build: stage counts, failures and a corpus profile. Correlated logs, Prometheus
  metrics, SLOs with burn-rate alerts, and a Health page that says it in plain words.
- **Measured, not guessed.** Golden-set evaluation (hit@k, MRR, abstain rates) per config version, compared side by
  side.
- **Runs anywhere.** Plain local code, split Docker services, or one all-in-one container. Ollama or any
  OpenAI-compatible model server. Embedded or server Qdrant.

## Quick start

```bash
./scripts/rag.sh setup     # Windows: .\scripts\rag.ps1 setup
./scripts/rag.sh up        # Docker → http://localhost:8080   (or: rag dev → http://localhost:5173)
```

Then **Create your first chatbot** → name it → keep the sample folder → **Create and build** → **Ask your first
question**. Requirements, every command,
containers and troubleshooting: [setup.md](setup.md).

## Documentation

| Document | Contents |
|---|---|
| [setup.md](setup.md) | Running locally or in Docker, the `rag` commands, configuration, security defaults |
| [architecture.md](architecture.md) | How it works, the agent, telemetry, API, measured performance, design deviations |
| [plan.md](plan.md) | Goals, decisions, milestones and status |
| [docs/extending.md](docs/extending.md) | Adding a data source, file format, model provider or PII detector |

## Development

```bash
cd backend && .venv/bin/python -m pytest          # 178 tests; ruff + mypy configured in pyproject.toml
cd frontend && npm test && npm run lint && npm run typecheck
```

CI runs both on Linux, Windows and macOS, builds every image and smoke-tests the all-in-one container.

## License

MIT, see [LICENSE](LICENSE). The sample corpus in `data/sources/sample` is original and free to use.
