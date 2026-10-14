# Experimental long-decode pipeline

Use the public CLI with the locked SDK; no separate environment is needed:

```sh
gemini-transcribe recording.ogg --pipeline long-decode --json
gemini-transcribe --resume /path/to/manifest.json --json
```

The standard pipeline remains the default (`gemini-3.8-flash`, Interactions).
Long decode uses `gemini-4-argon`, GenerateContent, low thinking and explicit
manual continuation. Unsupported model/pipeline combinations fail before upload.
There is no automatic fallback or generation retry.

## Limits and completion

Long-decode defaults: `--max-output-tokens 131072`, `--max-hops 16`,
`--deadline-seconds 1800`, `--request-timeout-seconds 1200`. The token budget
includes visible output and thinking. The deadline applies separately to each
generation stage, not to upload/readiness or the complete three-stage run.
Request timeouts cannot exceed 1,200 seconds. Assembled JSON cannot exceed
8 MiB. These are local execution policies, not claims about a provider-wide
total deadline. Final serialized JSON artifacts are checked against the same 8 MiB limit before
any stage files are written or the stage is marked complete.

A POSIX main-thread process alarm bounds in-flight requests. An existing active
alarm is rejected; the prior handler is restored. Each continuation resends the
original input/schema/budget and only the latest token. Thinking-only hops are
allowed. Only STOP proceeds to JSON and bundle validation. MAX_TOKENS is treated
as incomplete even if resumable: the CLI never raises the authorized budget.
Missing/repeated tokens, exhausted limits and invalid output fail safely.
Uploaded audio deletion is attempted in all cases.

## Recovery and provenance

Resume infers the pipeline, model and configured limits from the manifest.
Explicit conflicting options are rejected. Resume currently requires the recorded
SDK version as well as the generation settings; offline export has no such restriction. Completed stages are retained;
failed enrichment starts a new charged stage attempt. Continuation tokens and
partial provider text are never saved. A failed transcription needs a new run.
Legacy standard bundles without generation metadata remain resumable. Unversioned
local prototype manifests support offline export but cannot resume with this CLI.

`configuration.generation` has schema version `gemini-transcribe.generation/v1`,
pipeline, API, SDK version, configured `settings`, and `stages`. Each stage has
an append-only array of attempt records: status, continuation_count, and hops.
Safe hop records include reported token counts, elapsed time and finish reason;
missing counts mean unavailable, never zero. No prompts, tokens, file URIs or
provider text appear in telemetry. Failed attempts remain available after resume.
The five extraction fields and eight artifact roles are unchanged.

Both APIs avoid stored interaction state. `extraction.store: false` is the
policy flag; GenerateContent has no store request parameter. Uploaded-file
retention, deletion and other provider data policies remain separate.

Tests use synthetic responses and HTTP transports. Live tests need authorized
recordings and incur charges. Multiple observed responses establish continuation;
a large configured budget alone does not. Valid hashes and JSON do not establish
speech accuracy, speaker identity, timestamp accuracy or complete coverage.

References:
- [GenerateContent long decode](https://ai.google.dev/gemini-api/docs/generate-content/long-decode)
- [Audio input](https://ai.google.dev/gemini-api/docs/audio)
- [Structured output](https://ai.google.dev/gemini-api/docs/structured-output)
