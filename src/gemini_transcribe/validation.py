"""Content-free diagnostics: paths use schema fields and array indexes only."""
import json
import re

REASONS = frozenset('''invalid_json invalid_structure invalid_nullable_value
expected_object missing_field unexpected_fields expected_array expected_nonempty_string
expected_finite_number expected_boolean expected_null invalid_enum empty_references
duplicate_references unknown_segment_reference unsupported_schema_version timestamp_order
timestamp_out_of_bounds invalid_local_id duplicate_speakers unknown_speaker
target_language_mismatch translation_segment_count translation_segment_order
invalid_language invalid_timestamp invalid_speaker_label'''.split())
FIELDS = frozenset('''language segments start end speaker text audio_annotations kind
description speakers uncertainties segment_ids tone label cues subjective schema_version
id summary topics title decisions action_items task owner deadline questions question answer
status speaker_mappings name target_language segment_id'''.split())


def valid_diagnostic(value):
    """Also validate diagnostics read from resumable, potentially edited manifests."""
    if not isinstance(value, dict) or set(value) not in (
            {'reason', 'path'}, {'reason', 'path', 'line', 'column'}):
        return False
    if not isinstance(value['reason'], str) or value['reason'] not in REASONS:
        return False
    path = value['path']
    if not isinstance(path, str) or len(path) > 256 or not re.fullmatch(
            r'\$(?:\.[a-z_]+|\[[0-9]{1,10}\])*', path):
        return False
    if any(field not in FIELDS for field in re.findall(r'\.([a-z_]+)', path)):
        return False
    return 'line' not in value or (value['reason'] == 'invalid_json' and all(
        type(value[k]) is int and 0 < value[k] <= 2**63-1 for k in ('line', 'column')))


class ValidationError(ValueError):
    def __init__(self, reason, path='$'):
        super().__init__(f'{reason} at {path}')
        self.reason = reason
        self.path = path


def diagnostic(exc):
    """Never serialize arbitrary exception messages, keys, or rejected values."""
    if isinstance(exc, ValidationError):
        return {'reason': exc.reason, 'path': exc.path}
    if isinstance(exc, json.JSONDecodeError):
        return {'reason': 'invalid_json', 'path': '$',
                'line': exc.lineno, 'column': exc.colno}
    return {'reason': 'invalid_structure', 'path': '$'}
