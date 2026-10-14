"""Public CLI pipeline selection, provenance and resume without provider calls."""
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest
from gemini_transcribe import pipeline, cli
import test_bundle
from test_long_decode import hop


class DualPipelineTests(unittest.TestCase):
    setUp = test_bundle.BundleTests.setUp
    metadata = test_bundle.BundleTests.metadata

    def invoke(self, *arguments):
        self.client.__enter__.return_value = self.client
        stdout = io.StringIO()
        with patch.dict(os.environ, {'GEMINI_API_KEY':'synthetic-key', 'GEMINI_MODEL':''}), patch.object(cli.genai, 'Client', return_value=self.client), patch.object(pipeline.bundle, 'audio_metadata', return_value=self.metadata()), patch('sys.stdout',stdout), patch('sys.stderr',io.StringIO()):
            code = pipeline.main([*arguments, '--output-dir', str(self.args.output_dir), '--json'])
        return code,json.loads(stdout.getvalue())

    def test_standard_default_reports_only_actual_usage(self):
        result = test_bundle.response(test_bundle.transcript())
        result.usage = {'total_input_tokens':20,'total_output_tokens':10}
        self.client.interactions.create.return_value=result
        code,receipt=self.invoke(str(self.audio),'--transcript-only')
        self.assertEqual(code,0)
        generation=json.loads(Path(receipt['manifest_path']).read_text())['configuration']['generation']
        self.assertEqual(generation['pipeline'],'standard')
        self.assertEqual(generation['stages']['transcription'][0]['hops'][0]['usage'],{'prompt_token_count':20,'candidates_token_count':10})
        self.client.models.generate_content.assert_not_called()

    def test_long_decode_cli_resume_infers_settings_and_preserves_failed_attempt(self):
        self.client.models.generate_content.side_effect=[hop(json.dumps(test_bundle.transcript())),RuntimeError('private'),hop(json.dumps(test_bundle.translation()))]
        code,receipt=self.invoke(str(self.audio),'--pipeline','long-decode','--max-output-tokens','70000')
        self.assertEqual(code,3)
        path=receipt['manifest_path']
        self.client.reset_mock()
        self.client.models.generate_content.side_effect=[hop(json.dumps(test_bundle.analysis()))]
        code,receipt=self.invoke('--resume',path)
        self.assertEqual(code,0)
        generation=json.loads(Path(receipt['manifest_path']).read_text())['configuration']['generation']
        self.assertEqual(generation['settings']['max_output_tokens'],70000)
        self.assertEqual([a['status'] for a in generation['stages']['analysis']],['failed','complete'])
        self.client.files.upload.assert_not_called()
        self.client.models.generate_content.assert_called_once()
        self.assertEqual(self.client.models.generate_content.call_args.kwargs['config'].max_output_tokens,70000)
        self.client.reset_mock()
        code,_=self.invoke('--resume',path,'--pipeline','standard')
        self.assertEqual(code,2)
        self.client.models.generate_content.assert_not_called()

    def test_conflicts_rejected_before_upload(self):
        for options in [('--pipeline','long-decode','--model','gemini-3.8-flash'),('--pipeline','standard','--max-hops','2'),('--pipeline','long-decode','--deadline-seconds','nan'),('--pipeline','long-decode','--max-output-tokens','0')]:
            with self.subTest(options=options):
                self.assertEqual(self.invoke(str(self.audio),*options)[0],2)
        self.client.files.upload.assert_not_called()

    def test_canonical_serialization_size_checked_before_writing(self):
        data = test_bundle.transcript()
        data['segments'][0]['text'] = ''
        compact = len(json.dumps(data, separators=(',',':')).encode())
        data['segments'][0]['text'] = 'x' * (8*1024*1024-compact-1)
        self.assertLess(len(json.dumps(data, separators=(',',':')).encode()),8*1024*1024)
        with self.assertRaises(ValueError):
            pipeline.bundle.export_stage(self.root, 'transcript', data)
        self.assertFalse((self.root/'transcript.json').exists())

    def test_offline_export_ignores_model_environment(self):
        self.client.interactions.create.return_value = test_bundle.response(test_bundle.transcript())
        code, receipt = self.invoke(str(self.audio),'--transcript-only')
        self.assertEqual(code,0)
        with patch.dict(os.environ, {'GEMINI_MODEL':'gemini-4-argon','GEMINI_API_KEY':''}), patch('sys.stdout',io.StringIO()):
            self.assertEqual(pipeline.main(['--export',receipt['manifest_path'],'--output-dir',str(self.root/'exports'),'--json']),0)

if __name__ == '__main__': unittest.main()

