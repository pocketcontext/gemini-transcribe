"""Versioned, conservative speaker presentation policy (shared bundle contract)."""
import re
import unicodedata

SCHEMA = 'gemini-transcribe.speaker-names/v1'
POLICY = 'explicit-unambiguous/v1'


def text_value(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in value)):
        raise ValueError('Invalid speaker presentation text.')
    return value.strip()


def metadata(overrides=None):
    return {'schema_version': SCHEMA, 'policy': POLICY, 'overrides': overrides or []}


def validate(value, transcript):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'policy', 'overrides'}
            or value['schema_version'] != SCHEMA or value['policy'] != POLICY
            or not isinstance(value['overrides'], list) or len(value['overrides']) > 1000):
        raise ValueError('Invalid speaker name metadata.')
    speakers = {s['speaker'] for s in transcript['segments']} - {'UNKNOWN'}
    seen, names = set(), set()
    for item in value['overrides']:
        if not isinstance(item, dict) or set(item) != {'speaker', 'name', 'reason'}:
            raise ValueError('Invalid speaker override.')
        speaker = item['speaker']
        if not isinstance(speaker, str) or speaker not in speakers or speaker in seen:
            raise ValueError('Unknown or duplicate override speaker.')
        name = text_value(item['name'], 200)
        text_value(item['reason'], 1000)
        if name.casefold() in names:
            raise ValueError('Duplicate override name.')
        seen.add(speaker)
        names.add(name.casefold())
    return value


def resolve(transcript, analysis=None, settings=None):
    if settings is None:
        return {}
    validate(settings, transcript)
    grouped = {}
    for item in (analysis or {}).get('speaker_mappings', []):
        if item['speaker'] != 'UNKNOWN':
            grouped.setdefault(item['speaker'], []).append(item)
    candidates = {}
    for speaker, items in grouped.items():
        try:
            names = [text_value(i['name'], 200) for i in items]
        except ValueError:
            continue
        explicit = [i for i in items if i['status'] == 'explicit']
        if explicit and len({n.casefold() for n in names}) == 1:
            candidates[speaker] = explicit[0]['name'].strip()
    overrides = {i['speaker']: i['name'].strip() for i in settings['overrides']}
    candidates = {s: n for s, n in candidates.items() if s not in overrides}
    counts = {}
    for name in candidates.values():
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    manual_names = {n.casefold() for n in overrides.values()}
    resolved = {s: n for s, n in candidates.items() if counts[n.casefold()] == 1 and n.casefold() not in manual_names}
    return {**resolved, **overrides}


def label(speaker, names=None):
    return f'{names[speaker]} ({speaker})' if names and speaker in names else speaker


def validate_export(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'source_manifest_sha256'}
            or value['schema_version'] != 'gemini-transcribe.export/v1'
            or not isinstance(value['source_manifest_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', value['source_manifest_sha256'])):
        raise ValueError('Invalid export provenance.')
