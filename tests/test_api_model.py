import json
import pytest
import httpx
from fastapi.testclient import TestClient
from vktech.api import app,store
from vktech.store import Store
from vktech.model import ModelGateway,validate_manifest,configured_text_model
from vktech.contracts import PresentationPlan


def test_api_upload_job_and_artifact_access(template_bytes,content,tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'data'));monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'db.sqlite'));monkeypatch.setenv('MODEL_MODE','quality');store.cache_clear()
    with TestClient(app) as client:
        health=client.get('/api/health').json();assert health['model_mode']=='quality';assert 'Ministral-3-14B' in health['model_name']
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
    monkeypatch.setenv('MODEL_PROFILE','final');monkeypatch.setenv('MODEL_MODE','fast');monkeypatch.setenv('VK_BASE_URL','https://vk.example/v1');monkeypatch.setenv('VK_MODEL_NAME','mlx-community/Qwen3-VL-4B-Instruct-4bit')
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}' if len(calls)==1 else plan.model_dump_json()}}]})
    gateway=ModelGateway(httpx.Client(transport=httpx.MockTransport(handle)))
    result=gateway.structured('planning',{'brief':'test','slide_count':12},PresentationPlan)
    assert len(result.slides)==12 and len(calls)==2
    assert all(c['provider']=='vk' for c in gateway.calls)
    assert all(str(c.url)=='https://vk.example/v1/chat/completions' for c in calls)
    body=json.loads(calls[-1].content)
    assert body['response_format']['type']=='json_schema'
    assert body['response_format']['json_schema']['schema']['title']=='PresentationPlan'
    assert body['response_format']['json_schema']['schema']['properties']['slides']['minItems']==12
    assert body['response_format']['json_schema']['schema']['properties']['slides']['maxItems']==12
    with pytest.raises(ValueError):validate_manifest({'text':{'parameters':36_000_000_000,'license':'Apache-2.0','open_weights':True}})


def test_model_modes(monkeypatch):
    monkeypatch.setenv('MODEL_BASE_URL','http://127.0.0.1:8001/v1')
    monkeypatch.delenv('MODEL_NAME',raising=False)
    monkeypatch.setenv('MODEL_MODE','quality')
    quality=ModelGateway(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    assert quality.model=='mlx-community/Ministral-3-14B-Instruct-2512-4bit'
    assert quality.model_config['parameters']==13_945_032_240
    monkeypatch.setenv('MODEL_MODE','fast')
    fast=ModelGateway(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    assert fast.model=='mlx-community/Qwen3-VL-4B-Instruct-4bit'
    assert configured_text_model(fast.manifest,'fast')['parameters']==4_437_815_808
    monkeypatch.setenv('MODEL_MODE','unknown')
    with pytest.raises(ValueError,match='Invalid MODEL_MODE'):
        ModelGateway(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))


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
