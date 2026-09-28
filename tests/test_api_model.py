import json
import pytest
import httpx
from fastapi.testclient import TestClient
from vktech.api import app,store
from vktech.store import Store
from vktech.model import ModelGateway,validate_manifest,model_endpoint,ModelUnavailable
from vktech.contracts import PresentationPlan


def test_api_upload_job_and_artifact_access(template_bytes,content,tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'data'));monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'db.sqlite'));monkeypatch.delenv('POLZA_API_KEY',raising=False);monkeypatch.delenv('MODEL_API_KEY',raising=False);monkeypatch.delenv('T2I_API_KEY',raising=False);store.cache_clear()
    with TestClient(app) as client:
        health=client.get('/api/health').json();assert health['model_mode']=='api';assert health['model_name']=='qwen/qwen3.8-27b'
        assert not health['model_configured'] and not health['image_model_configured']
        monkeypatch.setenv('POLZA_API_KEY','test-polza-key')
        health=client.get('/api/health').json()
        assert health['model_configured'] and health['image_model_configured']
        assert 'test-polza-key' not in json.dumps(health)
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


def test_gateway_schema_retry_and_polza_provider(plan,monkeypatch):
    monkeypatch.setenv('POLZA_API_KEY','test-polza-key')
    monkeypatch.setenv('MODEL_RESPONSE_FORMAT','json_schema')
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}' if len(calls)==1 else plan.model_dump_json()}}]})
    gateway=ModelGateway(httpx.Client(transport=httpx.MockTransport(handle)))
    gateway.cache_enabled=False
    result=gateway.structured('planning',{'brief':'test','slide_count':12},PresentationPlan)
    assert len(result.slides)==12 and len(calls)==2
    assert all(c['provider']=='polza' for c in gateway.calls)
    assert all(str(c.url)=='https://polza.ai/api/v1/chat/completions' for c in calls)
    assert all(c.headers['Authorization']=='Bearer test-polza-key' for c in calls)
    body=json.loads(calls[-1].content)
    assert body['response_format']['type']=='json_schema'
    assert body['model']=='qwen/qwen3.8-27b'
    assert body['reasoning']=={'enabled':False}
    assert body['response_format']['json_schema']['schema']['title']=='PresentationPlan'
    assert body['response_format']['json_schema']['schema']['properties']['slides']['minItems']==12
    assert body['response_format']['json_schema']['schema']['properties']['slides']['maxItems']==12
    slide_schema=body['response_format']['json_schema']['schema']['$defs']['PlanSlide']
    assert slide_schema['properties']['message']['maxLength']==260
    assert slide_schema['properties']['message']['pattern']=='.*[.!?)]$'
    assert {'takeaway','balanced_message','support_points','visual_items'}<=set(slide_schema['required'])
    with pytest.raises(ValueError):validate_manifest({'text':{'parameters':36_000_000_000,'license':'Apache-2.0','open_weights':True}})


def test_remote_model_configuration(monkeypatch):
    monkeypatch.setenv('POLZA_API_KEY','test-polza-key')
    monkeypatch.setenv('MODEL_NAME','qwen3.8-27b')
    endpoint=model_endpoint()
    assert endpoint.model=='qwen/qwen3.8-27b' and endpoint.configured
    assert 'test-polza-key' not in repr(endpoint)
    monkeypatch.setenv('MODEL_BASE_URL','https://custom.example/v1')
    assert not model_endpoint().configured
    monkeypatch.setenv('MODEL_API_KEY','custom-key')
    assert model_endpoint().key=='custom-key'
    monkeypatch.setenv('MODEL_NAME','other-model')
    with pytest.raises(ValueError,match='MODEL_NAME'):model_endpoint()


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
