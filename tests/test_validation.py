"""Validation failures identify safe locations without retaining provider content."""
import copy
import io
import json
import os
import unittest
from unittest.mock import patch

from gemini_transcribe import bundle, cli, pipeline
from gemini_transcribe.validation import ValidationError, diagnostic
import test_bundle


class ValidationTests(unittest.TestCase):
    def test_schema_and_semantic_failures_have_safe_paths(self):
        cases = [
            ('missing_field', '$.language', lambda d: d.pop('language')),
            ('unexpected_fields', '$', lambda d: d.update({'SECRET extra key': 'SECRET value'})),
            ('expected_array', '$.segments', lambda d: d.update(segments='SECRET')),
            ('expected_object', '$.segments[0]', lambda d: d['segments'].__setitem__(0, 'SECRET')),
            ('expected_array', '$.audio_annotations', lambda d: d.update(audio_annotations=None)),
            ('expected_finite_number', '$.segments[0].start', lambda d: d['segments'][0].update(start=True)),
            ('invalid_speaker_label', '$.segments[0].speaker', lambda d: d['segments'][0].update(speaker='SECRET')),
            ('timestamp_out_of_bounds', '$.segments[0]', lambda d: d['segments'][0].update(end=999)),
            ('invalid_enum', '$.tone[0].subjective', lambda d: d['tone'][0].update(subjective=False)),
            ('unknown_segment_reference', '$.tone[0].segment_ids[0]', lambda d: d['tone'][0].update(segment_ids=['SECRET'])),
            ('empty_references', '$.tone[0].segment_ids', lambda d: d['tone'][0].update(segment_ids=[])),
            ('duplicate_references', '$.tone[0].segment_ids', lambda d: d['tone'][0].update(segment_ids=['seg000001']*2)),
            ('unknown_speaker', '$.audio_annotations[0].speakers[0]', lambda d: d['audio_annotations'][0].update(speakers=['SECRET'])),
        ]
        for reason, path, mutate in cases:
            with self.subTest(reason=reason, path=path):
                data = test_bundle.transcript()
                mutate(data)
                with self.assertRaises(ValidationError) as caught:
                    bundle.validate_transcript(data, 2)
                self.assertEqual(diagnostic(caught.exception), {'reason': reason, 'path': path})
                self.assertNotIn('SECRET', str(caught.exception))

    def test_timing_warnings_preserve_evidence_and_safe_exports(self):
        data = test_bundle.transcript()
        data['segments'] = [
            {'start': 15, 'end': 10, 'speaker': 'SPEAKER_01', 'text': 'PRIVATE first'},
            {'start': 9, 'end': 12, 'speaker': 'SPEAKER_01', 'text': 'PRIVATE second'}]
        data['audio_annotations'][0].update(start=5, end=4)
        canonical = bundle.validate_transcript(data, 20)
        review = bundle.timing_review(canonical)
        self.assertEqual(review['status'], 'required')
        self.assertEqual([w['reason'] for w in review['warnings']],
                         ['timestamp_reversed', 'timestamp_backward', 'timestamp_reversed'])
        self.assertEqual(review['warnings'][1], {
            'reason': 'timestamp_backward', 'path': '$.segments[1]', 'index': 1,
            'id': 'seg000002', 'start': 9, 'end': 12, 'previous_id': 'seg000001',
            'previous_start': 15, 'backward_seconds': 6})
        self.assertNotIn('PRIVATE', json.dumps(review))
        self.assertEqual([s['start'] for s in canonical['segments']], [15, 9])
        self.assertNotIn('PRIVATE first', cli.render_srt(canonical))
        self.assertIn('PRIVATE second', cli.render_srt(canonical))
        self.assertIn('timing unavailable; review required', cli.render_txt(canonical))
        self.assertEqual(cli.render_srt({'segments': canonical['segments'][:1]}), '\n')

    def test_warning_bundle_survives_export_resume_and_rejects_tampering(self):
        fixture = test_bundle.BundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        data = test_bundle.transcript()
        data['segments'][0].update(start=1.5)
        fixture.args.transcript_only = True
        fixture.client.interactions.create.return_value = test_bundle.response(data)
        directory, manifest = fixture.process()
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(manifest['timing_review']['status'], 'required')
        self.assertEqual(manifest['errors'], [])
        pipeline.load_resume(directory/'manifest.json')
        fixture.client.interactions.create.assert_called_once()
        fixture.client.files.delete.assert_called_once()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'synthetic-test-key'}), \
             patch.object(cli.genai, 'Client'), patch.object(pipeline, 'process', return_value=(directory, manifest)), \
             patch('sys.stdout', stdout), patch('sys.stderr', stderr):
            status = pipeline.main([str(fixture.audio), '--json'])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())['timing_review'], manifest['timing_review'])
        self.assertIn('timestamp review required', stderr.getvalue())
        exported, copied = pipeline.export_copy(directory/'manifest.json', directory/'copies')
        self.assertEqual(copied['timing_review'], manifest['timing_review'])
        original_review = copy.deepcopy(manifest['timing_review'])
        for key, value in [('index', False), ('end', True), ('start', '1.5')]:
            manifest['timing_review'] = copy.deepcopy(original_review)
            manifest['timing_review']['warnings'][0][key] = value
            bundle.write_json(directory/'manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'timing review'):
                pipeline.load_resume(directory/'manifest.json')
        manifest['timing_review'] = original_review
        manifest['timing_review']['warnings'] = []
        bundle.write_json(directory/'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'timing review'):
            pipeline.load_resume(directory/'manifest.json')
        del manifest['timing_review']
        bundle.write_json(directory/'manifest.json', manifest)
        self.assertEqual(pipeline.load_resume(directory/'manifest.json')[1]['timing_review']['status'], 'required')

    def test_json_and_untyped_exception_never_expose_messages(self):
        try:
            json.loads('{"SECRET":\n SECRET}')
        except json.JSONDecodeError as exc:
            detail = diagnostic(exc)
        self.assertEqual(detail['reason'], 'invalid_json')
        self.assertEqual(detail['line'], 2)
        self.assertNotIn('SECRET', json.dumps(detail))
        self.assertEqual(diagnostic(ValueError('SECRET')), {'reason': 'invalid_structure', 'path': '$'})

    def test_failed_transcription_persists_diagnostic_and_cleans_upload(self):
        fixture = test_bundle.BundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bad = test_bundle.transcript()
        bad['segments'][0]['text'] = 'SECRET transcript'
        bad['tone'][0]['segment_ids'] = ['SECRET reference']
        fixture.client.interactions.create.return_value = test_bundle.response(bad)
        directory, manifest = fixture.process()
        detail = {'reason': 'unknown_segment_reference', 'path': '$.tone[0].segment_ids[0]'}
        self.assertEqual(manifest['errors'], [{'stage': 'transcription', 'code': 'invalid_output', 'validation': detail}])
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['artifacts'], [])
        self.assertEqual(list(directory.iterdir()), [directory/'manifest.json'])
        self.assertNotIn('SECRET', (directory/'manifest.json').read_text())
        fixture.client.interactions.create.assert_called_once()
        fixture.client.files.delete.assert_called_once()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'synthetic-test-key'}), \
             patch.object(cli.genai, 'Client'), patch.object(pipeline, 'process', return_value=(directory, manifest)), \
             patch('sys.stdout', stdout), patch('sys.stderr', stderr):
            status = pipeline.main([str(fixture.audio), '--json'])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(stdout.getvalue())['errors'][0]['validation'], detail)
        self.assertIn('unknown_segment_reference at $.tone[0].segment_ids[0]', stderr.getvalue())
        self.assertNotIn('SECRET', stdout.getvalue()+stderr.getvalue())

    def test_enrichment_diagnostics_preserve_valid_transcript(self):
        fixture = test_bundle.BundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        analysis = test_bundle.analysis()
        analysis['action_items'][0]['owner'] = {'SECRET': 'SECRET'}
        translation = test_bundle.translation()
        translation['segments'][0]['segment_id'] = 'SECRET'
        fixture.client.interactions.create.side_effect = [test_bundle.response(test_bundle.transcript()),
            test_bundle.response(analysis), test_bundle.response(translation)]
        directory, manifest = fixture.process()
        self.assertEqual(manifest['status'], 'partial')
        self.assertEqual(manifest['stages']['transcription'], 'complete')
        self.assertEqual([e['validation'] for e in manifest['errors']], [
            {'reason': 'invalid_nullable_value', 'path': '$.action_items[0].owner'},
            {'reason': 'translation_segment_order', 'path': '$.segments[0].segment_id'}])
        self.assertNotIn('SECRET', (directory/'manifest.json').read_text())
        pipeline.load_resume(directory/'manifest.json')
        self.assertEqual(fixture.client.interactions.create.call_count, 3)

    def test_resume_rejects_content_in_diagnostics(self):
        fixture = test_bundle.BundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.client.interactions.create.side_effect = [test_bundle.response(test_bundle.transcript()),
            test_bundle.response({}), test_bundle.response(test_bundle.translation())]
        directory, manifest = fixture.process()
        for detail in ({'reason': 'SECRET', 'path': '$'},
                       {'reason': 'missing_field', 'path': '$.SECRET'},
                       {'reason': 'invalid_json', 'path': '$', 'line': 'SECRET', 'column': 1}):
            manifest['errors'][0]['validation'] = detail
            bundle.write_json(directory/'manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'Invalid validation diagnostic'):
                pipeline.load_resume(directory/'manifest.json')
