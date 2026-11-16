"""What changing a setting does, in two short sentences: the gain, then the cost.

Keys are ``stage.field``. Choices and switches are noted per value (``"true"``/``"false"`` for switches), numbers
per direction (``"up"``/``"down"``), and free-text fields with ``"*"``. The UI shows the matching note next to an
edited setting, followed by when the change applies (the next question, or after a rebuild).
"""

from __future__ import annotations

NOTES: dict[str, dict[str, str]] = {
    # What it reads
    "source.type": {
        "local_folder": "Reads a folder on the server and picks up changes at every rebuild.",
        "uploads": "Uses files uploaded in the app. Best for a handful of documents; nothing outside Archie changes.",
        "website": "Reads a website's pages and linked documents. Web pages are untrusted: sentences that read like "
        "instructions to an AI are removed, and you can restore them on the Documents page.",
    },
    "source.url": {"*": "The next build reads this site. Pages outside the start page's folder are not followed."},
    "source.max_pages": {
        "up": "Reads more of the site: builds take longer.",
        "down": "Faster builds; pages past the limit are left out.",
    },
    "source.sync_hours": {
        "up": "Checks less often: changes on the source take longer to show up.",
        "down": "Checks more often: answers stay current, at the cost of more reading.",
    },
    "source.recursive": {
        "true": "Includes every subfolder.",
        "false": "Only the top folder: faster, but documents in subfolders are left out.",
    },
    "parsing.engine": {
        "auto": "Uses the fast reader for simple files and the layout reader for columns, tables and scans.",
        "fast": "Builds quickest. Multi-column pages, borderless tables and Office files may be misread.",
        "layout": "Reads every file's layout: best tables and reading order. Builds several times slower.",
    },
    "parsing.ocr": {
        "auto": "Reads text from scanned pages only when a page has no text layer.",
        "off": "Faster builds. Scanned pages and images contribute nothing.",
        "force": "Reads every page as an image: fixes broken text layers, but builds are much slower.",
    },
    "parsing.tables": {
        "markdown": "Keeps tables as rows and columns, so figures stay next to their labels.",
        "off": "Tables become plain text: smaller passages, but numbers lose their row and column.",
    },
    "parsing.strip_headers_footers": {
        "true": "Drops repeated page headers and footers, so they don't crowd search results.",
        "false": "Keeps them: useful only when headers carry real information, like a document code.",
    },
    "parsing.speech_model": {
        "tiny": "Transcribes fastest. More mistakes, especially with accents and jargon.",
        "base": "Fast, with fewer mistakes than tiny.",
        "small": "A good balance of speed and accuracy for meetings and calls.",
        "medium": "More accurate on difficult audio. About twice as slow as small.",
        "large-v3": "Most accurate. Slow without a GPU; best for recordings that matter.",
    },
    "chunking.strategy": {
        "auto": "Picks a way to split each document by its shape (headings, slides, tables, clauses).",
        "structure": "Splits by headings: passages follow the document's own sections.",
        "sections": "One passage per section: suits FAQs, policies and contracts with short clauses.",
        "slides": "One passage per slide.",
        "rows": "Splits tables by rows, repeating the header so every passage stays readable.",
        "fixed": "Equal-sized pieces regardless of structure: predictable, but can cut through a sentence.",
    },
    "chunking.chunk_size": {
        "up": "Passages carry more surrounding text, good for long explanations. Search is less precise.",
        "down": "More precise matches for short facts. Answers may miss context around them.",
    },
    "chunking.overlap_pct": {
        "up": "Less chance of a fact being split between two passages. The index grows.",
        "down": "A smaller index, at more risk of splitting a fact across passages.",
    },
    "chunking.section_headers": {
        "true": "Each passage carries its section title, which helps search and citations.",
        "false": "Passages are only their own text: slightly smaller, harder to place.",
    },
    "enrichment.context": {
        "true": "A model writes where each passage sits in its document: finds passages that don't repeat the topic. "
        "Costs a model call per passage at build time.",
        "false": "Faster, cheaper builds. Passages that rely on earlier pages for context are harder to find.",
    },
    "enrichment.questions": {
        "true": "Adds questions each passage answers, so different wording still finds it. A model call per passage.",
        "false": "Faster, cheaper builds.",
    },
    "enrichment.keywords": {
        "true": "Adds keywords and synonyms, which helps keyword search. A model call per passage.",
        "false": "Faster, cheaper builds.",
    },
    "enrichment.provider": {"*": "Results already written are reused; only new passages use the new provider."},
    "enrichment.model": {"*": "A stronger model writes better context but makes builds slower and costlier."},
    "enrichment.document_tokens": {
        "up": "The model sees more of the document when writing context. Slower and costlier.",
        "down": "Cheaper. Context for passages far into a long document gets thinner.",
    },
    "enrichment.concurrency": {
        "up": "Enriches more passages at once: faster builds if the model server keeps up.",
        "down": "Gentler on a busy or rate-limited model server.",
    },
    "embedding.provider": {"*": "A different search model changes every vector: the whole index is rebuilt."},
    "embedding.model": {"*": "A different search model changes every vector: the whole index is rebuilt."},
    "embedding.batch_size": {
        "up": "Fewer, larger requests: faster builds on a strong server.",
        "down": "Smaller requests: steadier on a slow or memory-limited server.",
    },
    "index.hnsw_m": {
        "up": "Better recall on large collections. Uses more memory and builds slower.",
        "down": "Less memory, at some loss of recall on large collections.",
    },
    "index.hnsw_ef_construct": {
        "up": "A higher-quality index; builds slower.",
        "down": "Faster builds, slightly lower search quality.",
    },
    "index.quantization": {
        "true": "About four times less memory for vectors, with a small loss of precision.",
        "false": "Full precision, more memory.",
    },
    # How it answers
    "retrieval.mode": {
        "hybrid": "Combines meaning and exact words: the most robust choice.",
        "dense": "Matches meaning only. Can miss exact codes, names and numbers.",
        "keyword": "Matches exact words only. Misses paraphrases; no search model needed at question time.",
    },
    "retrieval.candidates": {
        "up": "Finds more possible passages before ranking: better recall, slower answers.",
        "down": "Faster answers. The right passage may not make the shortlist.",
    },
    "retrieval.search_ef": {
        "up": "A more exhaustive vector search: better recall, slower.",
        "down": "Faster search with slightly lower recall.",
    },
    "retrieval.groups": {
        "*": "Answers can draw on those chatbots' documents, read their own way. People who can use this chatbot "
        "can read them through it."
    },
    "retrieval.routing": {
        "auto": "Searches only the chatbots a question is likely about: faster, less noise. Unsure questions "
        "search several.",
        "all": "Every chatbot is searched for every question: nothing is missed, but answers are slower.",
    },
    "fusion.method": {
        "rrf": "Merges searches by rank: steady and needs no tuning.",
        "dbsf": "Blends scores instead of ranks; better when one search is clearly stronger. "
        "Can over-weight a single noisy match.",
    },
    "fusion.rrf_k": {
        "up": "Flattens rank differences: lower-ranked results count for more.",
        "down": "Top results dominate the merged list.",
    },
    "rerank.enabled": {
        "true": "A second model re-reads the shortlist and puts the best passages first: fewer wrong answers.",
        "false": "Faster answers, ranked by search scores alone. Expect more off-target passages.",
    },
    "rerank.model": {"*": "A larger reranker ranks better but adds time to every question."},
    "rerank.top_n": {
        "up": "The answer sees more passages: better for broad questions, slower and costlier.",
        "down": "Focused, faster answers. Questions that need several sources may come up short.",
    },
    "rerank.min_relevance": {
        "up": "Drops weak passages, so answers rest on strong evidence. More questions get 'I don't know'.",
        "down": "Keeps more passages: fewer abstentions, more risk of answers built on weak matches.",
    },
    "context.token_budget": {
        "up": "Room for more source text: fuller answers, slower and costlier per question.",
        "down": "Cheaper, faster answers from less text.",
    },
    "context.dedup": {
        "true": "Removes repeated passages, leaving room for different sources.",
        "false": "Keeps duplicates: only useful when copies differ in ways that matter.",
    },
    "context.near_duplicate_threshold": {
        "up": "Only near-identical passages count as duplicates.",
        "down": "Looser matching removes more similar passages, possibly ones that differ in a detail.",
    },
    "context.compression": {
        "off": "Sends whole passages to the model.",
        "extractive": "Keeps only the sentences that match the question: cheaper, but may drop needed context.",
    },
    "context.expand": {
        "off": "Only the matching passages: fastest, but answers may lack surrounding detail.",
        "neighbors": "Adds the text just before and after each hit: fuller answers at modest cost.",
        "section": "Adds the whole section around each hit: most complete, uses the most of the budget.",
    },
    "agent.max_reformulations": {
        "up": "Retries more rephrased searches before giving up: answers more questions, slower when it misses.",
        "down": "Answers or abstains sooner.",
    },
    "agent.confidence_threshold": {
        "up": "Abstains unless search is confident: fewer wrong answers, more 'I don't know'.",
        "down": "Answers more often, including from weaker matches.",
    },
    "agent.verification": {
        "claims": "Each sentence is checked against the passage it cites, on this machine: unsupported sentences are "
        "marked. Downloads a 90 MB model once and adds a fraction of a second.",
        "citations": "Checks that every answer cites real sources. Fast and free.",
        "llm": "A model also checks the answer against its sources: catches unsupported claims, adds a model call.",
        "off": "No checks: fastest, and unsupported answers go out unflagged.",
    },
    "agent.on_verify_fail": {
        "flag": "Shows the answer with a warning when the check fails.",
        "abstain": "Replaces a failed answer with 'I don't know': safest for customer-facing chatbots.",
    },
    "agent.abstain_message": {"*": "What people read when Archie can't answer from the documents."},
    "generation.provider": {"*": "Answers change in style and quality; check a few questions after switching."},
    "generation.model": {"*": "Larger models answer better and follow citation rules more closely; they are slower."},
    "generation.temperature": {
        "up": "More varied wording. More risk of drifting from the sources. Claude ignores this setting.",
        "down": "More consistent, literal answers.",
    },
    "generation.system_prompt": {
        "*": "Shapes tone and rules for every answer. Keep the instruction to use only sources."
    },
    "generation.prompt_template": {
        "*": "Keep {sources} and {question}, and the citation instruction, or answers stop citing."
    },
    "generation.max_tokens": {
        "up": "Allows longer answers; slower and costlier.",
        "down": "Shorter answers; long explanations may be cut off.",
    },
    "generation.context_window": {
        "up": "Lets the model read more at once, if the model supports it. Uses more memory.",
        "down": "Less memory on the model server; long contexts get cut.",
    },
    "generation.thinking": {
        "true": "The model reasons before answering: better on hard questions, noticeably slower.",
        "false": "Faster answers.",
    },
    # Safety and records
    "cache.enabled": {
        "true": "Repeated questions are answered instantly from earlier answers.",
        "false": "Every question is answered fresh: slower and costlier, always current.",
    },
    "cache.semantic": {
        "true": "Also reuses answers to differently worded questions with the same meaning.",
        "false": "Reuses answers only for the exact same question.",
    },
    "cache.semantic_threshold": {
        "up": "Reuses an answer only for very close paraphrases: safer.",
        "down": "Reuses answers more often, with more risk of answering a slightly different question.",
    },
    "cache.capacity": {
        "up": "Keeps more answers ready.",
        "down": "Keeps only the most asked questions.",
    },
    "cache.history": {
        "up": "Judges which questions are popular over a longer stretch.",
        "down": "Adapts faster to what people ask now.",
    },
    "cache.memory": {
        "true": "Follow-up questions like 'and for managers?' understand the conversation.",
        "false": "Every question stands alone; follow-ups must repeat the subject.",
    },
    "cache.memory_turns": {
        "up": "Remembers more of the conversation; follow-ups cost a little more.",
        "down": "Remembers only the latest turns.",
    },
    "privacy.redact_prompt": {
        "auto": "Hides personal data from sources sent to a model on another company's servers; local models see all.",
        "always": "Hides personal data from every model, local ones included.",
        "never": "Models see personal data as written. Only for models you run yourself.",
    },
    "privacy.redact_at_ingest": {
        "true": "Personal data never enters the index: answers can't quote it.",
        "false": "The index keeps it; redaction happens only when text goes to a model.",
    },
    "telemetry.level": {
        "minimal": "Keeps no question traces: most private, but Activity can't explain answers.",
        "standard": "Keeps what each answer searched and decided, with personal data hidden.",
        "verbose": "Also keeps the passages and prompts: best for debugging, stores the most.",
    },
    "telemetry.retention_days": {
        "up": "Activity reaches further back; more stored data.",
        "down": "Less stored data; older questions disappear sooner.",
    },
}
