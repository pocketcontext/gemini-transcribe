"""Opt-in bounded GenerateContent continuation adapter.

Requires google-genai 2.30.1 or newer. Continuation tokens stay
in memory; the event journal contains counts and finish reasons only.
"""
import copy
from contextlib import contextmanager
import signal
import threading
import math
from importlib.metadata import version
import time

from google.genai import types

from .cli import TranscriptionError, _get
from .pipeline import safe_error


@contextmanager
def wall_timeout(seconds):
    """POSIX main-thread deadline; refuse competing timers and restore handler."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, 'setitimer'):
        raise ValueError('Prototype requires a POSIX main-thread runner.')
    prior_handler = signal.getsignal(signal.SIGALRM)
    prior_delay, prior_interval = signal.getitimer(signal.ITIMER_REAL)
    if prior_delay or prior_interval:
        raise ValueError('Prototype cannot run under an active process alarm.')
    def expired(signum, frame):
        raise TimeoutError('Long-decode wall deadline reached.')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior_handler)


class LongDecodeClient:
    def __init__(self, client, *, max_output_tokens=131072, deadline_seconds=1800,
                 request_timeout_seconds=1200, max_hops=16, clock=time.monotonic,
                 on_event=None):
        if (type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 1048576
                or type(max_hops) is not int or not 1 <= max_hops <= 64
                or any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0
                       for n in (deadline_seconds, request_timeout_seconds))):
            raise ValueError('Invalid long-decode limits.')
        if tuple(int(n) for n in version('google-genai').split('.')[:3]) < (2, 30, 1):
            raise ValueError('The prototype requires google-genai 2.30.1 or newer.')
        self.client = client
        self.files = client.files
        self.interactions = self
        self.max_output_tokens = max_output_tokens
        self.deadline_seconds = deadline_seconds
        self.request_timeout_seconds = min(request_timeout_seconds, 1200)
        self.max_hops = max_hops
        self.clock = clock
        self.on_event = on_event
        self.events = []
        self.calls = 0
        self.extraction_metadata = {
            'schema_version': 'gemini-transcribe.generation/v1',
            'pipeline': 'long-decode', 'api': 'generate_content',
            'sdk_version': version('google-genai'), 'settings': {
            'max_output_tokens': max_output_tokens, 'thinking_level': 'low',
            'automatic_continuation': False, 'automatic_retries': False,
            'deadline_seconds': deadline_seconds,
            'request_timeout_seconds': self.request_timeout_seconds, 'max_hops': max_hops,
        }}

    def emit(self, event):
        self.events.append(event)
        if self.on_event:
            self.on_event(copy.deepcopy(event))

    def create(self, *, model, input, response_format, store=False):
        if store is not False or model != 'gemini-4-argon':
            raise ValueError('Prototype requires Argon and no stored interactions.')
        parts = []
        for item in input:
            if item['type'] == 'text':
                parts.append(types.Part.from_text(text=item['text']))
            elif item['type'] == 'audio':
                parts.append(types.Part.from_uri(file_uri=item['uri'], mime_type=item['mime_type']))
            else:
                raise ValueError('Unsupported prototype input.')
        contents = [types.Content(role='user', parts=parts)]
        self.calls += 1
        call = self.calls
        started = self.clock()
        deadline = started + self.deadline_seconds
        text_parts = []
        token = None
        seen_tokens = set()
        output_bytes = 0
        usage_total = 0
        for hop in range(1, self.max_hops + 1):
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TranscriptionError('Long-decode deadline reached.', 'request_timeout')
            config = types.GenerateContentConfig(
                max_output_tokens=self.max_output_tokens,
                thinking_config=types.ThinkingConfig(thinking_level='low'),
                automatic_continuation=False, continuation_token=token,
                response_mime_type='application/json',
                response_json_schema=copy.deepcopy(response_format),
                http_options=types.HttpOptions(
                    timeout=max(1, int(min(remaining, self.request_timeout_seconds) * 1000)),
                    retry_options=types.HttpRetryOptions(attempts=1)),
            )
            hop_started = self.clock()
            try:
                with wall_timeout(min(remaining, self.request_timeout_seconds)):
                    response = self.client.models.generate_content(
                        model=model, contents=copy.deepcopy(contents), config=config)
            except Exception as exc:
                error = safe_error(exc, 'generation')
                self.emit({'call': call, 'hop': hop, 'status': 'failed',
                           'error_code': error.code, 'elapsed_seconds': self.clock()-hop_started})
                raise error from None
            candidates = _get(response, 'candidates') or []
            candidate = candidates[0] if len(candidates) == 1 else None
            reason = _get(candidate, 'finish_reason')
            reason = _get(reason, 'value', reason)
            if reason not in ('STOP', 'CONTINUATION', 'MAX_TOKENS'):
                reason = 'OTHER'
            usage = _get(response, 'usage_metadata')
            counts = {}
            for name in ('prompt_token_count', 'cached_content_token_count',
                         'candidates_token_count', 'thoughts_token_count',
                         'total_token_count', 'initial_prompt_token_count'):
                count = _get(usage, name)
                if type(count) is int and count >= 0:
                    counts[name] = count
            if candidate is None:
                self.emit({'call': call, 'hop': hop, 'status': 'received',
                           'finish_reason': 'OTHER', 'usage': counts,
                           'elapsed_seconds': self.clock()-hop_started})
                raise TranscriptionError('Missing or ambiguous candidate.', 'incomplete_response')
            usage_total += counts.get('candidates_token_count', 0) + counts.get('thoughts_token_count', 0)
            fragment = ''.join(_get(p, 'text') or '' for p in
                               (_get(_get(candidate, 'content'), 'parts') or [])
                               if not _get(p, 'thought', False))
            output_bytes += len(fragment.encode('utf-8'))
            next_token = _get(candidate, 'continuation_token')
            self.emit({'call': call, 'hop': hop, 'status': 'received',
                       'finish_reason': reason, 'usage': counts,
                       'visible_bytes': len(fragment.encode('utf-8')),
                       'has_continuation_token': bool(next_token),
                       'elapsed_seconds': self.clock()-hop_started})
            if self.clock() >= deadline:
                raise TranscriptionError('Long-decode deadline reached.', 'request_timeout')
            if output_bytes > 8 * 1024 * 1024 or usage_total > self.max_output_tokens:
                raise TranscriptionError('Long-decode budget exhausted.', 'incomplete_response')
            text_parts.append(fragment)
            if reason == 'STOP':
                return {'status': 'completed', 'output_text': ''.join(text_parts)}
            # MAX_TOKENS can be resumed with a raised limit, but this prototype
            # deliberately never increases the authorized spending budget.
            if usage_total >= self.max_output_tokens or reason != 'CONTINUATION' or not isinstance(next_token, (str, bytes)) or not next_token or next_token in seen_tokens:
                raise TranscriptionError('Long-decode response incomplete.', 'incomplete_response')
            seen_tokens.add(next_token)
            token = next_token
        raise TranscriptionError('Long-decode hop limit reached.', 'incomplete_response')
