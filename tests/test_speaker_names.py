"""Synthetic name resolution and offline, byte-preserving presentation exports."""
import copy
import io
import json
import os
import unittest
from unittest.mock import patch
from gemini_transcribe import speaker_names, pipeline, cli, bundle
import test_bundle
from test_bundle import transcript, analysis, translation, response


class NamedExports(unittest.TestCase):
    setUp = test_bundle.BundleTests.setUp
    metadata = test_bundle.BundleTests.metadata
    process = test_bundle.BundleTests.process
    def named_analysis(self):
        data = analysis()
        data['speaker_mappings'] = [{'speaker': 'SPEAKER_01', 'name': 'Alice', 'status': 'explicit', 'segment_ids': ['seg000001']}]
        return data

    def test_pipeline_and_offline_copy_preserve_canonical_bytes(self):
        self.client.interactions.create.side_effect = [response(transcript()), response(self.named_analysis()), response(translation())]
        directory, manifest = self.process()
        self.assertEqual(list(self.args.output_dir.iterdir()), [directory])
        self.assertEqual(manifest['schema_version'], 'gemini-transcribe.bundle/v2')
        self.assertIn('Alice (SPEAKER_01)', (directory/'transcript.txt').read_text())
        self.assertIn('Alice (SPEAKER_01)', (directory/'translation.srt').read_text())
        original = {p.name: p.read_bytes() for p in directory.iterdir()}
        override = self.root/'names.json'
        bundle.write_json(override, speaker_names.metadata([{'speaker':'SPEAKER_01','name':'Alicia','reason':'Reviewed introduction'}]))
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(cli.genai, 'Client') as client, patch('sys.stdout', output):
            code = cli.main(['--export', str(directory/'manifest.json'), '--speaker-overrides', str(override), '--output-dir', str(self.root/'named'), '--json'])
        self.assertEqual(code, 0)
        client.assert_not_called()
        exported = __import__('pathlib').Path(json.loads(output.getvalue())['manifest_path']).parent
        for name in ('transcript.json', 'analysis.json', 'translation.json'):
            self.assertEqual((exported/name).read_bytes(), original[name])
        self.assertEqual({p.name:p.read_bytes() for p in directory.iterdir()}, original)
        self.assertIn('Alicia (SPEAKER_01)', (exported/'transcript.srt').read_text())
        pipeline.load_resume(exported/'manifest.json')
        self.assertEqual(exported.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in exported.iterdir()))

    def test_partial_export_and_tamper(self):
        self.client.interactions.create.side_effect = [response(transcript()), RuntimeError(), response(translation())]
        directory, manifest = self.process()
        bundle.write_json(directory/'manifest.json', manifest)
        exported, result = pipeline.export_copy(directory/'manifest.json', self.root/'export')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('SPEAKER_01:', (exported/'transcript.txt').read_text())
        (directory/'transcript.txt').write_text('tampered')
        with self.assertRaises(ValueError):
            pipeline.export_copy(directory/'manifest.json', self.root/'export')

    def test_export_failure_preserves_original_and_removes_partial_copy(self):
        self.args.transcript_only = True
        self.client.interactions.create.return_value = response(transcript())
        directory, manifest = self.process()
        original = (directory/'manifest.json').read_bytes()
        with patch.object(cli, 'render_srt', side_effect=OSError()):
            with self.assertRaises(OSError):
                pipeline.export_copy(directory/'manifest.json', self.root/'export')
        self.assertEqual((directory/'manifest.json').read_bytes(), original)
        self.assertEqual(list((self.root/'export').iterdir()), [])

    def test_conservative_resolution_and_reviewed_precedence(self):
        data = bundle.validate_transcript(transcript(), 2)
        second = copy.deepcopy(data['segments'][0]); second.update(id='seg000002', speaker='SPEAKER_02')
        data['segments'].append(second)
        settings = speaker_names.metadata()
        items = self.named_analysis()
        resolve = lambda: speaker_names.resolve(data, items, settings)
        self.assertEqual(resolve(), {'SPEAKER_01':'Alice'})
        self.assertEqual(speaker_names.resolve(data, items), {})
        items['speaker_mappings'][0]['status'] = 'tentative'
        self.assertEqual(resolve(), {})
        items['speaker_mappings'][0]['status'] = 'explicit'
        items['speaker_mappings'].append({**items['speaker_mappings'][0], 'name':'Bob'})
        self.assertEqual(resolve(), {})
        items['speaker_mappings'][-1].update(speaker='SPEAKER_02', name=' alice ')
        self.assertEqual(resolve(), {})
        settings['overrides'] = [{'speaker':'SPEAKER_01','name':'Alice','reason':'Reviewed'}]
        self.assertEqual(resolve(), {'SPEAKER_01':'Alice'})
        settings['overrides'].append({'speaker':'SPEAKER_02','name':'alice','reason':'Reviewed'})
        with self.assertRaises(ValueError): resolve()
        for name in ('Alice\nInjected', '\u202eAlice', 'x'*201):
            settings['overrides'] = [{'speaker':'SPEAKER_01','name':name,'reason':'Reviewed'}]
            with self.assertRaises(ValueError): resolve()

    def test_old_version_and_missing_name_contract_rejected(self):
        self.args.transcript_only = True
        self.client.interactions.create.return_value = response(transcript())
        directory, manifest = self.process()
        original = copy.deepcopy(manifest)
        manifest['schema_version'] = 'gemini-transcribe.bundle/v1'
        bundle.write_json(directory/'manifest.json', manifest)
        with self.assertRaises(ValueError):
            pipeline.load_resume(directory/'manifest.json')
        original.pop('speaker_names')
        bundle.write_json(directory/'manifest.json', original)
        with self.assertRaises(KeyError):
            pipeline.load_resume(directory/'manifest.json')

    def test_resume_recovers_failed_named_presentation_without_provider(self):
        self.client.interactions.create.side_effect = [response(transcript()), response(self.named_analysis()), response(translation())]
        with patch.object(pipeline, 'export_copy', side_effect=OSError()):
            with self.assertRaises(OSError): self.process()
        pending = next(self.args.output_dir.glob('*/manifest.json'))
        pipeline.load_resume(pending)
        self.args.resume = pending
        self.client.reset_mock()
        directory, manifest = self.process()
        self.client.interactions.create.assert_not_called()
        self.assertIn('Alice (SPEAKER_01)', (directory/'transcript.txt').read_text())
        self.assertEqual(manifest['status'], 'complete')
