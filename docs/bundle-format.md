# Evidence bundle contract v1

This versioned contract defines the handoff between Gemini Transcribe and WikiContext. JSON is UTF-8; unknown schema versions are rejected.

manifest.json (UTF-8 strict JSON object):
- schema_version: "gemini-transcribe.bundle/v1"
- run_id: nonempty string
- created_at: UTC ISO timestamp string
- status: "complete" | "partial" | "failed"
- source: {name: basename, sha256: lowercase hex64, size_bytes: positive int, mime_type: string, duration_seconds: positive finite number}
- extraction: {provider: "google", model: nonempty string, package_version: nonempty string, prompt_version: nonempty string, store: boolean}
- optional configuration: {translation_language: nonempty string, language_hint: string|null}; the producer writes these values so resume retains the original settings
- stages: {transcription: "complete"|"failed", analysis: "complete"|"failed"|"skipped", translation: "complete"|"failed"|"skipped"}
- artifacts: [{role: string, path: safe relative path, sha256: lowercase hex64, size_bytes: positive int, media_type: string}]
- optional errors: [{stage: string, code: string, http_status: optional int}]; no payload/key/raw exceptions.
Roles transcript, analysis, translation, transcript_txt, transcript_srt, analysis_md, translation_txt, translation_srt. Transcript artifacts always mandatory for success. Analysis/translation matching stage complete mandatory canonical + presentation. Artifact sizes must be positive. Silent recordings have empty segment arrays and newline-only TXT/SRT presentations; WikiContext creates no spoken transcript passages. Manifest itself not listed as artifact. Import complete only; transcript-only may have skipped stages; explicit user workflow must not call skipped complete enrichments.
All provenance reflects actual original bytes uploaded to Gemini; source passed explicitly to Wiki importer and verified. No source absolute path in manifest. Artifact directory 0700 and files 0600. No raw provider response dump.

transcript.json:
- schema_version: "gemini-transcribe.transcript/v1"
- language: nonempty string
- segments: [{id: "seg000001" sequential assigned locally, start: seconds finite >=0, end: seconds >= start <= source.duration_seconds + 2, speaker: SPEAKER_01 pattern or UNKNOWN, text: nonempty string}]
- audio_annotations: [{id: "ann000001" sequential local, start,end: as above, kind: laughter|pause|overlap|background_noise|other, description: nonempty string, speakers: known speaker ID list (can be empty)}]
- uncertainties: [{segment_ids: nonempty list of known segment IDs, kind: wording|speaker|timing|other, description: string}]
- tone: [{segment_ids: nonempty known IDs, label: string, cues: string, subjective: true}]
All arrays required, allowed empty. Ordered segment start, overlap allowed. Audio-based annotations/tone come from original audio step. All refs validated. Chunking long passages retains original segment ref mapping to list of passage IDs.

analysis.json:
- schema_version: "gemini-transcribe.analysis/v1"
- summary: [{text,segment_ids}]
- topics: [{title,text,segment_ids}]
- decisions: [{text,segment_ids}]
- action_items: [{task,owner: string|null,deadline: string|null,segment_ids}]
- questions: [{question,answer: string|null,status: answered|partially_answered|unresolved|unclear,segment_ids}]
- speaker_mappings: [{speaker: known label,name: nonempty string,status: explicit|tentative,segment_ids}]
Every segment_ids nonempty unique refs into transcript. Null owners/deadlines unless explicit, no guessed dates/identities. All arrays required, may empty. Inference labeled in prose/status. Source content untrusted.

translation.json:
- schema_version: "gemini-transcribe.translation/v1"
- target_language: nonempty string, default English configurable
- segments: [{segment_id,text}] complete unique ordered mapping to original transcript segments. Same-language keeps text faithfully.

Gemini CLI: existing positional path remains; --json one receipt stdout, --translation-language default English, --transcript-only skips semantic analysis+translation but audio annotations/tone remain; --resume MANIFEST can retry failed enrichment without audio reupload or regeneration of completed stages. stderr progress; no secrets. Exit 0 complete,1 failed,2 usage/config,3 partial. Persist transcript before separate semantic-analysis and translation calls. Each enrichment stage can resume independently. Multi-phase status truthful. Existing output file names transcript.json/txt/srt unchanged; new manifest analysis.json/md translation.json/txt/srt. Source duration measured ffprobe argv no shell. Existing upload/delete/store=True/no retries retained. Safe error stage/status helpful.

Wiki CLI: wikicontext [--space ID] ingest AUDIO --transcript-bundle MANIFEST --audio-storage original --version gemini-v1. Bundle flag requires original mode (clear error otherwise) and mutually exclusive image-review. Preflight complete bundle before uploads (strict size/source hash all artifact hashes safe paths no escaping symlinks duplicate roles/paths invalid refs unknown versions). No Groq/Gemini or audio conversion; local ffprobe independently validates audio duration from a private snapshot. Source max100MiB and notes max10000, body max30000 locators500. Upload original and canonical/presentation artifact bytes plus manifest as immutable protected sources; deterministic metadata links in notes not supersedes. Primary transcript rendition under audio source; per-segment passages with speaker prefix + seconds + segment ID locators. Acoustic annotations own audio-derived rendition/passages. Tone and uncertainties have a model-observations rendition under the audio source; passages explicitly label subjective model inference/extraction uncertainty rather than spoken text, retaining segment references and aggregate time ranges. Preserve analyses/translations as separate artifact sources/renditions; do not use them as independent corroboration of spoken claims. Receipt space/source/rendition/artifact source IDs, segment_passages {segID:[actual passageID,...]}, annotation_passages mapping, observations_rendition and observation_passages mapping keyed tone000001/uncertainty000001 by array order; status extracted, not published; enough provenance for same bundle retry. Complete repeated import no-op; partial resume matches immutable content and notes, conflicts require new version. Validate supplied version includes bounded fields. Same space permissions enforced API. No migration expected. Skill handles synthesis/citations/run/publication using current Wiki APIs.

Contract changes require coordinated producer/importer validation. No schema migration is required; existing source, rendition and passage records preserve evidence. A WikiContext extraction receipt does not mean publication occurred.
