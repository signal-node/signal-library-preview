"""CIでは実際のPR/push範囲、ローカルでは未コミット差分を検査する。"""

import json
import os
from pathlib import Path
import re
import subprocess


def checked(args):
    return subprocess.run(['git',*args],check=True,capture_output=True,text=True).stdout.strip()


def check(event_name=None,event_path=None):
    event_name=event_name or os.environ.get('GITHUB_EVENT_NAME')
    event_path=event_path or os.environ.get('GITHUB_EVENT_PATH')
    if not event_name or not event_path:
        checked(['diff','--check']);return 'working-tree'
    event=json.loads(Path(event_path).read_text(encoding='utf-8'))
    if event_name=='pull_request':
        base=event['pull_request']['base']['sha'];head=event['pull_request']['head']['sha']
    elif event_name=='push':base=event['before'];head=event['after']
    else:
        checked(['show','--format=','--check','HEAD']);return 'HEAD-commit'
    if not all(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',value) for value in (base,head)):
        raise ValueError('CI差分のcommit IDが不正です。')
    checked(['rev-parse','--verify',head+'^{commit}'])
    if set(base)=={'0'}:
        checked(['diff-tree','--root','--check','-r',head]);return 'initial-push'
    checked(['rev-parse','--verify',base+'^{commit}'])
    checked(['diff','--check',base,head]);return 'commit-range'


if __name__=='__main__':print('whitespace check: '+check())
