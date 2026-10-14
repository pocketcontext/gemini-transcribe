"""Independent manual-continuation tests; all audio/responses are synthetic."""
import base64
from importlib.metadata import version
import time
import signal
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import httpx
from google import genai
from google.genai import types
from gemini_transcribe import cli, pipeline
from gemini_transcribe.long_decode import LongDecodeClient
import test_bundle


def hop(text='', reason='STOP', token=None, thoughts=0, output=1):
    return NS(candidates=[NS(finish_reason=reason, continuation_token=token,
        content=NS(parts=[NS(text='PRIVATE THINKING', thought=True), NS(text=text, thought=False)]))],
        usage_metadata=NS(prompt_token_count=100, cached_content_token_count=95,
            candidates_token_count=output, thoughts_token_count=thoughts, total_token_count=100+thoughts+output))


SDK_READY=tuple(map(int,version('google-genai').split('.')[:3])) >= (2,30,1)

@unittest.skipUnless(SDK_READY,'optional prototype requires isolated SDK 2.30.1')
class LongDecodeTests(unittest.TestCase):
    def adapter(self, responses, **limits):
        client = MagicMock()
        client.models.generate_content.side_effect = responses
        return LongDecodeClient(client, **limits)

    def call(self, adapter):
        return adapter.create(model='gemini-4-argon', input=[{'type':'text','text':'Original only'}],
                              response_format={'type':'object'}, store=False)

    def test_blocked_response_retains_reported_billable_usage(self):
        response = hop()
        response.candidates = []
        adapter = self.adapter([response])
        with self.assertRaises(cli.TranscriptionError):
            self.call(adapter)
        self.assertEqual(adapter.events[0]['finish_reason'], 'OTHER')
        self.assertEqual(adapter.events[0]['usage']['prompt_token_count'],100)
        self.assertEqual(adapter.events[0]['usage']['candidates_token_count'],1)

    def test_split_json_thinking_only_latest_token_and_fixed_request(self):
        adapter=self.adapter([hop('', 'CONTINUATION',b'PRIVATE TOKEN1', thoughts=7,output=0),
                              hop('{"ok":','CONTINUATION',b'PRIVATE TOKEN2'), hop('true}')])
        result=self.call(adapter)
        self.assertEqual(json.loads(result['output_text']),{'ok':True})
        calls=adapter.client.models.generate_content.call_args_list
        original=calls[0].kwargs['contents']
        for call,token in zip(calls,[None,b'PRIVATE TOKEN1',b'PRIVATE TOKEN2']):
            config=call.kwargs['config']
            self.assertEqual(call.kwargs['contents'],original)
            self.assertEqual(config.continuation_token,token)
            self.assertEqual(config.max_output_tokens,131072)
            self.assertFalse(config.automatic_continuation)
            self.assertEqual(config.http_options.retry_options.attempts,1)
            self.assertEqual(config.thinking_config.thinking_level,'LOW')
            self.assertIsNone(config.thinking_config.thinking_budget)
        encoded=json.dumps(adapter.events)
        self.assertNotIn('PRIVATE',encoded)
        self.assertEqual(sum(e['usage']['thoughts_token_count'] for e in adapter.events),7)
        self.assertEqual(sum(e['usage']['cached_content_token_count'] for e in adapter.events),285)
        self.assertEqual(adapter.events[0]['visible_bytes'],0)

    def test_missing_and_repeated_token_stop_without_extra_call(self):
        for responses,count in [([hop('','CONTINUATION')],1),
                                ([hop('','CONTINUATION','same'),hop('','CONTINUATION','same')],2)]:
            with self.subTest(count=count):
                adapter=self.adapter(responses)
                with self.assertRaises(cli.TranscriptionError) as caught:self.call(adapter)
                self.assertEqual(caught.exception.code,'incomplete_response')
                self.assertEqual(adapter.client.models.generate_content.call_count,count)

    def test_max_tokens_never_raises_budget_even_with_token(self):
        adapter=self.adapter([hop('{}','MAX_TOKENS','resume-possible')])
        with self.assertRaises(cli.TranscriptionError) as caught:self.call(adapter)
        self.assertEqual(caught.exception.code,'incomplete_response')
        self.assertEqual(adapter.client.models.generate_content.call_count,1)

    def test_deadline_before_request_and_after_response(self):
        for values,expected_calls in [([0,2],0),([0,0,0,2,2],1)]:
            with self.subTest(values=values):
                adapter=self.adapter([hop('{}')],deadline_seconds=1,clock=MagicMock(side_effect=values))
                with self.assertRaises(cli.TranscriptionError) as caught:self.call(adapter)
                self.assertEqual(caught.exception.code,'request_timeout')
                self.assertEqual(adapter.client.models.generate_content.call_count,expected_calls)

    def test_hard_wall_timeout_restores_signal_and_deletes_upload(self):
        client=MagicMock()
        client.files.upload.return_value=NS(name='files/synthetic',uri='fake://audio',state='ACTIVE',mime_type='audio/wav')
        client.models.generate_content.side_effect=lambda **kwargs: (time.sleep(0.1),hop('{}'))[1]
        adapter=LongDecodeClient(client,deadline_seconds=0.01)
        handler=signal.getsignal(signal.SIGALRM)
        with self.assertRaises(cli.TranscriptionError) as caught:
            cli.transcribe(adapter,'synthetic.wav',model='gemini-4-argon')
        self.assertEqual(caught.exception.code,'request_timeout')
        self.assertEqual(signal.getsignal(signal.SIGALRM),handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))
        client.files.delete.assert_called_once()
        self.assertEqual(client.models.generate_content.call_count,1)

    def test_existing_alarm_fails_before_provider_without_altering_timer(self):
        adapter=self.adapter([hop('{}')])
        handler=signal.getsignal(signal.SIGALRM)
        prior_timer=signal.getitimer(signal.ITIMER_REAL)
        self.assertEqual(prior_timer,(0.0,0.0))
        signal.setitimer(signal.ITIMER_REAL,60)
        try:
            with self.assertRaises(cli.TranscriptionError):self.call(adapter)
            self.assertEqual(signal.getsignal(signal.SIGALRM),handler)
            delay,interval=signal.getitimer(signal.ITIMER_REAL)
            self.assertGreater(delay,59)
            self.assertEqual(interval,0)
            adapter.client.models.generate_content.assert_not_called()
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,handler)

    def test_timeout_is_bounded_by_remaining_deadline(self):
        adapter=self.adapter([hop('{}')],deadline_seconds=10,clock=lambda:0)
        self.call(adapter)
        self.assertEqual(adapter.client.models.generate_content.call_args.kwargs['config'].http_options.timeout,10000)

    def test_hop_and_token_budget_bound_even_if_provider_continues(self):
        for limits,response in [({'max_hops':1},hop('','CONTINUATION','next')),
                                ({'max_output_tokens':3},hop('{}',thoughts=3,output=1))]:
            adapter=self.adapter([response],**limits)
            with self.assertRaises(cli.TranscriptionError):self.call(adapter)
            self.assertEqual(adapter.client.models.generate_content.call_count,1)

    def test_exact_budget_continuation_stops_but_stop_succeeds(self):
        for reason in ('CONTINUATION','STOP'):
            with self.subTest(reason=reason):
                adapter=self.adapter([hop('{}',reason,'next',thoughts=2,output=1)],max_output_tokens=3)
                if reason=='CONTINUATION':
                    with self.assertRaises(cli.TranscriptionError) as caught:self.call(adapter)
                    self.assertEqual(caught.exception.code,'incomplete_response')
                else:
                    self.assertEqual(self.call(adapter)['output_text'],'{}')
                self.assertEqual(adapter.client.models.generate_content.call_count,1)

    def test_real_sdk_transport_503_is_not_retried(self):
        requests=[]
        def unavailable(request):
            requests.append(request)
            return httpx.Response(503,json={'error':{'message':'PRIVATE PROVIDER ERROR'}})
        with httpx.Client(transport=httpx.MockTransport(unavailable)) as http:
            with genai.Client(api_key='synthetic',http_options=types.HttpOptions(httpx_client=http)) as client:
                adapter=LongDecodeClient(client)
                with self.assertRaises(cli.TranscriptionError) as caught:self.call(adapter)
        self.assertEqual(len(requests),1)
        self.assertNotIn('PRIVATE',str(caught.exception))
        self.assertNotIn('PRIVATE',json.dumps(adapter.events))

    def test_real_sdk_manual_continuation_does_not_hide_hops(self):
        requests=[]
        def response(request):
            requests.append(json.loads(request.content))
            first=len(requests)==1
            candidate={'content':{'parts':[{'text':'{"a":' if first else '1}'}]},
                       'finishReason':'CONTINUATION' if first else 'STOP'}
            if first:candidate['continuationToken']=base64.b64encode(b'opaque').decode()
            return httpx.Response(200,json={'candidates':[candidate]})
        with httpx.Client(transport=httpx.MockTransport(response)) as http:
            with genai.Client(api_key='synthetic',http_options=types.HttpOptions(httpx_client=http)) as client:
                adapter=LongDecodeClient(client)
                self.assertEqual(json.loads(self.call(adapter)['output_text']),{'a':1})
        self.assertEqual(len(requests),2)
        self.assertEqual(len(adapter.events),2)
        self.assertEqual(requests[0]['contents'],requests[1]['contents'])
        self.assertEqual(requests[1]['continuationToken'],base64.b64encode(b'opaque').decode())


@unittest.skipUnless(SDK_READY,'optional prototype requires isolated SDK 2.30.1')
class LongDecodeBundleTests(unittest.TestCase):
    setUp=test_bundle.BundleTests.setUp
    metadata=test_bundle.BundleTests.metadata
    process=test_bundle.BundleTests.process

    def test_full_bundle_hashes_and_cleanup_after_fragmented_generation(self):
        underlying=self.client
        transcript=json.dumps(test_bundle.transcript())
        underlying.models.generate_content.side_effect=[hop(transcript[:30],'CONTINUATION','next'),hop(transcript[30:]),
            hop(json.dumps(test_bundle.analysis())),hop(json.dumps(test_bundle.translation()))]
        self.client=LongDecodeClient(underlying)
        self.args.model='gemini-4-argon'
        directory,manifest=self.process()
        self.assertEqual(manifest['status'],'complete')
        self.assertEqual(set(manifest['extraction']),{'provider','model','package_version','prompt_version','store'})
        self.assertEqual(manifest['configuration']['generation']['api'],'generate_content')
        self.assertEqual(pipeline.load_resume(directory/'manifest.json')[1],manifest)
        self.assertEqual(directory.stat().st_mode & 0o777,0o700)
        for file in directory.iterdir():self.assertEqual(file.stat().st_mode & 0o777,0o600)
        underlying.files.delete.assert_called_once()

    def test_incomplete_transcription_never_becomes_complete_and_deletes_upload(self):
        underlying=self.client
        underlying.models.generate_content.side_effect=[hop('{}','MAX_TOKENS','still-resumable')]
        self.client=LongDecodeClient(underlying)
        self.args.model='gemini-4-argon'
        directory,manifest=self.process()
        self.assertEqual(manifest['status'],'failed')
        self.assertFalse((directory/'transcript.json').exists())
        underlying.files.delete.assert_called_once()

    def test_resume_rejects_api_or_budget_changes_before_generation(self):
        underlying=self.client
        underlying.models.generate_content.side_effect=[hop(json.dumps(test_bundle.transcript())),
            hop(json.dumps(test_bundle.analysis())),hop(json.dumps(test_bundle.translation()))]
        self.client=LongDecodeClient(underlying)
        self.args.model='gemini-4-argon'
        directory,_=self.process()
        self.args.resume=directory/'manifest.json'
        underlying.reset_mock()
        for replacement in (LongDecodeClient(underlying,max_output_tokens=262144),underlying):
            self.client=replacement
            with self.assertRaises(cli.TranscriptionError) as caught:self.process()
            self.assertEqual(caught.exception.code,'resume_invalid')
        underlying.models.generate_content.assert_not_called()
        underlying.interactions.create.assert_not_called()
        underlying.files.upload.assert_not_called()

    def test_stop_with_malformed_json_is_failed_and_cleaned_up(self):
        underlying=self.client
        underlying.models.generate_content.side_effect=[hop('{"truncated":')]
        self.client=LongDecodeClient(underlying)
        self.args.model='gemini-4-argon'
        directory,manifest=self.process()
        self.assertEqual(manifest['status'],'failed')
        self.assertFalse((directory/'transcript.json').exists())
        underlying.files.delete.assert_called_once()
