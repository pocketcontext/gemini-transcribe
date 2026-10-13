# Gemini Transcribe

A small Python command-line application that sends an audio file directly to
Gemini and produces a transcript with speaker labels and timestamps. The default
model is `gemini-3.8-flash`; override it with `GEMINI_MODEL` or `--model`.

## Setup

Requirements: Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).
The included `devenv.nix` supplies Python 3.12 and uv.

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
```

The wrapper uses this project's locked dependencies while preserving your current
directory for relative audio and output paths. Without devenv, run
`uv run --locked gemini-transcribe` from the repository instead.

Supported file extensions: WAV, MP3, AIFF/AIF, AAC, OGG, FLAC and M4A.
Files must be nonempty and smaller than 2 GiB; model duration and output limits
still apply.

Each invocation creates a unique output directory containing a JSON transcript,
a readable TXT transcript and SRT subtitles. Existing transcripts are preserved.
Labels such as `SPEAKER_01` identify voices within a recording, not real-world
identities. The language option is a hint; the transcript stays in the original
language. The prompt requests verbatim text, consistent labels, uncertain and
inaudible passages marked explicitly, and no invented names or summarization.

Interactions requests explicitly use `store=True` to opt into server-side
interaction storage. This is separate from the GenerateContent logging setting.
Deleting the uploaded file does not delete the stored interaction.

Audio is uploaded to Google's Files API, processed by Gemini, and deletion of
the uploaded file is attempted even after a generation or validation failure.
Deletion failures are reported. No audio is sent by setup or offline tests.
Audio and generated transcripts in the standard `audio/` and `transcripts/`
directories are ignored by Git. Custom output paths outside those directories
are your responsibility.

There are no automatic generation retries: rerunning can incur another API
charge. Incomplete or invalid structured responses fail rather than being
presented as successful transcripts. Timestamp and format validation cannot
establish that the model heard every word or assigned every speaker correctly.
Check a representative recording before relying on the results.

This version processes one whole recording per request. It does not split long
audio or reconcile speaker identities across recordings. Very long recordings
can exceed model input/output limits. SRT timestamps are model estimates, not
forced word alignment, and overlapping speech can produce overlapping cues.

## Billing

API usage follows the billing and quotas of the API-key project. Google AI Pro
is not an unlimited API subscription. Any Google Developer Program credit must
be activated and applied to the appropriate billing account. Check your project's
usage and current model pricing before processing large collections.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
```

The SDK is pinned because its Files and Interactions clients interpret retry
settings differently. The CLI explicitly disables Interactions retries; preserve
that behavior and its transport test when upgrading.

Tests use synthetic responses and mocked clients; no credentials or network
access are required for the test suite itself.

Official references:

- [Gemini audio input and transcription](https://ai.google.dev/gemini-api/docs/audio)
- [Structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [API billing](https://ai.google.dev/gemini-api/docs/billing)
- [Google Developer Program benefits](https://developers.google.com/program/plans-and-pricing)
