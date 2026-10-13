"""Safe failure diagnostics at provider, local export and CLI boundaries."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import httpx
from gemini_transcribe import bundle, cli, pipeline
import test_bundle


class ErrorTests(unittest.TestCase):
    def test_timeout_and_http_diagnostics_hide_payloads(self):
        for cause in (TimeoutError('SECRET'), httpx.ReadTimeout('SECRET')):
            wrapped = RuntimeError('SECRET wrapper')
            wrapped.__cause__ = cause
            for exc in (cause, wrapped):
                error = pipeline.safe_error(exc, 'analysis')
                self.assertEqual(error.code, 'request_timeout')
                self.assertNotIn('SECRET', str(error))
        error = RuntimeError('SECRET provider payload')
        error.status_code = 429
        safe = pipeline.safe_error(error, 'analysis')
        self.assertEqual(pipeline.error_entry('analysis', safe),
                         {'stage': 'analysis', 'code': 'request_failed', 'http_status': 429})
        self.assertNotIn('SECRET', str(safe))

    def test_incomplete_enrichment_is_distinct_from_invalid_output(self):
        client = MagicMock()
        client.interactions.create.return_value = {'status': 'incomplete'}
        with self.assertRaises(cli.TranscriptionError) as caught:
            pipeline.generate(client, 'test-model', 'analysis', {}, 'English')
        self.assertEqual(caught.exception.code, 'incomplete_response')

    def fixture(self):
        fixture = test_bundle.BundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_export_failure_retains_safe_stage_error(self):
        fixture = self.fixture()
        fixture.client.interactions.create.return_value = test_bundle.response(test_bundle.transcript())
        with patch.object(bundle, 'export_stage', side_effect=OSError('SECRET local path')):
            directory, manifest = fixture.process()
        self.assertEqual(manifest['errors'], [{'stage': 'transcription', 'code': 'export_failed'}])
        self.assertEqual(manifest['status'], 'failed')
        self.assertNotIn('SECRET', (directory/'manifest.json').read_text())
        fixture.client.files.delete.assert_called_once()

    def test_changed_snapshot_is_not_a_provider_error(self):
        fixture = self.fixture()
        def transcribe(*args, **kwargs):
            Path(args[1]).write_bytes(b'changed')
            return bundle.validate_transcript(test_bundle.transcript(), 2)
        with patch.object(cli, 'transcribe', side_effect=transcribe):
            _, manifest = fixture.process()
        self.assertEqual(manifest['errors'], [{'stage': 'transcription', 'code': 'source_changed'}])

    def test_lock_and_invalid_resume_receipts_are_actionable_and_safe(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = Path(root)/'manifest.json'
            manifest.write_text('SECRET invalid json')
            client = MagicMock()
            client.__enter__.return_value = client
            for locked, path, code, message in ((True, manifest, 'bundle_in_use', 'Bundle is in use'),
                                               (False, manifest, 'resume_invalid', 'Cannot resume'),
                                               (False, Path(root)/'missing'/'manifest.json', 'resume_invalid', 'Cannot resume')):
                stdout, stderr = io.StringIO(), io.StringIO()
                context = patch.object(pipeline.fcntl, 'flock', side_effect=BlockingIOError('SECRET')) if locked else patch.object(pipeline, 'generate')
                with patch.dict(os.environ, {'GEMINI_API_KEY': 'synthetic-test-key'}), \
                     patch.object(cli.genai, 'Client', return_value=client), context, \
                     patch('sys.stdout', stdout), patch('sys.stderr', stderr):
                    result = pipeline.main(['--resume', str(path), '--json'])
                self.assertEqual(result, 1)
                self.assertEqual(json.loads(stdout.getvalue())['errors'], [{'stage': 'resume', 'code': code}])
                self.assertIn(message, stderr.getvalue())
                self.assertNotIn('SECRET', stdout.getvalue()+stderr.getvalue())
                client.interactions.create.assert_not_called()
