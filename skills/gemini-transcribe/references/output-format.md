# Evidence bundle

Each run writes `manifest.json`, `transcript.json`, `transcript.txt` and `transcript.srt`. Full successful runs also write `analysis.json`, `analysis.md`, `translation.json`, `translation.txt` and `translation.srt`. Artifacts are private local files; keep them outside source control.

The manifest uses `gemini-transcribe.bundle/v2` and binds the recording and each artifact to SHA-256 and byte size. It records duration, provider/model/package/prompt provenance, interaction storage, stage states and overall status. Optional configuration
retains the language hint and translation language for resume. Artifact paths are relative to the manifest directory. It contains no credentials, raw provider responses or absolute source path. Keep the original audio separately and unchanged.

New bundles record `extraction.store=false`. Legacy bundles with true remain resumable and retain that flag for their original transcription; newly retried enrichment always uses false. Resume does not delete previously stored interactions.

`transcript.json` uses `gemini-transcribe.transcript/v1`: original language, ordered speaker-labelled segments (`id`, `start`, `end`, `speaker`, `text`), audio annotations, uncertainty and subjective tone. Times are seconds from the recording start; overlaps are allowed. IDs such as `seg000001` and `ann000001` are assigned locally and stable only within this extraction.

`analysis.json` uses `gemini-transcribe.analysis/v1`: summary, topics, decisions, action items, questions and speaker mappings. Every item has nonempty `segment_ids` pointing to supporting original segments. Owners and deadlines are null unless explicit. Questions distinguish answered, partially answered, unresolved and unclear; mappings distinguish explicit and tentative names.

`translation.json` uses `gemini-transcribe.translation/v1`: target language and exactly one ordered `{segment_id, text}` entry per original segment. Translation preserves the original speaker and time mapping in its presentations.

TXT/SRT/Markdown are readable presentations of the canonical JSON, not additional independent evidence. Sound observations and tone derive from audio processing; semantic analysis and translation derive from the saved transcript. Empty arrays indicate no findings, not a missing stage. A skipped stage is not a successful enrichment.

The maintained repository's [bundle contract](https://github.com/pocketcontext/gemini-transcribe/blob/main/docs/bundle-format.md) defines fields and importer invariants. Use the contract at the launcher's pinned commit when comparing released behavior.

Named exports retain canonical speaker IDs and render `Name (SPEAKER_01)` in
TXT/SRT, including translations. Required manifest `speaker_names` has exactly
`schema_version: "gemini-transcribe.speaker-names/v1"`,
`policy: "explicit-unambiguous/v1"`, and `overrides: [{speaker, name, reason}]`.
Use this object as the JSON file for offline `--export MANIFEST --speaker-overrides
JSON --output-dir DIR`. Reasons record reviewed provenance separately from model
analysis. Names must be unique, control-free and at most 200 characters; reasons
are nonempty, control-free and at most 1000 characters. UNKNOWN cannot be named.
Only unambiguous explicit model mappings resolve automatically; conflicting,
tentative or duplicate names retain generic IDs. New copies preserve canonical
bytes, update presentation hashes, and record `export_provenance` with schema
`gemini-transcribe.export/v1` and the `source_manifest_sha256` parent hash. No API
key or provider call is needed; partial bundles retain their stage state.
