"""Versioned portable evidence, strict local validation and private exports."""
import hashlib
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

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


def check_schema(value, schema):
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try:
                check_schema(value, choice)
                return
            except ValueError:
                pass
        raise ValueError('Invalid nullable value.')
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['required']):
            raise ValueError('Unexpected or missing object fields.')
        for key, field in schema['properties'].items():
            check_schema(value[key], field)
    elif kind == 'array':
        if not isinstance(value, list):
            raise ValueError('Expected array.')
        for item in value:
            check_schema(item, schema['items'])
    elif kind == 'string':
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Expected nonempty string.')
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value):
            raise ValueError('Expected finite number.')
    elif kind == 'boolean':
        if type(value) is not bool:
            raise ValueError('Expected boolean.')
    elif kind == 'null' and value is not None:
        raise ValueError('Expected null.')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Invalid enumeration.')

def refs(items, ids):
    if not items or len(items) != len(set(items)) or not set(items) <= ids:
        raise ValueError('Invalid evidence references.')

def validate_transcript(data, duration):
    from .cli import validate_transcript as base_validate
    if not isinstance(data, dict):
        raise ValueError('Expected object.')
    copy = {k:v for k,v in data.items() if k != 'schema_version'}
    canonical = data.get('schema_version') == PREFIX+'transcript/v1'
    if 'schema_version' in data and not canonical:
        raise ValueError('Unknown transcript version.')
    if not isinstance(data, dict) or not {'segments','audio_annotations'} <= set(data):
        raise ValueError('Missing transcript arrays.')
    for field in ('segments','audio_annotations'):
        copy[field] = [{k:v for k,v in item.items() if k != 'id'} for item in copy.get(field, [])]
    check_schema(copy, TRANSCRIPT_SCHEMA)
    base_validate(copy)
    ids = {f'seg{i:06d}' for i in range(1,len(copy['segments'])+1)}
    speakers = {s['speaker'] for s in copy['segments']}
    for field, prefix in [('segments','seg'),('audio_annotations','ann')]:
        for i,item in enumerate(copy[field],1):
            if not 0 <= item['start'] <= item['end'] <= duration+2:
                raise ValueError('Timestamp outside source duration.')
            expected = f'{prefix}{i:06d}'
            if canonical and data[field][i-1].get('id') != expected:
                raise ValueError('Invalid local ID.')
            item['id'] = expected
    for item in copy['audio_annotations']:
        if len(set(item['speakers'])) != len(item['speakers']) or not set(item['speakers']) <= speakers:
            raise ValueError('Unknown annotation speaker.')
    for field in ('uncertainties','tone'):
        for item in copy[field]:
            refs(item['segment_ids'],ids)
    return {'schema_version':PREFIX+'transcript/v1', **copy}

def validate_analysis(data, transcript):
    if not isinstance(data, dict):
        raise ValueError('Expected object.')
    copy = {k:v for k,v in data.items() if k != 'schema_version'}
    if data.get('schema_version', PREFIX+'analysis/v1') != PREFIX+'analysis/v1':
        raise ValueError('Unknown analysis version.')
    check_schema(copy, ANALYSIS_SCHEMA)
    ids = {s['id'] for s in transcript['segments']}
    speakers = {s['speaker'] for s in transcript['segments']}
    for values in copy.values():
        for item in values:
            refs(item['segment_ids'],ids)
    for item in copy['speaker_mappings']:
        if item['speaker'] not in speakers:
            raise ValueError('Unknown speaker.')
    return {'schema_version':PREFIX+'analysis/v1', **copy}

def validate_translation(data, transcript, language):
    if not isinstance(data, dict):
        raise ValueError('Expected object.')
    copy = {k:v for k,v in data.items() if k != 'schema_version'}
    if data.get('schema_version', PREFIX+'translation/v1') != PREFIX+'translation/v1':
        raise ValueError('Unknown translation version.')
    check_schema(copy, TRANSLATION_SCHEMA)
    if copy['target_language'] != language or [s['segment_id'] for s in copy['segments']] != [s['id'] for s in transcript['segments']]:
        raise ValueError('Translation must cover every segment in order.')
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
