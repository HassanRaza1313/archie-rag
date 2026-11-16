# Extending ragkit

Extension points are plain registries (dicts) next to the code they extend. Add an entry, add its settings to the
stage model in `config.py` if it needs any, and the setup UI picks them up from the schema automatically.

## A data source

A service with an account (an API, a bucket, a database) is a connector: add a module to
`backend/src/ragkit/connectors/` that registers an `AccountKind` (form fields, a live `check`) and a `Connector`
whose `fetch(account, location, cache, limit)` returns `cache.keep(uri, version, suffix, load, fields)` for each item.
The cache downloads only changed versions and removes items that are gone; `connectors/notion.py` is a short
example. Add the type to `SourceConfig.type`, a note in `notes.py`, the name in `CONNECTORS` in `SetupPage.tsx` and
`SERVICES` in `NewChatbotPage.tsx`, and a test with the service's documented responses (`tests/test_connectors.py`).

For local sources, `backend/src/ragkit/sources.py`: a source is a function that yields `DocumentRef(uri, path, size)`.

```python
def discover_mail_archive(root: Path, config: SourceConfig) -> Iterator[DocumentRef]:
    # Unpack an archive kept inside the data roots into `root`, then reuse the folder walker.
    unpack(config.path, root)
    yield from discover_folder(root, config)

SOURCES["mail_archive"] = discover_mail_archive
```

Add `"mail_archive"` to `SourceConfig.type`'s `Literal`, and any options as fields on `SourceConfig` with
`option(...)` so they appear in the UI with help text. `uri` must be stable across runs: incremental builds and
reconciliation key on it. The website source (`web.py`) is the worked example of a remote source: it fetches into
`state_dir/web/<chatbot>`, uses the page address as `uri` (citations then show it), drops cached files that are
gone, and is listed in `SOURCE_FIELDS_FOR` in `SetupPage.tsx` so Settings shows only its fields. Treat remote
content as untrusted: `jobs.py` screens it with `injection.py`, and any network access needs the same
private-address guard as `web.check_address`. `source.sync_hours` schedules every source type without extra code.

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
`list_models`, `ping`, `is_remote`), add a kind to `connections.KINDS` and build it in `ProviderRegistry.build`.
People then add it in Preferences > Models. Most hosted and self-hosted servers already speak the OpenAI API, so the
"Other server" kind covers them without code.

## A setting

Add the field with `option(...)` in `config.py`, then its notes in `notes.py`: one per choice, `true`/`false` for
a switch, `up`/`down` for a number, `*` for text. `tests/test_notes.py` fails until every setting has them.

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
