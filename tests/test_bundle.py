"""Synthetic evidence bundles and partial recovery; no network requests."""
import argparse
import copy
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import wave
from gemini_transcribe import bundle, pipeline, cli


def transcript():
    return {'language':'English','segments':[{'start':0,'end':1,'speaker':'SPEAKER_01','text':'I will send the draft.'}],
        'audio_annotations':[{'start':1,'end':1.5,'kind':'pause','description':'A pause','speakers':[]}],
        'uncertainties':[], 'tone':[{'segment_ids':['seg000001'],'label':'neutral','cues':'Even delivery','subjective':True}]}

def analysis():
    return {'summary':[{'text':'A draft will be sent.','segment_ids':['seg000001']}], 'topics':[], 'decisions':[],
        'action_items':[{'task':'Send the draft','owner':None,'deadline':None,'segment_ids':['seg000001']}], 'questions':[], 'speaker_mappings':[]}

def translation():
    return {'target_language':'English','segments':[{'segment_id':'seg000001','text':'I will send the draft.'}]}

def response(data):
    return SimpleNamespace(status='completed', output_text=json.dumps(data))

class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root/'source.wav'
        with wave.open(str(self.audio),'wb') as stream:
            stream.setparams((1,2,8000,0,'NONE','not compressed'))
            stream.writeframes(b'\0\0'*16000)
        self.args = argparse.Namespace(audio=self.audio,output_dir=self.root/'out',model='test-model',language=None,translation_language='English',transcript_only=False,resume=None)
        self.client = MagicMock()
        self.client.files.upload.return_value = SimpleNamespace(name='files/synthetic',uri='fake://audio',state='ACTIVE',mime_type='audio/wav')

    def metadata(self):
        return {'name':self.audio.name,'sha256':bundle.digest(self.audio),'size_bytes':self.audio.stat().st_size,'mime_type':'audio/wav','duration_seconds':2}

    def process(self):
        with patch.object(bundle,'audio_metadata',return_value=self.metadata()):
            return pipeline.process(self.args,self.client)

    def test_complete_evidence_and_private_outputs(self):
        before = self.audio.read_bytes()
        self.client.interactions.create.side_effect = [response(transcript()),response(analysis()),response(translation())]
        directory, manifest = self.process()
        self.assertEqual(manifest['status'],'complete')
        self.assertEqual(len(manifest['artifacts']),8)
        self.assertEqual(directory.stat().st_mode & 0o777,0o700)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.audio.read_bytes(),before)
        self.assertEqual(pipeline.load_resume(directory/'manifest.json')[1],manifest)
        self.assertTrue(all(c.kwargs['store'] is False for c in self.client.interactions.create.call_args_list))
        self.client.files.delete.assert_called_once()

    def test_partial_saved_before_analysis_and_resume_without_upload(self):
        def call(**kwargs):
            if 'audio' in [x['type'] for x in kwargs['input']]:
                return response(transcript())
            saved = list(self.args.output_dir.glob('*/transcript.json'))
            self.assertEqual(len(saved),1)
            if kwargs['response_format'] == bundle.ANALYSIS_SCHEMA:
                error = RuntimeError('SECRET REMOTE CONTENT')
                error.status_code = 503
                raise error
            return response(translation())
        self.client.interactions.create.side_effect = call
        directory,manifest = self.process()
        self.assertEqual(manifest['status'],'partial')
        self.assertEqual(manifest['errors'],[{'stage':'analysis','code':'request_failed','http_status':503}])
        original = (directory/'transcript.json').read_bytes()
        self.args.resume = directory/'manifest.json'
        self.client.reset_mock()
        self.client.interactions.create.side_effect = [response(analysis())]
        directory,manifest = self.process()
        self.assertEqual(manifest['status'],'complete')
        self.assertEqual((directory/'transcript.json').read_bytes(),original)
        self.client.files.upload.assert_not_called()
        self.client.interactions.create.assert_called_once()
        self.assertEqual(manifest['errors'],[])

    def test_invalid_refs_translation_and_duration(self):
        canonical = bundle.validate_transcript(transcript(),2)
        bad = analysis(); bad['summary'][0]['segment_ids']=['seg999999']
        with self.assertRaises(ValueError): bundle.validate_analysis(bad,canonical)
        bad = translation(); bad['segments']=[]
        with self.assertRaises(ValueError): bundle.validate_translation(bad,canonical,'English')
        bad = transcript(); bad['segments'][0]['end']=5
        with self.assertRaises(ValueError): bundle.validate_transcript(bad,2)
        bad = transcript(); bad['tone'][0]['subjective']=False
        with self.assertRaises(ValueError): bundle.validate_transcript(bad,2)

    def test_silent_audio_and_transcript_only(self):
        self.args.transcript_only=True
        self.client.interactions.create.return_value=response({'language':'Unknown','segments':[], 'audio_annotations':[], 'uncertainties':[], 'tone':[]})
        directory,manifest=self.process()
        self.assertEqual(manifest['status'],'complete')
        self.assertEqual(manifest['stages']['analysis'],'skipped')
        self.assertTrue(all(a['size_bytes']>0 for a in manifest['artifacts']))
        self.client.interactions.create.assert_called_once()

    def test_tampered_resume_fails_before_request(self):
        self.args.transcript_only=True
        self.client.interactions.create.return_value=response(transcript())
        directory,_=self.process()
        (directory/'transcript.txt').write_text('tampered')
        self.args.resume=directory/'manifest.json'
        self.client.reset_mock()
        with self.assertRaises(cli.TranscriptionError) as caught: self.process()
        self.assertEqual(caught.exception.code, 'resume_invalid')
        self.client.interactions.create.assert_not_called()

    def test_cli_json_receipt_and_partial_exit(self):
        client=self.client
        client.__enter__.return_value=client
        client.interactions.create.side_effect=[response(transcript()),RuntimeError('SECRET'),response(translation())]
        stdout=io.StringIO()
        with patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic-key'}),patch.object(cli.genai,'Client',return_value=client),patch.object(bundle,'audio_metadata',return_value=self.metadata()),patch('sys.stdout',stdout),patch('sys.stderr',io.StringIO()):
            code=cli.main([str(self.audio),'--json','--output-dir',str(self.args.output_dir)])
        self.assertEqual(code,3)
        receipt=json.loads(stdout.getvalue())
        self.assertEqual(receipt['status'],'partial')
        self.assertNotIn('SECRET',stdout.getvalue())
        self.assertIsNone(client.interactions.sdk_configuration.retry_config)

    def test_ffprobe_argv_and_no_raw_error(self):
        with patch.object(bundle.subprocess,'run',return_value=SimpleNamespace(stdout=b'{"format":{"duration":"2.0"},"streams":[{"codec_type":"audio"}]}')) as run:
            metadata=bundle.audio_metadata(self.audio,'audio/wav')
        self.assertEqual(metadata['duration_seconds'],2)
        self.assertEqual(run.call_args.args[0][-1],str(self.audio.resolve()))
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_complete_resume_never_regenerates_and_lock_blocks_overlap(self):
        self.client.interactions.create.side_effect=[response(transcript()),response(analysis()),response(translation())]
        directory,_=self.process()
        self.args.resume=directory/'manifest.json'
        original=self.args.resume.read_bytes()
        self.client.reset_mock()
        self.process()
        self.client.interactions.create.assert_not_called()
        self.assertEqual(self.args.resume.read_bytes(),original)
        fd=os.open(directory,os.O_RDONLY)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(cli.TranscriptionError) as caught: self.process()
            self.assertEqual(caught.exception.code, 'bundle_in_use')
        finally:
            os.close(fd)

    def test_json_usage_error_does_not_echo_arguments(self):
        stdout=io.StringIO()
        with patch('sys.stdout',stdout),patch('sys.stderr',io.StringIO()):
            code=cli.main(['--json','--secret-token=not-for-logs'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(stdout.getvalue())['status'],'failed')
        self.assertNotIn('not-for-logs',stdout.getvalue())

    def test_metadata_bounds_fail_before_client_initialization(self):
        for flag in ('--model','--language','--translation-language'):
            for value in ('', '   ', 'x'*101):
                with self.subTest(flag=flag,value=value), patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic-key'}), patch.object(cli.genai,'Client') as client, patch('sys.stdout',io.StringIO()), patch('sys.stderr',io.StringIO()):
                    self.assertEqual(cli.main([str(self.audio),flag,value,'--json']),2)
                    client.assert_not_called()

    def test_transcription_schema_bounds_use_actual_duration_without_shared_mutation(self):
        first=bundle.transcript_response_schema(22.5)
        second=bundle.transcript_response_schema(100)
        for field in ('segments','audio_annotations'):
            for timestamp in ('start','end'):
                schema=first['properties'][field]['items']['properties'][timestamp]
                self.assertEqual(schema,{'type':'number','minimum':0,'maximum':22.5})
                self.assertEqual(second['properties'][field]['items']['properties'][timestamp]['maximum'],100)
                self.assertNotIn('maximum',bundle.TRANSCRIPT_SCHEMA['properties'][field]['items']['properties'][timestamp])
        self.client.interactions.create.return_value=response(transcript())
        cli.transcribe(self.client,self.audio,duration=2)
        request=self.client.interactions.create.call_args.kwargs
        self.assertEqual(request['response_format'],bundle.transcript_response_schema(2))
        self.assertIn('never MM.SS notation',request['input'][0]['text'])

    def test_initial_run_holds_resume_lock_during_enrichment(self):
        blocked=[]
        def call(**kwargs):
            if kwargs['response_format'] == bundle.ANALYSIS_SCHEMA:
                manifest_path=next(self.args.output_dir.glob('*/manifest.json'))
                resume_args=argparse.Namespace(**vars(self.args))
                resume_args.resume=manifest_path
                with self.assertRaises(cli.TranscriptionError) as caught:
                    pipeline.process(resume_args,MagicMock())
                self.assertEqual(caught.exception.code, 'bundle_in_use')
                blocked.append(True)
                return response(analysis())
            if kwargs['response_format'] == bundle.TRANSLATION_SCHEMA:
                return response(translation())
            return response(transcript())
        self.client.interactions.create.side_effect=call
        _,manifest=self.process()
        self.assertEqual(manifest['status'],'complete')
        self.assertEqual(blocked,[True])
