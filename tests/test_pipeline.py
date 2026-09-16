import json
import shutil
import pytest
from pptx import Presentation
from vktech.template import import_template
from vktech.planning import build_scenes,validate_plan
from vktech.export import export_pptx,export_html
from vktech.opc import Package,NS
from vktech.audit import audit_scene,repair_scene,contrast
from vktech.contracts import RepairRequest
from vktech.store import Store,Job
from vktech.worker import execute
from conftest import FixtureGateway


def test_three_variants_native_objects(template_bytes,content,plan,tmp_path):
    design=import_template(template_bytes);validate_plan(plan,content,12)
    scenes=build_scenes(design,content,plan,'test')
    required={c.id for c in content.claims}
    for scene in scenes:
        assert {cid for s in scene.slides for n in s.nodes for cid in n.claim_ids}==required
        file=tmp_path/(scene.variant+'.pptx');export_pptx(template_bytes,design,scene,file)
        prs=Presentation(file);assert len(prs.slides)==12
        pkg=Package(file.read_bytes());native=[s for s in prs.slides for sh in s.shapes if sh.has_chart or sh.has_table]
        assert len(native)==1
        assert any('embeddings/' in p for p in pkg.parts) or scene.variant=='C'
        if scene.variant!='A':
            assert any('diagrams/' in p and p.endswith('_data.xml') for p in pkg.parts)
            assert any(s.xpath('.//dgm:relIds',namespaces=NS) for s in [pkg.root(p) for p in pkg.slides()])
        html=tmp_path/(scene.variant+'.html');export_html(scene,html)
        text=html.read_text();assert '<svg' in text or '<table' in text
        assert 'aria-label="Навигация"' in text and 'ArrowRight' in text
    assert len({tuple(s.prototype_id for s in sc.slides) for sc in scenes})==3


def test_plan_cannot_drop_facts_or_invent_numbers(content,plan):
    broken=plan.model_copy(deep=True);broken.slides[-1].claim_ids=[]
    with pytest.raises(ValueError,match='omits'):validate_plan(broken,content,12)
    broken=plan.model_copy(deep=True);broken.slides[0].title='Рост 999%'
    with pytest.raises(ValueError,match='numbers'):validate_plan(broken,content,12)


def test_selected_repair_is_versioned(template_bytes,content,plan):
    design=import_template(template_bytes);scene=build_scenes(design,content,plan,'repair')[0]
    first=scene.slides[0].nodes[0];second=scene.slides[1].nodes[0];first.box.x=-.2;second.box.x=-.3
    report=audit_scene(scene,design,content)
    selected=next(i for i in report.issues if i.rule=='D01' and i.element_ids==[first.id])
    request=RepairRequest(variant='A',expected_version=1,issue_ids=[selected.id],idempotency_key='repair-test')
    new,patch=repair_scene(scene,report,request,design)
    assert new.version==2 and new.slides[0].nodes[0].box.x==0
    assert new.slides[1].nodes[0].box.x==-.3 and first.box.x==-.2
    with pytest.raises(ValueError,match='Stale'):repair_scene(new,report,request,design)
    assert contrast('FFFFFF','000000')==pytest.approx(21)


def test_job_lease_recovery_and_idempotency(tmp_path):
    store=Store('sqlite:///'+str(tmp_path/'db.sqlite'))
    jid=store.enqueue('generate',{'test':True},'test-key');assert store.enqueue('generate',{'test':True},'test-key')==jid
    with pytest.raises(ValueError):store.enqueue('generate',{'test':False},'test-key')
    job=store.claim();assert job.id==jid and store.claim() is None
    store.owned_update(jid,job.lease_owner,lease_until=0)
    recovered=store.claim();assert recovered.lease_owner!=job.lease_owner
    with pytest.raises(RuntimeError):store.owned_update(jid,job.lease_owner,stage='old')
    store.cancel(jid)
    with pytest.raises(RuntimeError):store.owned_update(jid,recovered.lease_owner,stage='old')


def test_missing_inference_is_explicit(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path));monkeypatch.delenv('MODEL_BASE_URL',raising=False)
    from vktech.model import ModelGateway,ModelUnavailable
    from vktech.contracts import PresentationPlan
    with pytest.raises(ModelUnavailable):ModelGateway().structured('planning',{},PresentationPlan)


@pytest.mark.skipif(not shutil.which('soffice') or not shutil.which('pdftoppm'),reason='render tools not configured')
def test_end_to_end_worker(template_bytes,content,plan,tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'artifacts'));store=Store('sqlite:///'+str(tmp_path/'db.sqlite'))
    design=import_template(template_bytes)
    tid=store.save_record('template','unknown.pptx',template_bytes,design.model_dump(),'pptx')
    cid=store.save_record('content','fixture',content.model_dump_json().encode(),content.model_dump(),'json')
    jid=store.enqueue('generate',{'template_id':tid,'content_id':cid,'brief':'Test fixture','slide_count':12})
    execute(store,store.claim(),FixtureGateway(plan))
    job=store.job(jid);assert job.state=='ready',job.error
    result=json.loads(job.result);assert len(result['variants'])==3
    for v in result['variants'].values():
        from vktech.settings import artifact_path
        assert all(artifact_path(v[k]).exists() for k in ('pptx','pdf','html','audit','scene'))
        audit=json.loads(artifact_path(v['audit']).read_text())
        assert any(i['category']=='contextual' and i['status']=='unknown' for i in audit['issues'])
