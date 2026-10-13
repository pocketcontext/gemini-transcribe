# Workflows

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

Resume reuses completed stages; it must not be presented as retranscribing or verifying speech. A new transcription requires the original file and creates a new extraction. Automatic generation retries are disabled; an explicit retry can incur API charges. If a retry fails, report the safe stage/status and preserve the bundle rather than looping indefinitely.

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
