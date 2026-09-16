import json
import pytest
import httpx
from fastapi.testclient import TestClient
from vktech.api import app,store
from vktech.store import Store
from vktech.model import ModelGateway,validate_manifest
from vktech.contracts import PresentationPlan


def test_api_upload_job_and_artifact_access(template_bytes,content,tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'data'));monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'db.sqlite'));store.cache_clear()
    with TestClient(app) as client:
        tr=client.post('/api/templates',files={'file':('unknown.pptx',template_bytes)});assert tr.status_code==200,tr.text
        cr=client.post('/api/content',files={'file':('content.json',content.model_dump_json().encode())});assert cr.status_code==200,cr.text
        jr=client.post('/api/jobs',json={'template_id':tr.json()['id'],'content_id':cr.json()['id'],'brief':'Explain sources'});assert jr.status_code==202
        jid=jr.json()['id'];assert client.get('/api/jobs/'+jid).json()['state']=='queued'
        assert client.get('/api/jobs/'+jid+'/file',params={'path':'../../secret'}).status_code==409
        client.post('/api/jobs/'+jid+'/cancel');assert client.get('/api/jobs/'+jid).json()['state']=='cancelled'
        unsafe=content.model_copy(deep=True)
        from vktech.contracts import Asset
        unsafe.assets=[Asset(id='a',path='secret.txt',description='image',source='local')]
        assert client.post('/api/content',files={'file':('unsafe.json',unsafe.model_dump_json().encode())}).status_code==422
    store.cache_clear()


def test_gateway_schema_retry_and_final_provider(plan,monkeypatch):
    monkeypatch.setenv('MODEL_PROFILE','final');monkeypatch.setenv('VK_BASE_URL','https://vk.example/v1');monkeypatch.setenv('VK_MODEL_NAME','Qwen3.8-27B')
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}' if len(calls)==1 else plan.model_dump_json()}}]})
    gateway=ModelGateway(httpx.Client(transport=httpx.MockTransport(handle)))
    result=gateway.structured('planning',{'brief':'test'},PresentationPlan)
    assert len(result.slides)==12 and len(calls)==2
    assert all(c['provider']=='vk' for c in gateway.calls)
    assert all(str(c.url)=='https://vk.example/v1/chat/completions' for c in calls)
    with pytest.raises(ValueError):validate_manifest({'text':{'parameters':36_000_000_000,'license':'Apache-2.0','open_weights':True}})


@pytest.mark.skipif(not __import__('os').environ.get('TEST_DATABASE_URL'),reason='PostgreSQL test database not configured')
def test_postgresql_skip_locked_claim(monkeypatch,tmp_path):
    import os
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv('DATA_DIR',str(tmp_path));s=Store(os.environ['TEST_DATABASE_URL'])
    jids={s.enqueue('generate',{'test':i}) for i in range(4)}
    with ThreadPoolExecutor(max_workers=4) as pool:claimed=list(pool.map(lambda _:s.claim(),range(4)))
    assert {j.id for j in claimed}==jids
    assert len({j.lease_owner for j in claimed})==4
    for j in claimed:s.cancel(j.id)
