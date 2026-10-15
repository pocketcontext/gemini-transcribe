"""Offline tests; no audio contents are sent to any service."""

import io
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx
from google.genai.interactions import Interaction
from gemini_transcribe import cli


def transcript():
    return {
        "language": "English",
        "segments": [
            {"start": 0.0, "end": 1.25, "speaker": "SPEAKER_01", "text": "Hello."},
            {"start": 1.1, "end": 2.5, "speaker": "SPEAKER_02", "text": "Hi!"},
        ],
    }


class TranscriptTests(unittest.TestCase):
    def test_overlapping_speakers_are_valid(self):
        self.assertEqual(cli.validate_transcript(transcript()), transcript())

    def test_invalid_segment_fields_are_rejected(self):
        for field, value in [
            ("start", -1), ("start", math.nan), ("end", math.inf),
            ("start", True), ("end", "2"), ("end", -1),
            ("speaker", "Alice"), ("speaker", ""), ("text", ""),
        ]:
            with self.subTest(field=field, value=value):
                data = transcript()
                data["segments"][0][field] = value
                with self.assertRaises(ValueError):
                    cli.validate_transcript(data)

    def test_out_of_order_segments_are_preserved(self):
        data = transcript()
        data["segments"].reverse()
        self.assertEqual(cli.validate_transcript(data), data)

    def test_missing_structure_is_rejected(self):
        for data in [None, [], {}, {"language": "English"},
                     {"language": "English", "segments": [{}]}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                cli.validate_transcript(data)

    def test_subtitles_include_timestamps_and_speakers(self):
        output = cli.render_srt(transcript())
        self.assertIn("00:00:00,000 --> 00:00:01,250", output)
        self.assertIn("SPEAKER_01", output)
        self.assertIn("Hello.", output)
        self.assertIn("00:00:01,100 --> 00:00:02,500", output)

    def test_text_preserves_both_speakers(self):
        output = cli.render_txt(transcript())
        for value in ("SPEAKER_01", "SPEAKER_02", "Hello.", "Hi!"):
            self.assertIn(value, output)


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.upload = SimpleNamespace(name="files/example", uri="fake://audio",
                                      mime_type="audio/wav", state="ACTIVE")
        self.client.files.upload.return_value = self.upload
        self.client.interactions.create.return_value = SimpleNamespace(
            status="completed", output_text=json.dumps(transcript()))

    def test_success_uses_audio_and_deletes_upload(self):
        result = cli.transcribe(self.client, Path("example.wav"), "test-model")
        self.assertEqual(result, transcript())
        request = self.client.interactions.create.call_args.kwargs
        self.assertEqual(request["model"], "test-model")
        self.assertFalse(request["store"])
        self.assertEqual(request["input"][1]["uri"], "fake://audio")
        self.client.files.delete.assert_called_once_with(name="files/example")

    def test_processing_is_polled_before_generation(self):
        self.client.files.upload.return_value = SimpleNamespace(
            name="files/example", state="PROCESSING")
        self.client.files.get.return_value = self.upload
        with patch.object(cli.time, "sleep"):
            cli.transcribe(self.client, "example.wav")
        self.client.files.get.assert_called_once_with(name="files/example")
        self.client.interactions.create.assert_called_once()
        self.client.files.delete.assert_called_once_with(name="files/example")

    def test_processing_failure_and_timeout_delete_upload(self):
        for state, timeout in [("FAILED", 300), ("PROCESSING", 0)]:
            with self.subTest(state=state):
                self.client.reset_mock()
                self.upload.state = state
                with self.assertRaises(cli.TranscriptionError) as caught:
                    cli.transcribe(self.client, "example.wav", processing_timeout=timeout)
                self.assertEqual(caught.exception.code, "processing_timeout" if state == "PROCESSING" else "processing_failed")
                self.client.interactions.create.assert_not_called()
                self.client.files.delete.assert_called_once_with(name="files/example")

    def test_bad_or_truncated_response_is_rejected_and_cleaned_up(self):
        for response in [
            {"status": "completed", "output_text": "{"},
            {"status": "completed", "output_text": "{}"},
            {"status": "incomplete", "output_text": json.dumps(transcript())},
            {"status": "completed", "outputs": []},
        ]:
            with self.subTest(response=response):
                self.client.reset_mock()
                self.client.interactions.create.return_value = response
                with self.assertRaises(cli.TranscriptionError) as caught:
                    cli.transcribe(self.client, "example.wav")
                self.assertEqual(caught.exception.code, "incomplete_response" if response["status"] != "completed" else "invalid_output")
                self.client.files.delete.assert_called_once_with(name="files/example")

    def test_text_output_blocks_are_supported(self):
        self.client.interactions.create.return_value = {
            "status": "completed", "outputs": [
                {"type": "text", "text": json.dumps(transcript())}]}
        self.assertEqual(cli.transcribe(self.client, "example.wav"), transcript())

    def test_installed_sdk_typed_file_and_interaction_response(self):
        self.client.files.upload.return_value = cli.types.File(
            name="files/example", uri="fake://audio", mime_type="audio/wav",
            state=cli.types.FileState.ACTIVE)
        self.client.interactions.create.return_value = Interaction.model_validate({
            "status": "completed", "steps": [{"type": "model_output", "content": [
                {"type": "text", "text": json.dumps(transcript())}]}]})
        self.assertEqual(cli.transcribe(self.client, "example.wav"), transcript())
        self.client.files.delete.assert_called_once_with(name="files/example")

    def test_remote_errors_do_not_expose_payload(self):
        self.client.interactions.create.side_effect = RuntimeError("SECRET_AUDIO_TEXT")
        with self.assertRaises(cli.TranscriptionError) as caught:
            cli.transcribe(self.client, "example.wav")
        self.assertNotIn("SECRET_AUDIO_TEXT", str(caught.exception))
        self.client.files.delete.assert_called_once()

    def test_cleanup_failure_preserves_success_and_warns(self):
        self.client.files.delete.side_effect = RuntimeError("SECRET_TOKEN")
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            self.assertEqual(cli.transcribe(self.client, "example.wav"), transcript())
        self.assertIn("deletion failed", stderr.getvalue())
        self.assertNotIn("SECRET_TOKEN", stderr.getvalue())


class OutputAndCliTests(unittest.TestCase):
    def test_sdk_http_failure_is_not_retried_by_cli(self):
        requests = []

        def unavailable(request):
            requests.append(request)
            return httpx.Response(503, json={"error": {"message": "synthetic failure"}})

        transport = httpx.MockTransport(unavailable)
        with httpx.Client(transport=transport) as http_client:
            sdk_client = cli.genai.Client(api_key="synthetic-key", http_options=cli.types.HttpOptions(
                httpx_client=http_client, retry_options=cli.types.HttpRetryOptions(attempts=1)))
            with tempfile.TemporaryDirectory() as root:
                audio = Path(root) / "test.wav"
                audio.write_bytes(b"synthetic test data")
                uploaded = cli.types.File(name="files/test", uri="fake://test",
                                          state=cli.types.FileState.ACTIVE, mime_type="audio/wav")
                with patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-key"}), \
                     patch.object(cli.genai, "Client", return_value=sdk_client), \
                     patch.object(sdk_client.files, "upload", return_value=uploaded), \
                     patch.object(sdk_client.files, "delete") as delete, \
                     patch("sys.stderr", io.StringIO()), \
                     patch("gemini_transcribe.bundle.audio_metadata", return_value={"name":"test.wav","sha256":"0"*64,"size_bytes":19,"mime_type":"audio/wav","duration_seconds":3}):
                    result = cli.main([str(audio), "--output-dir", str(Path(root) / "output")])
                self.assertEqual(result, 1)
                self.assertEqual(len(requests), 1)
                delete.assert_called_once_with(name="files/test")
                self.assertEqual(len(list((Path(root) / "output").glob("*/manifest.json"))), 1)

    def test_repeated_exports_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            first = cli.write_outputs(transcript(), root)
            before = {p.name: p.read_bytes() for p in first.iterdir()}
            second = cli.write_outputs(transcript(), root)
            self.assertNotEqual(first, second)
            self.assertEqual(before, {p.name: p.read_bytes() for p in first.iterdir()})
            self.assertEqual(set(before), {"transcript.json", "transcript.txt", "transcript.srt"})
            self.assertEqual(json.loads((second / "transcript.json").read_text()), transcript())

    def test_invalid_data_creates_no_output_directory(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "new-output"
            with self.assertRaises(ValueError):
                cli.write_outputs({}, output)
            self.assertFalse(output.exists())

    def test_missing_or_placeholder_key_never_initializes_client(self):
        for key in ["", "replace-me", "your_api_key"]:
            with self.subTest(key=key), patch.dict(os.environ, {"GEMINI_API_KEY": key}), \
                 patch.object(cli.genai, "Client") as client, patch("sys.stderr", io.StringIO()):
                self.assertEqual(cli.main(["example.wav"]), 2)
                client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
