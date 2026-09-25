#!/usr/bin/env python3
"""Validate Polza.ai credentials and configured model identifiers."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
try:
    import httpx
except ModuleNotFoundError:
    python=ROOT/'.venv/bin/python'
    if python.exists():os.execv(str(python),[str(python),str(Path(__file__).resolve()),*sys.argv[1:]])
    raise

def load_env():
    path=ROOT/'.env'
    if not path.exists():return
    for raw in path.read_text().splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        key,value=line.split('=',1)
        os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--chat',action='store_true',help='also make one minimal billed chat request')
    args=parser.parse_args();load_env()
    key=os.environ.get('POLZA_API_KEY','')
    if not key:
        print('POLZA_API_KEY is empty in .env',file=sys.stderr);return 2
    base=os.environ.get('POLZA_BASE_URL','https://polza.ai/api/v1').rstrip('/')
    headers={'Authorization':'Bearer '+key}
    try:
        with httpx.Client(timeout=httpx.Timeout(90,connect=30)) as client:
            response=client.get(base+'/models',headers=headers)
            if response.status_code!=200:
                print(f'Polza.ai rejected the model catalog request: HTTP {response.status_code}',file=sys.stderr);return 1
            payload=response.json();items=payload.get('data',payload if isinstance(payload,list) else [])
            available={str(item.get('id')) for item in items if isinstance(item,dict)}
            configured=[os.environ.get('POLZA_TEXT_MODEL_QUALITY','qwen/qwen3.8-27b'),os.environ.get('POLZA_TEXT_MODEL_FAST','qwen/qwen3-vl-8b-instruct'),os.environ.get('POLZA_IMAGE_MODEL','qwen/image-2')]
            missing=[model for model in configured if model not in available]
            print(f'Polza.ai connection is ready; catalog contains {len(available)} models.')
            if missing:
                print('Configured model IDs absent from this key catalog: '+', '.join(missing),file=sys.stderr);return 1
            print('Configured text and image models are available.')
            if args.chat:
                model=configured[0]
                # Reasoning-capable models may spend part of the output budget on
                # hidden reasoning before producing the visible answer. Eight
                # tokens can therefore yield a valid HTTP response with empty
                # content, so leave enough room for both parts.
                body={'model':model,'messages':[{'role':'user','content':'Return exactly: OK'}],'temperature':0,'max_tokens':128}
                chat=client.post(base+'/chat/completions',headers={**headers,'Content-Type':'application/json'},json=body)
                if chat.status_code!=200:
                    print(f'Test generation failed: HTTP {chat.status_code}',file=sys.stderr);return 1
                content=chat.json().get('choices',[{}])[0].get('message',{}).get('content','')
                if not str(content).strip():
                    print('Test generation returned no text',file=sys.stderr);return 1
                print('Minimal text generation succeeded.')
    except (httpx.HTTPError,ValueError,json.JSONDecodeError) as exc:
        print('Polza.ai connection failed: '+type(exc).__name__,file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
