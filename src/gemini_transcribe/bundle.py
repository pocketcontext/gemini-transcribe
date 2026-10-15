"""Versioned portable evidence, strict local validation and private exports."""
import hashlib
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from .validation import ValidationError

PREFIX = 'gemini-transcribe.'

def obj(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields)}
def arr(item):
    return {'type': 'array', 'items': item}
STR = {'type': 'string'}
NUM = {'type': 'number'}
REFS = arr(STR)
NULLSTR = {'anyOf': [STR, {'type': 'null'}]}
TRANSCRIPT_SCHEMA = obj(language=STR, segments=arr(obj(start=NUM, end=NUM, speaker=STR, text=STR)),
    audio_annotations=arr(obj(start=NUM, end=NUM, kind={'type':'string','enum':['laughter','pause','overlap','background_noise','other']}, description=STR, speakers=arr(STR))),
    uncertainties=arr(obj(segment_ids=REFS, kind={'type':'string','enum':['wording','speaker','timing','other']}, description=STR)),
    tone=arr(obj(segment_ids=REFS, label=STR, cues=STR, subjective={'type':'boolean','enum':[True]})))
ANALYSIS_SCHEMA = obj(summary=arr(obj(text=STR, segment_ids=REFS)), topics=arr(obj(title=STR,text=STR,segment_ids=REFS)),
    decisions=arr(obj(text=STR,segment_ids=REFS)), action_items=arr(obj(task=STR,owner=NULLSTR,deadline=NULLSTR,segment_ids=REFS)),
    questions=arr(obj(question=STR,answer=NULLSTR,status={'type':'string','enum':['answered','partially_answered','unresolved','unclear']},segment_ids=REFS)),
    speaker_mappings=arr(obj(speaker=STR,name=STR,status={'type':'string','enum':['explicit','tentative']},segment_ids=REFS)))
TRANSLATION_SCHEMA = obj(target_language=STR, segments=arr(obj(segment_id=STR,text=STR)))

def transcript_response_schema(duration):
    """Constrain generated segment and observation times to the actual recording."""
    if isinstance(duration,bool) or not isinstance(duration,(float,int)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid source duration.')
    schema = copy.deepcopy(TRANSCRIPT_SCHEMA)
    for field in ('segments','audio_annotations'):
        properties = schema['properties'][field]['items']['properties']
        for timestamp in ('start','end'):
            properties[timestamp] = {'type':'number','minimum':0,'maximum':duration}
    return schema


def check_schema(value, schema, path='$'):
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try:
                check_schema(value, choice, path)
                return
            except ValidationError:
                pass
        raise ValidationError('invalid_nullable_value', path)
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict):
            raise ValidationError('expected_object', path)
        for key in schema['required']:
            if key not in value:
                raise ValidationError('missing_field', path+'.'+key)
        if set(value) != set(schema['required']):
            # Unknown keys are provider content: never include them in paths.
            raise ValidationError('unexpected_fields', path)
        for key, field in schema['properties'].items():
            check_schema(value[key], field, path+'.'+key)
    elif kind == 'array':
        if not isinstance(value, list):
            raise ValidationError('expected_array', path)
        for index, item in enumerate(value):
            check_schema(item, schema['items'], f'{path}[{index}]')
    elif kind == 'string':
        if not isinstance(value, str) or not value.strip():
            raise ValidationError('expected_nonempty_string', path)
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value):
            raise ValidationError('expected_finite_number', path)
    elif kind == 'boolean':
        if type(value) is not bool:
            raise ValidationError('expected_boolean', path)
    elif kind == 'null' and value is not None:
        raise ValidationError('expected_null', path)
    if 'enum' in schema and value not in schema['enum']:
        raise ValidationError('invalid_enum', path)

def refs(items, ids, path='$'):
    if not items:
        raise ValidationError('empty_references', path)
    if len(items) != len(set(items)):
        raise ValidationError('duplicate_references', path)
    for index, item in enumerate(items):
        if item not in ids:
            raise ValidationError('unknown_segment_reference', f'{path}[{index}]')

def validate_transcript(data, duration):
    from .cli import validate_transcript as base_validate
    if not isinstance(data, dict):
        raise ValidationError('expected_object')
    copy = {k:v for k,v in data.items() if k != 'schema_version'}
    canonical = data.get('schema_version') == PREFIX+'transcript/v1'
    if 'schema_version' in data and not canonical:
        raise ValidationError('unsupported_schema_version', '$.schema_version')
    for field in ('segments','audio_annotations'):
        if field not in copy:
            raise ValidationError('missing_field', '$.'+field)
        if not isinstance(copy[field], list):
            raise ValidationError('expected_array', '$.'+field)
        cleaned = []
        for index, item in enumerate(copy[field]):
            if not isinstance(item, dict):
                raise ValidationError('expected_object', f'$.{field}[{index}]')
            cleaned.append({k:v for k,v in item.items() if k != 'id'})
        copy[field] = cleaned
    check_schema(copy, TRANSCRIPT_SCHEMA)
    base_validate(copy)
    ids = {f'seg{i:06d}' for i in range(1,len(copy['segments'])+1)}
    speakers = {s['speaker'] for s in copy['segments']}
    for field, prefix in [('segments','seg'),('audio_annotations','ann')]:
        for i,item in enumerate(copy[field],1):
            path = f'$.{field}[{i-1}]'
            if not all(0 <= item[t] <= duration+2 for t in ('start', 'end')):
                raise ValidationError('timestamp_out_of_bounds', path)
            expected = f'{prefix}{i:06d}'
            if canonical and data[field][i-1].get('id') != expected:
                raise ValidationError('invalid_local_id', path+'.id')
            item['id'] = expected
    for index, item in enumerate(copy['audio_annotations']):
        path = f'$.audio_annotations[{index}].speakers'
        if len(set(item['speakers'])) != len(item['speakers']):
            raise ValidationError('duplicate_speakers', path)
        for speaker_index, speaker in enumerate(item['speakers']):
            if speaker not in speakers:
                raise ValidationError('unknown_speaker', f'{path}[{speaker_index}]')
    for field in ('uncertainties','tone'):
        for index, item in enumerate(copy[field]):
            refs(item['segment_ids'],ids,f'$.{field}[{index}].segment_ids')
    return {'schema_version':PREFIX+'transcript/v1', **copy}

def exact_metadata(actual, expected):
    """Compare JSON metadata without Python's boolean/number coercion."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            exact_metadata(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            exact_metadata(a, e) for a, e in zip(actual, expected))
    return actual == expected


def timing_review(transcript):
    """Derive content-free review warnings without altering evidence order or IDs."""
    warnings = []
    for field, prefix in [('segments', 'seg'), ('audio_annotations', 'ann')]:
        previous = None
        for index, item in enumerate(transcript.get(field, [])):
            detail = {'path': f'$.{field}[{index}]', 'index': index,
                      'id': f'{prefix}{index+1:06d}', 'start': item['start'], 'end': item['end']}
            if item['start'] > item['end']:
                warnings.append({'reason': 'timestamp_reversed', **detail})
            if field == 'segments' and previous is not None and item['start'] < previous['start']:
                warnings.append({'reason': 'timestamp_backward', **detail,
                    'previous_id': f'seg{index:06d}', 'previous_start': previous['start'],
                    'backward_seconds': previous['start'] - item['start']})
            previous = item
    return {'status': 'required' if warnings else 'not_required', 'warnings': warnings}


def validate_analysis(data, transcript):
    if not isinstance(data, dict):
        raise ValidationError('expected_object')
    copy = {k:v for k,v in data.items() if k != 'schema_version'}
    if data.get('schema_version', PREFIX+'analysis/v1') != PREFIX+'analysis/v1':
        raise ValidationError('unsupported_schema_version', '$.schema_version')
    check_schema(copy, ANALYSIS_SCHEMA)
    ids = {s['id'] for s in transcript['segments']}
    speakers = {s['speaker'] for s in transcript['segments']}
    for field, values in copy.items():
        for index, item in enumerate(values):
            refs(item['segment_ids'],ids,f'$.{field}[{index}].segment_ids')
    for index, item in enumerate(copy['speaker_mappings']):
        if item['speaker'] not in speakers:
            raise ValidationError('unknown_speaker', f'$.speaker_mappings[{index}].speaker')
    return {'schema_version':PREFIX+'analysis/v1', **copy}

def validate_translation(data, transcript, language):
    if not isinstance(data, dict):
        raise ValidationError('expected_object')
    copy = {k:v for k,v in data.items() if k != 'schema_version'}
    if data.get('schema_version', PREFIX+'translation/v1') != PREFIX+'translation/v1':
        raise ValidationError('unsupported_schema_version', '$.schema_version')
    check_schema(copy, TRANSLATION_SCHEMA)
    if copy['target_language'] != language:
        raise ValidationError('target_language_mismatch', '$.target_language')
    if len(copy['segments']) != len(transcript['segments']):
        raise ValidationError('translation_segment_count', '$.segments')
    for index, (translated, original) in enumerate(zip(copy['segments'], transcript['segments'])):
        if translated['segment_id'] != original['id']:
            raise ValidationError('translation_segment_order', f'$.segments[{index}].segment_id')
    return {'schema_version':PREFIX+'translation/v1', **copy}

def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            hasher.update(block)
    return hasher.hexdigest()

def audio_metadata(path, mime):
    result = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(Path(path).resolve())], capture_output=True, timeout=30, check=True)
    metadata = json.loads(result.stdout)
    duration = float(metadata['format']['duration'])
    if not math.isfinite(duration) or duration <= 0 or not any(s.get('codec_type') == 'audio' for s in metadata.get('streams',[])):
        raise ValueError('No finite audio duration.')
    return {'name':Path(path).name,'sha256':digest(path),'size_bytes':Path(path).stat().st_size,'mime_type':mime,'duration_seconds':duration}

def atomic_write(path, content):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.write-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def write_json(path, data):
    atomic_write(path,json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def artifact(directory, name, role, content, media):
    path = directory/name
    atomic_write(path,content)
    return {'role':role,'path':name,'sha256':digest(path),'size_bytes':path.stat().st_size,'media_type':media}

def export_stage(directory, stage, data, transcript=None):
    from .cli import render_txt, render_srt
    entries = [(stage+'.json',stage,json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n','application/json')]
    if stage in ('transcript','translation'):
        rendered = data if stage == 'transcript' else {'segments':[{**s,'text':t['text']} for s,t in zip(transcript['segments'],data['segments'],strict=True)]}
        entries += [(stage+'.txt',stage+'_txt',render_txt(rendered),'text/plain'),(stage+'.srt',stage+'_srt',render_srt(rendered),'application/x-subrip')]
    else:
        lines = ['# Recording analysis','', 'Generated interpretation; verify against the cited transcript segments.','']
        for field, values in data.items():
            if field == 'schema_version':
                continue
            lines += ['## '+field.replace('_',' ').title(),'']
            for value in values:
                lines += ['- '+'; '.join(f'{k}: {v}' for k,v in value.items() if k != 'segment_ids')+' ['+', '.join(value['segment_ids'])+']']
            if not values:
                lines += ['None identified.']
            lines += ['']
        entries += [('analysis.md','analysis_md','\n'.join(lines),'text/markdown')]
    if any(media == 'application/json' and len(content.encode('utf-8')) > 8 * 1024 * 1024
           for _, _, content, media in entries):
        raise ValueError('JSON artifact exceeds the 8 MiB bundle limit.')
    return [artifact(directory,*entry) for entry in entries]
