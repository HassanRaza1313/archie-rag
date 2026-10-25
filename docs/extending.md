# Extending ragkit

Extension points are plain registries (dicts) next to the code they extend. Add an entry, add its settings to the
stage model in `config.py` if it needs any, and the setup UI picks them up from the schema automatically.

## A data source

`backend/src/ragkit/sources.py`: a source is a function that yields `DocumentRef(uri, path, size)`.

```python
def discover_s3(root: Path, config: SourceConfig) -> Iterator[DocumentRef]:
    # Sync the bucket prefix into `root` (a local cache), then reuse the folder walker.
    sync_bucket(config.path, root)
    yield from discover_folder(root, config)

SOURCES["s3"] = discover_s3
```

Add `"s3"` to `SourceConfig.type`'s `Literal`, and any options (bucket, region) as fields on `SourceConfig` with
`option(...)` so they appear in the UI with help text. `uri` must be stable across runs: incremental builds and
reconciliation key on it.

## A file format

`backend/src/ragkit/parsing.py`: a parser turns a file into `ParsedDocument(title, blocks)`, where blocks are
`heading` (with `level`), `text` and `table` (markdown) with page numbers.

```python
def parse_docx(path: Path, config: ParsingConfig) -> ParsedDocument: ...

PARSERS[".docx"] = parse_docx
```

Add the extension to `SourceConfig.file_types`. Chunking, indexing and citations work unchanged.

## A model provider

`backend/src/ragkit/providers.py`: implement the `ModelProvider` protocol (`embed`, streaming `chat`,
`list_models`, `ping`, `is_remote`) and register it in `ProviderRegistry.__init__`. Add the name to the `provider`
`Literal` in `EmbeddingConfig` and `GenerationConfig`. Most hosted and self-hosted servers already speak the
OpenAI API, so `openai` with `RAG_OPENAI_BASE_URL` covers them without code.

## A PII detector

`backend/src/ragkit/privacy.py`: add `DETECTORS["kind"] = (regex, validator)`; the order of `DETECTORS` is the
priority when matches overlap. Add the kind to `PiiKind` in `config.py`. Prefer a validator (checksum, format
check) over a looser regex: false positives redact real content.

## A chunking strategy

`backend/src/ragkit/chunking.py`: add a function returning `(text, section, page_start, page_end, content_type)`
pieces to `STRATEGIES`, and its name to `ChunkingConfig.strategy`.

## Tests

Every extension point has fakes in `backend/tests/conftest.py` (`FakeProvider`, `FakeReranker`, `FakeRegistry`)
and an embedded-Qdrant `World` helper in `tests/test_build.py`, so a new source, parser or provider can be tested
end to end without any external service.
