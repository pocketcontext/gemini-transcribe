#!/usr/bin/env python3
"""Check a copied standalone launcher outside the checkout.

By default resolve the working package through uv. --published checks the exact
remote commit pinned in the unmodified launcher. No provider requests are made.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--published',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    content=(root/'skills/gemini-transcribe/gemini-transcribe').read_text()
    pattern=r'gemini-transcribe @ git\+https://github.com/pocketcontext/gemini-transcribe.git@[0-9a-f]{40}'
    assert re.search(pattern,content),'Launcher must pin a full Git commit'
    if not args.published:
        content=re.sub(pattern,'gemini-transcribe @ '+root.as_uri(),content)
    with tempfile.TemporaryDirectory(prefix='gemini-launcher-test-') as folder:
        target=Path(folder)/'gemini-transcribe'
        target.write_text(content); target.chmod(0o755)
        env={k:v for k,v in os.environ.items() if not k.startswith(('GEMINI_','GOOGLE_API_KEY'))}
        run=lambda *a: subprocess.run([shutil.which('uv') or 'uv','run','--script',str(target),*a],cwd=folder,env=env,text=True,capture_output=True,timeout=120)
        help_result=run('--help')
        assert help_result.returncode==0,help_result.stderr
        assert '--json' in help_result.stdout and '--resume' in help_result.stdout
        failed=run('missing.wav','--json')
        assert failed.returncode==2,failed.stderr
        assert json.loads(failed.stdout)['status']=='failed'
    print('PASS copied '+('published' if args.published else 'working-tree')+' launcher: help, argument forwarding and JSON configuration error')

if __name__=='__main__': main()
