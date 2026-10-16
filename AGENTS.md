# Gemini Transcribe

Read README.md and the portable skill before changes. Keep recordings, API keys,
transcripts and provider responses private. Use synthetic audio and mocked provider
responses for validation; follow the README release and launcher-pin sequence.

Authorized audio work includes up to three agent-initiated paid retry invocations
without renewed approval. Follow [the retry policy](skills/gemini-transcribe/references/retries.md),
reuse completed stages and retain retry counts across resumes. CLI/SDK automatic
retries remain disabled; do not change provider, model, pipeline or budgets implicitly.
