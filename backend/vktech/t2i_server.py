"""OpenAI-compatible adapter for the local MLX Z-Image CLI."""
from __future__ import annotations
import base64
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from PIL import Image


app=FastAPI(title='Local Z-Image-Turbo adapter',version='1.0')
lock=threading.Lock()
OFFICIAL='Tongyi-MAI/Z-Image-Turbo'


class ImageRequest(BaseModel):
    model: str = OFFICIAL
    prompt: str = Field(min_length=1,max_length=4000)
    size: str = '1024x1024'
    response_format: str = 'b64_json'


@app.get('/health')
def health():
    cli=Path(os.environ.get('T2I_CLI',''))
    return {'status':'ok' if cli.is_file() else 'missing_cli','backend':'ZImageCLI','model':os.environ.get('T2I_LOCAL_MODEL','mzbac/Z-Image-Turbo-8bit'),'requests':'serialized'}


@app.post('/v1/images/generations')
def generate(request: ImageRequest):
    cli=Path(os.environ.get('T2I_CLI',''))
    if not cli.is_file():raise HTTPException(503,'T2I_CLI does not point to ZImageCLI; run scripts/install_zimage.sh')
    try:width,height=(int(x) for x in request.size.lower().split('x',1))
    except Exception as exc:raise HTTPException(422,'size must be WIDTHxHEIGHT') from exc
    if width%64 or height%64 or not (512<=width<=1536 and 512<=height<=1536):raise HTTPException(422,'width and height must be multiples of 64 from 512 to 1536')
    local_model=os.environ.get('T2I_LOCAL_MODEL','mzbac/Z-Image-Turbo-8bit')
    if request.model not in {OFFICIAL,local_model}:raise HTTPException(422,'Unapproved image model')
    prompt=request.prompt+'; editorial educational illustration, clean composition, no labels, no letters, no watermark'
    with lock,tempfile.TemporaryDirectory(prefix='vktech-zimage-') as temp:
        output=Path(temp)/'image.png'
        command=[str(cli),'-p',prompt,'-o',str(output),'-m',local_model,'-W',str(width),'-H',str(height),'-s','9','-g','0','--cache-limit',os.environ.get('T2I_CACHE_LIMIT_MB','4096')]
        try:result=subprocess.run(command,cwd=cli.parent,capture_output=True,text=True,timeout=float(os.environ.get('T2I_TIMEOUT_SECONDS','1800')))
        except subprocess.TimeoutExpired as exc:raise HTTPException(504,'Z-Image generation timed out') from exc
        if result.returncode or not output.exists():raise HTTPException(502,'Z-Image failed: '+(result.stderr or result.stdout)[-500:])
        with Image.open(output) as image:image.verify()
        raw=output.read_bytes()
    return {'created':0,'data':[{'b64_json':base64.b64encode(raw).decode()}]}
