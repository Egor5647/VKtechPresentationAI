import base64
import io
import json

import httpx
import pytest
from PIL import Image

from vktech.contracts import ContextualFailureReport, ImageSelection, PresentationPlan, SlideRevision
from vktech.model import ModelGateway, ModelUnavailable


def gateway(monkeypatch, handler):
    monkeypatch.setenv('POLZA_API_KEY', 'test-key-never-log')
    monkeypatch.setenv('MODEL_CACHE', '0')
    return ModelGateway(httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize('role,schema,payload', [
    ('planning', PresentationPlan, {'status': 'needs_input', 'reason': 'No source', 'slides': []}),
    ('regenerate_slide', SlideRevision, {'title': 'Title', 'message': 'Complete message.'}),
    ('vision_audit', ContextualFailureReport, {'issues': []}),
    ('image_selection', ImageSelection, {'candidates': [{'index': 0, 'score': 90, 'semantic_fit': 90,
        'naturalness': 90, 'composition': 90, 'accepted': True, 'reason': 'Matches the source.'}],
        'selected_index': 0, 'reason': 'Matches the source.'}),
])
def test_all_roles_use_polza_and_vision_data(monkeypatch, tmp_path, role, schema, payload):
    image = tmp_path / 'input.png'; Image.new('RGB', (20, 10), 'blue').save(image)
    def handle(request):
        body = json.loads(request.content)
        assert request.url == 'https://polza.ai/api/v1/chat/completions'
        assert body['model'] == 'qwen/qwen3.8-27b'
        assert body['response_format'] == {'type': 'json_object'}
        transmitted_schema = json.loads(body['messages'][0]['content'].split('Return JSON matching this schema: ')[-1])
        assert transmitted_schema['title'] == schema.__name__
        content = body['messages'][1]['content']
        assert content[1]['image_url']['url'].startswith('data:image/png;base64,')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload)}, 'finish_reason': 'stop'}],
                                       'usage': {'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 16}})
    model = gateway(monkeypatch, handle)
    assert isinstance(model.structured(role, {}, schema, [image]), schema)
    assert model.calls[0]['usage']['total_tokens'] == 16


@pytest.mark.parametrize('status', [400, 401, 402, 403, 404, 429, 500, 503])
def test_api_errors_are_bounded_and_do_not_leak_provider_body(monkeypatch, status):
    requests=[]
    monkeypatch.setattr('vktech.model.time.sleep', lambda _: None)
    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': 'test-key-never-log private upstream body'}})
    model = gateway(monkeypatch, handle)
    with pytest.raises(ModelUnavailable, match=f'HTTP {status}') as caught:
        model.structured('vision_audit', {}, ContextualFailureReport)
    assert len(requests) == (3 if status in {429, 500, 503} else 1)
    assert 'test-key-never-log' not in str(caught.value) + json.dumps(model.calls)
    assert 'private upstream body' not in str(caught.value)


def test_rate_limit_retry_after(monkeypatch):
    requests=[];delays=[]
    monkeypatch.setattr('vktech.model.time.sleep', delays.append)
    def handle(request):
        requests.append(request)
        if len(requests)==1:return httpx.Response(429, headers={'Retry-After': '2'})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"issues":[]}'}}]})
    model=gateway(monkeypatch,handle)
    assert model.structured('vision_audit',{},ContextualFailureReport).issues==[]
    assert len(requests)==2 and delays==[2]


def test_json_mode_still_retries_schema_invalid_output(monkeypatch):
    requests=[]
    def handle(request):
        requests.append(request)
        answer='{"issues":"invalid"}' if len(requests)==1 else '{"issues":[]}'
        return httpx.Response(200,json={'choices':[{'message':{'content':answer}}]})
    model=gateway(monkeypatch,handle)
    assert model.structured('vision_audit',{},ContextualFailureReport).issues==[]
    assert len(requests)==2
    correction=json.loads(requests[-1].content)['messages'][-1]
    assert correction['role']=='user'
    feedback=json.loads(correction['content'])
    assert feedback['validation_errors'][0]['path']=='issues'
    assert feedback['validation_errors'][0]['type']=='list_type'


def test_json_mode_never_accepts_repeated_invalid_schema(monkeypatch):
    model=gateway(monkeypatch,lambda _:httpx.Response(200,json={'choices':[{'message':{'content':'{"issues":"invalid"}'}}]}))
    with pytest.raises(ModelUnavailable,match='issues: list_type'):
        model.structured('vision_audit',{},ContextualFailureReport)
    assert len(model.calls)==2


def test_planning_retries_with_exact_invalid_visual_location(monkeypatch):
    requests=[]
    slide={'id':'s1','title':'Архитектура','message':'Компоненты связаны.',
           'claim_ids':['c1'],'visual':'diagram'}
    def handle(request):
        requests.append(request)
        if len(requests)==2:
            feedback=json.loads(json.loads(request.content)['messages'][-1]['content'])
            issue=feedback['validation_errors'][0]
            assert issue['path']=='slides[0].visual' and issue['type']=='literal_error'
            assert "'hierarchy'" in issue['message']
        answer={'slides':[{**slide,'visual':'diagram' if len(requests)==1 else 'hierarchy'}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)}}]})
    model=gateway(monkeypatch,handle)
    result=model.structured('planning',{'slide_count':1},PresentationPlan)
    assert result.slides[0].visual=='hierarchy'
    assert result.slides[0].claim_ids==['c1']
    assert len(requests)==2
    assert model.calls[0]['validation_errors']==[{'path':'slides[0].visual','type':'literal_error'}]


def test_schema_diagnostics_never_log_response_or_unknown_field_names(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    raw=json.dumps({'issues':'private-source-text','test-key-never-log':'private-source-text'})
    model=gateway(monkeypatch,lambda _:httpx.Response(200,json={'choices':[{'message':{'content':raw}}]}))
    with pytest.raises(ModelUnavailable) as caught:
        model.structured('vision_audit',{},ContextualFailureReport)
    exposed=str(caught.value)+json.dumps(model.calls)+caplog.text
    assert 'private-source-text' not in exposed and 'test-key-never-log' not in exposed
    assert '<unknown_field>' in exposed
    assert not list(tmp_path.rglob('*.json'))


def test_json_syntax_errors_are_reported_to_correction_request(monkeypatch):
    requests=[]
    def handle(request):
        requests.append(request)
        if len(requests)==2:
            issue=json.loads(json.loads(request.content)['messages'][-1]['content'])['validation_errors'][0]
            assert issue['path']=='$' and issue['type']=='json_invalid'
        answer='```json\n{"issues":[]}\n```' if len(requests)==1 else '{"issues":[]}'
        return httpx.Response(200,json={'choices':[{'message':{'content':answer}}]})
    model=gateway(monkeypatch,handle)
    assert model.structured('vision_audit',{},ContextualFailureReport).issues==[]


def test_invalid_model_output_is_retryable_in_worker(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from vktech.store import Store
    from vktech.worker import execute
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'jobs.sqlite'))
    model=gateway(monkeypatch,lambda _:httpx.Response(200,json={'choices':[{'message':{'content':'{"issues":"invalid"}'}}]}))
    monkeypatch.setattr('vktech.worker.Pipeline',lambda *_:SimpleNamespace(
        run=lambda _:model.structured('vision_audit',{},ContextualFailureReport)))
    store=Store()
    try:
        jid=store.enqueue('generate',{})
        execute(store,store.claim(),model)
        job=store.job(jid)
        assert job.state=='awaiting_input'
        assert 'issues: list_type' in job.error
        assert 'ValueError:' not in job.error
        store.retry(jid)
        assert store.job(jid).state=='queued'
    finally:
        store.engine.dispose()


@pytest.mark.parametrize('choice', [
    {'message': {'content': '{"issues":[]}'}, 'finish_reason': 'length'},
    {'message': {'content': None}},
    {'message': {'content': ''}},
    {},
])
def test_incomplete_responses_are_not_cached(monkeypatch, tmp_path, choice):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    model=gateway(monkeypatch,lambda _:httpx.Response(200,json={'choices':[choice]}))
    with pytest.raises(ModelUnavailable):model.structured('vision_audit',{},ContextualFailureReport)
    assert not list(tmp_path.rglob('*.json'))


def test_timeout_does_not_duplicate_paid_request(monkeypatch):
    requests=[]
    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout('test-key-never-log', request=request)
    model=gateway(monkeypatch,handle)
    with pytest.raises(ModelUnavailable) as caught:model.structured('vision_audit',{},ContextualFailureReport)
    assert len(requests)==1 and 'test-key-never-log' not in str(caught.value)


def test_cache_isolated_by_endpoint_reasoning_and_credentials_not_stored(monkeypatch,tmp_path):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"issues":[]}'}}]})
    model=gateway(monkeypatch,handle)
    model.cache_enabled=True
    model.structured('vision_audit',{},ContextualFailureReport)
    model.structured('vision_audit',{},ContextualFailureReport)
    assert len(requests)==1 and model.calls[-1]['provider']=='cache'
    monkeypatch.setenv('MODEL_REASONING_ENABLED','1')
    reasoning_gateway=gateway(monkeypatch,handle);reasoning_gateway.cache_enabled=True
    reasoning_gateway.structured('vision_audit',{},ContextualFailureReport)
    monkeypatch.setenv('MODEL_BASE_URL','https://other.example/v1');monkeypatch.setenv('MODEL_API_KEY','other-key')
    endpoint_gateway=gateway(monkeypatch,handle);endpoint_gateway.cache_enabled=True
    endpoint_gateway.structured('vision_audit',{},ContextualFailureReport)
    assert len(requests)==3
    for file in tmp_path.rglob('*.json'):
        assert 'test-key-never-log' not in file.read_text()


def png_bytes():
    output=io.BytesIO();Image.new('RGB',(16,9),'blue').save(output,'PNG');return output.getvalue()


def test_polza_image_job_polled_and_downloaded_without_credentials(monkeypatch,tmp_path):
    requests=[];raw=png_bytes()
    monkeypatch.setattr('vktech.model.time.sleep',lambda _:None)
    def handle(request):
        requests.append(request)
        if request.url.path=='/api/v1/media':
            body=json.loads(request.content)
            assert body=={'model':'tongyi-mai/z-image','input':{'prompt':'A blue card','aspect_ratio':'16:9'},'async':True}
            return httpx.Response(200,json={'id':'gen_test','status':'pending'})
        if request.url.path=='/api/v1/media/gen_test':
            return httpx.Response(200,json={'id':'gen_test','status':'completed','data':{'url':'https://s3.polza.ai/test.png'}})
        assert request.url=='https://s3.polza.ai/test.png'
        assert 'authorization' not in request.headers
        return httpx.Response(200,content=raw)
    model=gateway(monkeypatch,handle);target=tmp_path/'output.png'
    model.image('A blue card',target)
    assert len(requests)==3
    with Image.open(target) as image:assert image.format=='PNG' and image.size==(16,9)


@pytest.mark.parametrize('status',['failed','cancelled'])
def test_failed_image_never_downloads_or_retries_generation(monkeypatch,tmp_path,status):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'gen_test','status':status,'error':{'message':'test-key-never-log'}})
    model=gateway(monkeypatch,handle)
    with pytest.raises(ModelUnavailable):model.image('A blue card',tmp_path/'output.png')
    assert len(calls)==1 and not (tmp_path/'output.png').exists()


def test_custom_image_api_keeps_base64_support(monkeypatch,tmp_path):
    monkeypatch.setenv('T2I_BASE_URL','https://images.example/v1');monkeypatch.setenv('T2I_API_KEY','image-key')
    monkeypatch.setenv('T2I_MODEL','custom-image-model')
    def handle(request):
        assert request.url=='https://images.example/v1/images/generations'
        assert request.headers['Authorization']=='Bearer image-key'
        assert json.loads(request.content)['response_format']=='b64_json'
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png_bytes()).decode()}]})
    model=gateway(monkeypatch,handle);model.image('A blue card',tmp_path/'output.png')
    assert (tmp_path/'output.png').exists()
