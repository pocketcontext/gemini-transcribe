# Gemini Transcribe

A small Python command-line application that sends an audio file directly to
Gemini and produces a versioned evidence bundle: transcript, speaker labels,
timestamps, audio observations, summary, topics, decisions, actions, questions,
speaker-name mappings and translation. The default
standard pipeline model is `gemini-3.8-flash`; override it with `GEMINI_MODEL` or `--model`.
The opt-in experimental `--pipeline long-decode` uses `gemini-4-argon` and
bounded manual GenerateContent continuation. Both produce the same evidence bundle.

## Setup

Requirements: Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and
FFmpeg (`ffprobe` must be on PATH). The included `devenv.nix` supplies them.

```sh
git clone https://github.com/pocketcontext/gemini-transcribe.git
cd gemini-transcribe
cp .envrc.private.example .envrc.private
chmod 600 .envrc.private
# Edit .envrc.private and replace the API-key placeholder.
```

Create your key in [Google AI Studio](https://aistudio.google.com/api-keys).
With Nix/devenv and direnv installed, run `direnv allow`, then:

```sh
uv sync --locked
gemini-transcribe --help
```

Without direnv, enter `devenv shell` (or use an existing Python/uv installation),
then explicitly load your trusted local configuration:

```sh
source .envrc.private
uv sync --locked
```

`.envrc.private` is ignored by Git; the setup above makes it owner-readable only.
The application reads environment variables; it does not execute this file.
Never put a key in a command-line argument or commit it.

## Transcribe

Inside the devenv shell (including direnv), use the provided command:

```sh
gemini-transcribe audio/interview.mp3
gemini-transcribe audio/interview.wav --language Italian
gemini-transcribe audio/interview.mp3 --output-dir transcripts
gemini-transcribe audio/interview.ogg --pipeline long-decode --json
```

The wrapper uses this project's locked dependencies while preserving your current
directory for relative audio and output paths. Without devenv, run
`uv run --locked gemini-transcribe` from the repository instead.

Supported file extensions: WAV, MP3, AIFF/AIF, AAC, OGG, FLAC and M4A.
Files must be nonempty and smaller than 2 GiB; model duration and output limits
still apply.

Each invocation creates a unique private output directory containing a manifest,
JSON/TXT/SRT transcript, JSON/Markdown analysis and JSON/TXT/SRT translation.
Translation defaults to English; use `--translation-language Italian` to change it.
Use `--transcript-only` to skip semantic analysis and translation while retaining
audio observations. Existing transcripts are preserved.
Labels such as `SPEAKER_01` identify voices within a recording, not real-world
identities. The language option is a hint; the transcript stays in the original
language. The prompt requests verbatim text, consistent labels, uncertain and
inaudible passages marked explicitly, and no invented names or paraphrasing of
the transcript. Summaries and other analyses are separate, segment-linked artifacts.
Unstated owners/deadlines remain null; speaker names can be tentative and tone is
explicitly subjective.

Standard-pipeline Interactions requests explicitly use `store=False`, including resumed enrichment.
This opts out of interaction storage; it does not change Google's other data-use
terms. Version 2 bundles retain their original `extraction.store` flag on resume;
resuming does not delete any previously stored results. Version 1 bundles are
not accepted by this version.
This is separate from the GenerateContent logging setting and uploaded-file deletion.
See Google's [interaction retention documentation](https://ai.google.dev/gemini-api/docs/interactions-overview#data-storage-and-retention).

A private temporary snapshot preserves the exact input bytes used for duration
measurement and upload; allow temporary disk space up to the recording size.
Audio is uploaded to Google's Files API, processed by Gemini, and deletion of
the uploaded file is attempted even after a generation or validation failure.
Deletion failures are reported. No audio is sent by setup or offline tests.
Audio and generated transcripts in the standard `audio/` and `transcripts/`
directories are ignored by Git. Custom output paths outside those directories
are your responsibility.

There are no automatic generation retries: rerunning can incur another API
charge. The first request transcribes audio and records acoustic observations;
separate requests analyze and translate the saved transcript. Transcription is
saved before enrichment. If enrichment fails, the run is
partial and retains its valid transcript. Resume with
`gemini-transcribe --resume /path/to/run/manifest.json --json` to retry incomplete
enrichment without reuploading the audio or regenerating completed stages.
Incomplete or structurally invalid responses are never marked complete. Reversed intervals and backward segment starts preserve the transcript with `timing_review.status: required`; complete extraction does not mean reviewed publication. Timestamp and format validation cannot
establish that the model heard every word or assigned every speaker correctly.
Check a representative recording before relying on the results.

Both pipelines process a whole recording without audio chunking. Long decode may
continue generation across multiple requests. See [pipeline limits and recovery](docs/local-long-decode.md).
Resume infers pipeline/model/limits from the manifest and rejects conflicting flags.
Reported usage and continuation counts are saved per stage attempt; missing counts
are unavailable. No pipeline fallback or automatic budget increase occurs.

This version processes one whole recording as input. It does not split long
audio or reconcile speaker identities across recordings. Very long recordings
can exceed model input/output limits. SRT timestamps are model estimates, not
forced word alignment, and overlapping speech can produce overlapping cues.
See [recording limits and measurement](docs/recording-limits.md) before processing
long recordings. The 2 GiB file cap is not a supported-duration guarantee.

## Portable launcher and skill

The standalone `skills/gemini-transcribe/gemini-transcribe` uv launcher follows the
PocketContext application pattern: a full Git commit pins the package and no
repository checkout or active devenv shell is required. Install uv, Python 3.12+
and FFmpeg, then copy it onto PATH:

```sh
mkdir -p ~/.local/bin
install -m 755 skills/gemini-transcribe/gemini-transcribe ~/.local/bin/gemini-transcribe
```

Include `~/.local/bin` on PATH and export `GEMINI_API_KEY` in your trusted shell
configuration. The launcher does not source `.envrc.private`, change directories,
or embed credentials. Relative paths resolve from your current directory. A
devenv command with the same name may take precedence inside the development shell.

Install the agent skill separately:

```sh
npx skills add pocketcontext/gemini-transcribe --skill gemini-transcribe --agent codex claude-code --yes
```

The launcher executes its pinned release, not uncommitted source changes. Release
new package code first, then update and validate the launcher's full commit pin.
Use `uv run --locked gemini-transcribe` to test the current source checkout.

## Machine output and WikiContext

Use `--json` for one machine-readable receipt on stdout; progress and safe errors
go to stderr. Exit codes: 0 complete, 1 failed, 2 usage/configuration, 3 partial.
The receipt points to `manifest.json`. See the [bundle contract](docs/bundle-format.md)
for hashes, provenance, stage states and stable within-extraction segment IDs.
Invalid generated output retains `code: invalid_output` and adds a content-free
`validation` diagnostic to the JSON receipt and saved manifest, also printed on
stderr. For example:

```json
{"stage":"transcription","code":"invalid_output","validation":{"reason":"unknown_segment_reference","path":"$.tone[0].segment_ids[0]"}}
```

Paths use zero-based array indexes and known schema fields. Reasons distinguish
missing fields, invalid types/enums, timestamp order/bounds, speaker labels and
references, and translation coverage. Invalid JSON reports line/column only.
Unknown fields are reported at their containing object without printing their
names. Unexpected validator exceptions use `invalid_structure` at `$` rather than
exposing their messages. No rejected values, transcript text or raw provider
response are retained in diagnostics. These diagnostics apply to both pipelines
and all generation stages; structural, numeric and duration validation remains strict and no retry is added.
Old failed manifests cannot recover details that were never saved.

Timing anomalies are warnings, not failed extraction: segment starts earlier than the
preceding start and reversed segment/annotation intervals are preserved in original
order with stable IDs. The manifest and JSON receipt include deterministic
`timing_review` metadata with content-free timestamp diagnostics. Review affected
passages against the audio before publication; broad resets require broader review.
TXT marks reversed intervals as timing unavailable and SRT omits those cues (a
recording with no valid cues produces an empty presentation). No timestamps are
silently sorted or repaired. Negative, nonfinite, nonnumeric and out-of-duration
timestamps remain fatal. Complete means extraction completed, not timing approved.

No raw provider response is dumped. Timestamp validity does not establish accuracy.

Errors use safe codes rather than provider payloads. `bundle_in_use` means another
writer holds the run lock; `resume_invalid` means bundle validation failed.
`source_changed` identifies an unstable recording; `source_unavailable` identifies
a filesystem error during source preflight. `export_failed` identifies
a failure saving stage artifacts. `request_timeout` distinguishes a transport
timeout from `request_failed`; `processing_timeout` refers to uploaded-file
readiness. `incomplete_response` and `invalid_output` identify unusable model
responses. Local failures outside these categories retain `local_failure`.

To preserve everything in WikiContext, use its external-transcript importer:

```sh
wikicontext --space companyspace001 ingest recording.m4a \
  --transcript-bundle /path/to/run/manifest.json \
  --audio-storage original --version gemini-v1
```

It preserves the exact recording and artifacts, validates the bundle, and creates
addressable evidence without another provider call. WikiContext's 100 MiB source
limit applies. Review actual returned passages and publish cited pages with the
WikiContext skill; extraction alone is not complete knowledge ingestion. Analytical
artifacts and format variants do not independently corroborate the original audio.

## Billing

API usage follows the billing and quotas of the API-key project. Google AI Pro
is not an unlimited API subscription. Any Google Developer Program credit must
be activated and applied to the appropriate billing account. Check your project's
usage and current model pricing before processing large collections.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv run --locked python tests/launcher.py
uv build
```

The SDK is pinned because its Files and Interactions clients interpret retry
settings differently. The CLI explicitly disables retries on both APIs; preserve
that behavior and its transport test when upgrading.

Tests use synthetic responses and mocked clients; no credentials or network
access are required for the test suite itself.

Official references:

- [Gemini audio input and transcription](https://ai.google.dev/gemini-api/docs/audio)
- [Structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [API billing](https://ai.google.dev/gemini-api/docs/billing)
- [Google Developer Program benefits](https://developers.google.com/program/plans-and-pricing)

## Named speakers and offline export

Successful analysis produces TXT/SRT and translated presentations using supported
names, such as `Alice (SPEAKER_01)`. Canonical JSON keeps stable speaker IDs.
Tentative, conflicting and duplicate names retain generic labels. Review identities
against the recording before relying on them.

Regenerate an existing bundle without an API key or another Gemini request:

```sh
uv run --locked gemini-transcribe --export /path/to/run/manifest.json \
  --output-dir /path/to/named-exports --json
```

For reviewed corrections, add `--speaker-overrides /path/to/names.json` containing:

```json
{
  "schema_version": "gemini-transcribe.speaker-names/v1",
  "policy": "explicit-unambiguous/v1",
  "overrides": [
    {"speaker": "SPEAKER_01", "name": "Alice", "reason": "Reviewed the self-introduction in seg000003."}
  ]
}
```

Each export creates a private bundle copy with updated presentation hashes and
parent-manifest provenance. Original JSON bytes and the input bundle are preserved.
Keep correction files private. Existing overrides are inherited unless a replacement
file is supplied; an empty overrides array clears them. Use a new WikiContext
`--version` when importing corrected evidence and explicitly publish updated pages;
existing passages and citations remain historical evidence.
