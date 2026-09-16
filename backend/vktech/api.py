from __future__ import annotations
import json
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from fastapi import FastAPI,UploadFile,HTTPException,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .contracts import GenerateRequest,RepairRequest,DesignIR,ContentIR
from .template import import_template
from .content import import_content
from .settings import artifact_path,ROOT,config
from .store import Store,job_document

@asynccontextmanager
async def lifespan(app):
    store()
    yield

app=FastAPI(title='VK Tech Presentation AI',version='0.1.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=os.environ.get('CORS_ORIGINS','http://localhost:5173').split(','),allow_methods=['GET','POST'],allow_headers=['Content-Type'])


@lru_cache
def store():return Store()


@app.exception_handler(KeyError)
async def missing(request:Request,exc:KeyError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=404,content={'detail':'Record or job not found'})


@app.exception_handler(ValueError)
async def invalid(request:Request,exc:ValueError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422,content={'detail':str(exc)[:500]})


async def uploaded(file):
    chunks=[];size=0;limit=config('pipeline.yaml')['max_upload_bytes']
    while chunk:=await file.read(1024*1024):
        size+=len(chunk)
        if size>limit:raise HTTPException(413,'Upload exceeds 100 MiB')
        chunks.append(chunk)
    if not size:raise ValueError('Empty upload')
    return b''.join(chunks)


@app.get('/api/health')
def health():
    import shutil
    from .model import configured_text_model
    manifest=config('models.yaml');mode=os.environ.get('MODEL_MODE',manifest['text']['default_mode']);selected=configured_text_model(manifest,mode)
    final=os.environ.get('MODEL_PROFILE')=='final';name=os.environ.get('VK_MODEL_NAME' if final else 'MODEL_NAME') or selected['repository']
    return {'status':'ok','profile':os.environ.get('MODEL_PROFILE','selection'),'model_mode':mode,'model_name':name,'model_configured':bool(os.environ.get('VK_BASE_URL' if final else 'MODEL_BASE_URL')),'renderer_available':bool(shutil.which(os.environ.get('SOFFICE','soffice'))),'database':store().engine.dialect.name}


@app.post('/api/templates')
async def template(file:UploadFile):
    data=await uploaded(file);design=import_template(data)
    rid=store().save_record('template',file.filename or 'template.pptx',data,design.model_dump(),'pptx')
    return {'id':rid,'design':design.model_dump()}


@app.get('/api/templates')
def templates():return [{'id':r.id,'name':r.name,'design':json.loads(r.document)} for r in store().records('template')]


@app.post('/api/assets')
async def asset(file:UploadFile):
    import io
    from PIL import Image
    data=await uploaded(file)
    with Image.open(io.BytesIO(data)) as im:
        im.verify();fmt=im.format
    if fmt not in {'PNG','JPEG','WEBP'}:raise ValueError('Asset must be PNG, JPEG or WEBP')
    rid=store().save_record('asset',file.filename or 'image',data,{},fmt.lower())
    return {'id':rid,'path':store().record(rid).path,'media_type':{'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[fmt]}


@app.post('/api/content')
async def content(file:UploadFile):
    data=await uploaded(file);name=file.filename or 'content.txt'
    def save_embedded(raw,asset_name,extension):
        rid=store().save_record('asset',asset_name,raw,{},extension)
        return store().record(rid,'asset').path
    content=import_content(data,name,save_embedded if name.lower().endswith('.pptx') else None)
    allowed={r.path for r in store().records('asset')}
    if any(a.path not in allowed for a in content.assets):raise ValueError('Upload image assets first; use only returned asset paths')
    rid=store().save_record('content',name,data,content.model_dump(),'bin')
    return {'id':rid,'content':content.model_dump()}


@app.get('/api/content')
def contents():return [{'id':r.id,'name':r.name,'content':json.loads(r.document)} for r in store().records('content')]


@app.post('/api/jobs',status_code=202)
def generate(request:GenerateRequest):
    store().record(request.template_id,'template');store().record(request.content_id,'content')
    return {'id':store().enqueue('generate',request.model_dump())}


@app.get('/api/jobs/{jid}')
def job(jid:str):return job_document(store().job(jid))


@app.post('/api/jobs/{jid}/cancel')
def cancel(jid:str):store().cancel(jid);return job(jid)


@app.post('/api/jobs/{jid}/retry',status_code=202)
def retry(jid:str):store().retry(jid);return job(jid)


@app.post('/api/jobs/{jid}/repair',status_code=202)
def repair(jid:str,request:RepairRequest):
    parent=store().job(jid)
    if parent.state!='ready':raise HTTPException(409,'Job is not ready')
    result=json.loads(parent.result);variant=result['variants'].get(request.variant)
    if not variant or variant['version']!=request.expected_version:raise HTTPException(409,'Audit version is stale')
    return {'id':store().enqueue('repair',{'parent_job_id':jid,'request':request.model_dump()},dedup_key=jid+':'+request.idempotency_key)}


def job_files(jid):
    j=store().job(jid)
    if j.state!='ready':raise HTTPException(409,'Exports are not ready')
    result=json.loads(j.result);allowed={result['manifest']}
    for v in result['variants'].values():allowed.update(v[k] for k in ('scene','audit','pptx','pdf','html'));allowed.update(v['previews'])
    return allowed


@app.get('/api/jobs/{jid}/file')
def file(jid:str,path:str):
    if path not in job_files(jid):raise HTTPException(404,'Artifact not found')
    return FileResponse(artifact_path(path),headers={'X-Content-Type-Options':'nosniff'})


if (ROOT/'frontend/dist').exists():app.mount('/',StaticFiles(directory=ROOT/'frontend/dist',html=True),name='frontend')
