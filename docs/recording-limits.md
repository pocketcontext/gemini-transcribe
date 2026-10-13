# Recording limits and measurement

The CLI processes one whole recording per request. It measures duration locally
with `ffprobe` before uploading, but does not enforce a duration cap or split audio.
Files must be smaller than 2 GiB; WikiContext imports additionally require an
original no larger than 100 MiB. Compression changes file size without reducing
the amount of speech that must fit in the generated transcript.

As checked on 2026-10-13, Google's [audio guide](https://ai.google.dev/gemini-api/docs/audio#technical-details-about-audio)
documents 32 input tokens per second and a maximum of 9.5 hours per prompt.
The [default model](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
has a 1,048,576-token input limit and a 65,536-token output limit. Both constraints
apply; the audio ceiling alone does not establish a usable transcription length.
Prompts, JSON structure, annotations and model reasoning also consume capacity.
Other models can have different limits.

The configured HTTP timeout is 600 seconds per request, not a ten-minute budget
for the whole run. File readiness polling has a separate 300-second deadline;
an in-flight HTTP request can extend the observed polling time. The CLI makes no
automatic generation retries. A timeout does not prove that Google stopped
processing or did not bill the request. Failed enrichment can be resumed from
the saved transcript; failed transcription requires a new run.

There is no measured live-provider duration guarantee for this release. Offline
tests use synthetic audio and mocked responses; they verify duration validation,
timeout handling and recovery, not Google's throughput or transcript completeness.
Do not interpret a passing offline test or the file-size cap as a long-recording
benchmark.

To establish a practical limit for a particular workload, use non-sensitive
representative speech, the intended model and the intended enrichment settings:

1. Measure each sample locally with `ffprobe -v error -show_entries format=duration
   -of default=noprint_wrappers=1:nokey=1 sample.wav` (one command line).
2. Test increasing durations, recording source duration, model, wall-clock time,
   stage outcome and provider token usage/cost. Live calls are metered.
3. Review coverage throughout each recording, including the end, speaker changes
   and dense passages. Valid JSON and bounded timestamps do not prove completeness.
4. Choose a workload limit below the longest consistently complete sample. Recheck
   after model or prompt changes. Preserve separate originals if splitting manually;
   timestamps and speaker labels are local to each resulting recording.

Until that benchmark exists, process a representative short recording first and
review its outputs before committing a large collection. No arbitrary smaller
byte limit can replace this measurement.
