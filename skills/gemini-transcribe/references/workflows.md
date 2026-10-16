# Workflows

Follow the [paid API retry policy](retries.md): up to three agent-initiated
retries per authorized recording workflow require no renewed approval.

## Generate and review

```sh
gemini-transcribe /path/to/recording.m4a --language Italian --translation-language English --output-dir ./evidence --json
```

The language hint does not translate the original transcript. Full runs include all supported analysis categories and translation. No detected decisions, questions or other findings is valid: their arrays can be empty. `--transcript-only` skips semantic analysis and translation but retains audio annotations, uncertainty and tone; do not describe that as complete enrichment.

Stdout with `--json` is one receipt. Progress and safe errors use stderr. Exit codes are 0 complete, 1 failure, 2 usage/configuration error, 3 partial. Follow `manifest_path` from the receipt rather than guessing the new run directory. Preserve successful transcription after an enrichment failure.

To retry incomplete enrichment, use the existing manifest:

```sh
gemini-transcribe --resume /path/to/run/manifest.json --json
```

Resume reuses completed stages; it must not be presented as retranscribing or verifying speech. A new transcription requires the original file and creates a new extraction. CLI/SDK generation retries are disabled. The agent may explicitly retry under the bounded retry policy without renewed approval; preserve evidence and report safe diagnostics and the remaining retry count.

Compare doubtful names, numbers and speaker changes with the original audio. Generated speaker mappings are evidence-backed proposals; labels are local to this extraction. Timestamps are model estimates. Audio observations describe sound; tone is a subjective interpretation of audible cues. Do not replace the original transcript with a translation or summary.

## WikiContext handoff

Use the separately installed WikiContext skill to authenticate and select the authorized space. Its importer requires the exact original audio, a complete bundle and explicit original storage:

```sh
wikicontext --space companyspace001 ingest /path/to/recording.m4a \
  --transcript-bundle /path/to/run/manifest.json \
  --audio-storage original --version gemini-v1
```

The importer verifies source/artifact hashes and evidence references before writing. It does not invoke Gemini or Groq or convert the audio. Its source limit is 100 MiB, smaller than this CLI's input limit. Keep oversized original recordings intact and report the limit; do not silently substitute a compressed copy for the hashed original.

Use returned source, rendition and passage IDs, including `segment_passages`, `annotation_passages` and `observation_passages`, when reviewing evidence and staging citations. One segment may map to several passages. Preserve analytical artifacts and presentations, but don't count them as independent corroborating sources for speech. Review the actual passages, reconcile existing published knowledge, and stage/publish cited revisions through WikiContext. The import receipt reports extraction, not publication. Corrected evidence uses a new extraction/version; repeated identical imports can resume without duplicating evidence.

## Pipeline selection and usage

`--pipeline standard` is the default. Explicitly select experimental
`--pipeline long-decode` for Argon GenerateContent manual continuation. It defaults
to 131,072 output/thinking tokens, 16 hops, a 1,800-second deadline per stage and
a 1,200-second timeout per request. The CLI performs no implicit retries, fallback or budget increases; agent retries follow the bounded retry policy.
Resume infers saved settings and rejects conflicts; offline export needs no key.

New manifests keep the five-field extraction contract and add optional
`configuration.generation` (`gemini-transcribe.generation/v1`): pipeline, api,
sdk_version, settings and stages. Stage values are arrays of attempts with status,
continuation_count and safe hops. Hop usage contains only actually reported counts;
missing counts are unavailable. Resume appends attempts without erasing failed
request usage. No provider text or continuation tokens are stored in metadata.
WikiContext imports the same bundle; use distinct extraction version labels and
review evidence before explicitly changing a published selection.
