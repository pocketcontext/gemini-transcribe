"""Storage policy and stored-interaction provenance recovery with synthetic provider responses."""
import json
import unittest

from gemini_transcribe import pipeline
import test_bundle
from test_bundle import analysis, response, transcript, translation


class StorageTests(unittest.TestCase):
    setUp = test_bundle.BundleTests.setUp
    metadata = test_bundle.BundleTests.metadata
    process = test_bundle.BundleTests.process

    def test_new_bundle_and_all_requests_disable_storage(self):
        self.client.interactions.create.side_effect = [response(transcript()), response(analysis()), response(translation())]
        directory, manifest = self.process()
        self.assertIs(manifest['extraction']['store'], False)
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(len(self.client.interactions.create.call_args_list), 3)
        for call in self.client.interactions.create.call_args_list:
            self.assertIs(call.kwargs['store'], False)
        self.assertEqual(pipeline.load_resume(directory/'manifest.json')[1], manifest)

    def test_stored_interaction_resume_preserves_provenance_and_disables_new_storage(self):
        self.client.interactions.create.side_effect = [response(transcript()), RuntimeError('synthetic failure'), response(translation())]
        directory, manifest = self.process()
        manifest['extraction']['store'] = True
        path = directory/'manifest.json'
        path.write_text(json.dumps(manifest))
        original_transcript = (directory/'transcript.json').read_bytes()
        original_translation = (directory/'translation.json').read_bytes()
        self.args.resume = path
        self.client.reset_mock()
        self.client.interactions.create.side_effect = [response(analysis())]
        _, resumed = self.process()
        self.assertEqual(resumed['status'], 'complete')
        self.assertIs(resumed['extraction']['store'], True)
        self.assertIs(json.loads(path.read_text())['extraction']['store'], True)
        self.client.interactions.create.assert_called_once()
        self.assertIs(self.client.interactions.create.call_args.kwargs['store'], False)
        self.client.files.upload.assert_not_called()
        self.assertEqual((directory/'transcript.json').read_bytes(), original_transcript)
        self.assertEqual((directory/'translation.json').read_bytes(), original_translation)

    def test_resume_rejects_non_boolean_storage(self):
        self.args.transcript_only = True
        self.client.interactions.create.return_value = response(transcript())
        directory, manifest = self.process()
        path = directory/'manifest.json'
        for invalid in (None, 0, 1, 'false', 'true'):
            with self.subTest(store=invalid):
                manifest['extraction']['store'] = invalid
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    pipeline.load_resume(path)
