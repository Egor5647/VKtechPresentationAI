from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .contracts import ContentIR,DesignIR,GenerateRequest,SceneIR,AuditReport,RepairRequest
from .store import Store
from .settings import artifact_path,ROOT,config
from .model import ModelGateway
from .planning import plan_with_model,build_scenes
from .export import export_pptx,render,export_html
from .audit import audit_scene,contextual_audit,repair_scene


def write_json(path,doc):path.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')


def workflow_manifest():
    files=sorted(p for folder in ('config','prompts','skills','agents') for p in (ROOT/folder).glob('**/*') if p.is_file())
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


class Pipeline:
    def __init__(self,store:Store,gateway=None):self.store=store;self.gateway=gateway or ModelGateway()

    def run(self,job):
        started=time.monotonic();timings={};payload=json.loads(job.payload)
        folder=artifact_path(f'jobs/{job.id}/{job.lease_owner}')
        folder.mkdir(parents=True,exist_ok=True)
        def stage(name):self.store.owned_update(job.id,job.lease_owner,stage=name)
        stage('loading')
        if job.kind=='repair':
            parent=self.store.job(payload['parent_job_id']);parentresult=json.loads(parent.result)
            if parent.state!='ready':raise ValueError('Parent job is not ready')
            request=RepairRequest.model_validate(payload['request'])
            old=parentresult['variants'][request.variant]
            scene=SceneIR.model_validate_json(artifact_path(old['scene']).read_bytes())
            report=AuditReport.model_validate_json(artifact_path(old['audit']).read_bytes())
            original=GenerateRequest.model_validate(parentresult['request'])
        else:original=GenerateRequest.model_validate(payload)
        tr=self.store.record(original.template_id,'template');cr=self.store.record(original.content_id,'content')
        design=DesignIR.model_validate(json.loads(tr.document));content=ContentIR.model_validate(json.loads(cr.document));template=artifact_path(tr.path).read_bytes()
        stage('planning')
        if job.kind=='repair':
            if scene.version>=1+config('pipeline.yaml')['repair_attempts']:raise ValueError('Repair attempt limit reached; edit content or layout explicitly')
            scene,patch=repair_scene(scene,report,request,design);scenes=[scene]
            write_json(folder/'patch.json',patch);plan=None
        else:
            before=time.monotonic();plan=plan_with_model(self.gateway,content,original);timings['planning']=time.monotonic()-before
            if original.generate_images:
                from .contracts import Asset
                for ps in plan.slides:
                    if ps.visual=='none' and ps.role=='content':
                        output=folder/(ps.id+'-generated.png');self.gateway.image(ps.message,output)
                        from PIL import Image
                        with Image.open(output) as generated: media={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[generated.format]
                        asset=Asset(id=ps.id+'-generated',path=str(output.relative_to(artifact_path('.'))),description=ps.message,source='Z-Image-Turbo generated; illustrative, not factual evidence',media_type=media)
                        content.assets.append(asset);ps.asset_id=asset.id;ps.visual='image'
                        break
            write_json(folder/'plan.json',plan.model_dump());scenes=build_scenes(design,content,plan,job.id)
        write_json(folder/'content.json',content.model_dump());write_json(folder/'design.json',design.model_dump())
        stage('layout_export_render')
        def materialize(scene):
            start=time.monotonic();out=folder/scene.variant;out.mkdir(exist_ok=True)
            pptx=out/'presentation.pptx';decor=out/'decoration.pptx'
            export_pptx(template,design,scene,pptx);export_pptx(template,design,scene,decor,decor_only=True)
            pdf,previews=render(pptx,out/'render');_,backgrounds=render(decor,out/'background')
            html=out/'presentation.html';export_html(scene,html,backgrounds)
            report=audit_scene(scene,design,content,opened=True)
            return scene,out,pptx,pdf,html,previews,report,time.monotonic()-start
        # Each renderer has its own isolated LibreOffice profile and output directory.
        with ThreadPoolExecutor(max_workers=2) as pool:materials=list(pool.map(materialize,scenes))
        stage('contextual_audit')
        variants={}
        for scene,out,pptx,pdf,html,previews,report,seconds in materials:
            report.issues.extend(contextual_audit(self.gateway,scene,content,previews))
            sp=out/'scene.json';ap=out/'audit.json';write_json(sp,scene.model_dump());write_json(ap,report.model_dump())
            rel=lambda p:str(p.relative_to(artifact_path('.')))
            variants[scene.variant]={'version':scene.version,'scene':rel(sp),'audit':rel(ap),'pptx':rel(pptx),'pdf':rel(pdf),'html':rel(html),'previews':[rel(p) for p in previews],'issue_count':sum(i.status=='fail' for i in report.issues)}
            timings[scene.variant+'_export_render']=round(seconds,3)
        stage('finalizing')
        if job.kind=='repair':
            merged=dict(parentresult['variants']);merged.update(variants);variants=merged
        elapsed=round(time.monotonic()-started,3)
        manifest={'request':original.model_dump(),'profile':self.gateway.profile,'model_manifest':self.gateway.manifest,'model_calls':self.gateway.calls,'workflow_hashes':workflow_manifest(),'timings':timings,'elapsed_seconds':elapsed,'deadline_met':elapsed<=config('pipeline.yaml')['deadline_seconds'],'font_verification':'see environment/unknown issues in audit','variants':variants}
        write_json(folder/'manifest.json',manifest)
        manifest['manifest']=str((folder/'manifest.json').relative_to(artifact_path('.')))
        return manifest
