from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .contracts import ContentIR,DesignIR,GenerateRequest,SceneIR,AuditReport,RepairRequest,SelectionRequest,ExportRequest,RegenerateSlideRequest,Asset
from .store import Store
from .settings import artifact_path,ROOT,config
from .model import ModelGateway
from .planning import plan_with_model,build_scenes,regenerate_slide_with_model,enrich_plan,validate_plan
from .export import export_pptx,render,export_html
from .audit import audit_scene,contextual_audit,project_contextual_issues,repair_scene
from .selection import score_candidate,choose_variants,compose_scene


def write_json(path,doc):path.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')


def workflow_manifest():
    files=sorted(p for folder in ('config','prompts','skills','agents') for p in (ROOT/folder).glob('**/*') if p.is_file())
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def illustration_prompt(slide):
    text=(slide.title+' '+slide.message).lower()
    suffix='Isometric editorial illustration, VK Education palette with vivid blue, cyan and magenta accents, soft depth, clean pale background, wide 16:9 composition, no words, no letters, no numbers, no logo'
    if 'вывод' in text or 'итог' in text:return 'A coherent overview of parallel computing: processors, shared memory, task graph and critical path assembled into one balanced system. '+suffix
    if 'scheduler' in text or 'планиров' in text:return 'Several computing nodes take ready tasks from a shared queue, clear visual flow from queue to processors. '+suffix
    if 'dag' in text or 'граф' in text:return 'A directed acyclic graph made of luminous task nodes and dependency arrows, one critical path is emphasized. '+suffix
    if 'pram' in text or 'общ' in text and 'памят' in text:return 'Many identical processor modules connected to one shared memory block, visually clear concurrent access. '+suffix
    if 'reduc' in text or 'редук' in text:return 'A balanced reduction tree combines many small input blocks into one result, clear bottom-up flow. '+suffix
    if 'work' in text or 'span' in text or 'работ' in text and 'глубин' in text:return 'Parallel task branches with total work shown by many nodes and critical span shown by one highlighted path. '+suffix
    if 'fork' in text or 'join' in text:return 'One task splits into several parallel branches and then joins into one result, clear fork and join structure. '+suffix
    if 'конфликт' in text or 'запис' in text:return 'Several processors simultaneously address shared memory cells, one access conflict is highlighted with a magenta glow. '+suffix
    if 'цикл' in text or 'parallel for' in text:return 'A long sequence of loop iterations is distributed evenly across several processor lanes. '+suffix
    if 'нижн' in text or 'границ' in text:return 'A computational path approaches a firm lower boundary visualized as a glowing geometric plane. '+suffix
    return 'Abstract parallel computing system with connected processors and flowing task blocks. '+suffix


class Pipeline:
    def __init__(self,store:Store,gateway=None):self.store=store;self.gateway=gateway or ModelGateway()

    def run(self,job):
        started=time.monotonic();timings={};payload=json.loads(job.payload)
        folder=artifact_path(f'jobs/{job.id}/{job.lease_owner}')
        folder.mkdir(parents=True,exist_ok=True)
        def stage(name):self.store.owned_update(job.id,job.lease_owner,stage=name)
        stage('loading')
        if job.kind=='compose':return self._compose(job,payload,folder,started,stage)
        parentresult=None;regenerate=None;recompose=False
        if job.kind in {'repair','regenerate_slide','recompose'}:
            parent=self.store.job(payload['parent_job_id']);parentresult=json.loads(parent.result)
            if parent.state!='ready':raise ValueError('Parent job is not ready')
            original=GenerateRequest.model_validate(parentresult['request'])
            if job.kind=='repair':
                request=RepairRequest.model_validate(payload['request'])
                old=parentresult['variants'][request.variant]
                scene=SceneIR.model_validate_json(artifact_path(old['scene']).read_bytes())
                report=AuditReport.model_validate_json(artifact_path(old['audit']).read_bytes())
            elif job.kind=='regenerate_slide':regenerate=RegenerateSlideRequest.model_validate(payload['request'])
            else:recompose=True
        else:original=GenerateRequest.model_validate(payload)
        tr=self.store.record(original.template_id,'template');cr=self.store.record(original.content_id,'content')
        template=artifact_path(tr.path).read_bytes()
        if regenerate or recompose:
            design=DesignIR.model_validate_json(artifact_path(parentresult['artifacts']['design']).read_bytes())
            content=ContentIR.model_validate_json(artifact_path(parentresult['artifacts']['content']).read_bytes())
            from .contracts import PresentationPlan
            plan=PresentationPlan.model_validate_json(artifact_path(parentresult['artifacts']['plan']).read_bytes())
        else:
            design=DesignIR.model_validate(json.loads(tr.document));content=ContentIR.model_validate(json.loads(cr.document))
        stage('planning')
        if job.kind=='repair':
            if scene.version>=1+config('pipeline.yaml')['repair_attempts']:raise ValueError('Repair attempt limit reached; edit content or layout explicitly')
            scene,patch=repair_scene(scene,report,request,design);scenes=[scene]
            write_json(folder/'patch.json',patch);plan=None
        elif regenerate:
            try:index=next(i for i,s in enumerate(plan.slides) if s.id==regenerate.slide_id)
            except StopIteration as exc:raise ValueError('Unknown slide ID') from exc
            old_slide=plan.slides[index]
            before=time.monotonic();revised=regenerate_slide_with_model(self.gateway,old_slide,content,regenerate.instruction);timings['slide_regeneration']=round(time.monotonic()-before,3)
            revised.claim_ids=list(old_slide.claim_ids);revised.dataset_id=old_slide.dataset_id
            if old_slide.visual=='image' and original.generate_images:
                revised.visual='image'
                asset=self._generate_image(revised,content,folder,job.id,' Alternative composition with a distinct camera angle and silhouette.')
                revised.asset_id=asset.id
            else:revised.asset_id=None
            plan.slides[index]=revised;plan=enrich_plan(plan,content)
            write_json(folder/'plan.json',plan.model_dump());scenes=build_scenes(design,content,plan,job.id)
        elif recompose:
            validate_plan(plan,content,original.slide_count)
            plan=enrich_plan(plan,content);write_json(folder/'plan.json',plan.model_dump());scenes=build_scenes(design,content,plan,job.id)
        else:
            before=time.monotonic();plan=plan_with_model(self.gateway,content,original);timings['planning']=time.monotonic()-before
            if original.generate_images:
                from .runtime import local_image_phase
                candidates=[ps for i,ps in enumerate(plan.slides) if ps.visual=='none' and ps.role=='content' and i<len(plan.slides)-1 and not any(word in (ps.title+' '+ps.message).lower() for word in ('вопрос','домашн','спасибо','итог'))]
                priority=('reduc','редук','dag','граф','планиров','scheduler','pram','памят','work','span','fork','join','parallel for','цикл','конфликт','границ')
                candidates.sort(key=lambda ps:(0 if any(word in (ps.title+' '+ps.message).lower() for word in priority) else 1,ps.id))
                image_count=max(0,min(8,int(os.environ.get('T2I_IMAGE_COUNT','4'))))
                image_started=time.monotonic();generated_count=0
                generated_hashes=set()
                with local_image_phase():
                    for ps in candidates[:image_count]:
                        output=folder/(ps.id+'-generated.png');prompt=illustration_prompt(ps)
                        for attempt in range(2):
                            self.gateway.image(prompt+(' Alternative bird-eye composition with a distinct silhouette.' if attempt else ''),output)
                            digest=hashlib.sha256(output.read_bytes()).hexdigest()
                            if digest not in generated_hashes:break
                        generated_hashes.add(digest)
                        from PIL import Image
                        with Image.open(output) as generated: media={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[generated.format]
                        asset=Asset(id=ps.id+'-generated-'+job.id[:8],path=str(output.relative_to(artifact_path('.'))),description=ps.message,source='Z-Image-Turbo 8-bit MLX generated; illustrative, not factual evidence',media_type=media,claim_ids=ps.claim_ids)
                        content.assets.append(asset);ps.asset_id=asset.id;ps.visual='image';generated_count+=1
                timings['image_generation']=round(time.monotonic()-image_started,3);timings['generated_image_count']=generated_count
            plan=enrich_plan(plan,content);write_json(folder/'plan.json',plan.model_dump());scenes=build_scenes(design,content,plan,job.id)
        write_json(folder/'content.json',content.model_dump());write_json(folder/'design.json',design.model_dump())
        stage('layout_export_render')
        # Each renderer has its own isolated LibreOffice profile and output directory.
        with ThreadPoolExecutor(max_workers=2) as pool:materials=list(pool.map(lambda s:self._materialize(s,folder,template,design,content),scenes))
        stage('contextual_audit')
        audit_start=time.monotonic()
        reference=next((m for m in materials if m[0].variant=='B'),materials[0])
        reference_context=contextual_audit(self.gateway,reference[0],content,reference[5])
        timings['contextual_audit']=round(time.monotonic()-audit_start,3)
        variants={}
        material_by_variant={m[0].variant:m for m in materials}
        for scene,out,pptx,pdf,html,previews,report,seconds in materials:
            report.issues.extend(reference_context if scene.id==reference[0].id else project_contextual_issues(reference_context,reference[0],scene))
            variants[scene.variant]=self._entry(scene,out,pptx,pdf,html,previews,report)
            timings[scene.variant+'_export_render']=round(seconds,3)
        stage('finalizing')
        if job.kind=='repair':
            merged=dict(parentresult['variants']);merged.update(variants);variants=merged
            selection=dict(parentresult.get('selection',{}));selection.update({s.id:scene.variant for s in scenes})
            all_scenes=[SceneIR.model_validate_json(artifact_path(v['scene']).read_bytes()) for v in variants.values()]
            plan_path=parentresult['artifacts']['plan']
        else:
            scores={}
            reasons={}
            for index,slide in enumerate(scenes[0].slides):
                scores[slide.id]={};reasons[slide.id]={}
                for variant in ('A','B','C'):
                    material=material_by_variant[variant];candidate=material[0].slides[index]
                    value,detail=score_candidate(material[0],candidate,material[5][index])
                    scores[slide.id][variant]=value;reasons[slide.id][variant]=detail
            selection=choose_variants(scores,[s.id for s in scenes[0].slides])
            if regenerate and parentresult.get('selection'):
                base_selection=regenerate.selection or parentresult['selection']
                selection={**base_selection,regenerate.slide_id:selection[regenerate.slide_id]}
            all_scenes=scenes;plan_path=str((folder/'plan.json').relative_to(artifact_path('.')))
        selected=compose_scene(all_scenes,selection,job.id+'-selected')
        selected_material=self._materialize(selected,folder,template,design,content,'presentation')
        selected_material[6].issues.extend(project_contextual_issues(reference_context,reference[0],selected))
        presentation=self._entry(*selected_material[:7])
        timings['selected_export_render']=round(selected_material[7],3)
        if job.kind=='repair':
            scores=parentresult.get('candidate_scores',{});reasons=parentresult.get('candidate_reasons',{})
        slide_options=self._slide_options(plan,variants,selection,scores,reasons) if plan else parentresult.get('slides',[])
        elapsed=round(time.monotonic()-started,3)
        manifest={'request':original.model_dump(),'profile':self.gateway.profile,'model_manifest':self.gateway.manifest,'model_calls':self.gateway.calls,'workflow_hashes':workflow_manifest(),'timings':timings,'elapsed_seconds':elapsed,'deadline_met':elapsed<=config('pipeline.yaml')['deadline_seconds'],'font_verification':'see environment/unknown issues in audit','contextual_audit_strategy':'candidate B checked once; content findings projected because candidates share the same facts','variants':variants,'candidate_scores':scores,'candidate_reasons':reasons,'selection':selection,'slides':slide_options,'presentation':presentation,'artifacts':{'plan':plan_path,'content':str((folder/'content.json').relative_to(artifact_path('.'))),'design':str((folder/'design.json').relative_to(artifact_path('.')))}}
        write_json(folder/'manifest.json',manifest)
        manifest['manifest']=str((folder/'manifest.json').relative_to(artifact_path('.')))
        return manifest

    def _generate_image(self,slide,content,folder,job_id,suffix=''):
        from .runtime import local_image_phase
        output=folder/(slide.id+'-generated.png')
        with local_image_phase():self.gateway.image(illustration_prompt(slide)+suffix,output)
        from PIL import Image
        with Image.open(output) as generated:media={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[generated.format]
        asset=Asset(id=slide.id+'-generated-'+job_id[:8],path=str(output.relative_to(artifact_path('.'))),description=slide.visual_brief or slide.message,source='Z-Image-Turbo 8-bit MLX generated; illustrative, not factual evidence',media_type=media,claim_ids=slide.claim_ids)
        content.assets.append(asset);return asset

    def _materialize(self,scene,folder,template,design,content,dirname=None):
        start=time.monotonic();out=folder/(dirname or scene.variant);out.mkdir(exist_ok=True)
        pptx=out/'presentation.pptx';decor=out/'decoration.pptx'
        export_pptx(template,design,scene,pptx);export_pptx(template,design,scene,decor,decor_only=True)
        pdf,previews=render(pptx,out/'render');_,backgrounds=render(decor,out/'background')
        html=out/'presentation.html';export_html(scene,html,backgrounds)
        report=audit_scene(scene,design,content,opened=True)
        return scene,out,pptx,pdf,html,previews,report,time.monotonic()-start

    def _entry(self,scene,out,pptx,pdf,html,previews,report):
        sp=out/'scene.json';ap=out/'audit.json';write_json(sp,scene.model_dump());write_json(ap,report.model_dump())
        rel=lambda p:str(p.relative_to(artifact_path('.')))
        return {'version':scene.version,'scene':rel(sp),'audit':rel(ap),'pptx':rel(pptx),'pdf':rel(pdf),'html':rel(html),'previews':[rel(p) for p in previews],'issue_count':sum(i.status=='fail' for i in report.issues)}

    def _slide_options(self,plan,variants,selection,scores,reasons):
        labels={'A':'Крупно и кратко','B':'Сбалансированно','C':'Подробно'};result=[]
        for index,slide in enumerate(plan.slides):
            options={v:{'label':labels[v],'preview':variants[v]['previews'][index],'score':scores.get(slide.id,{}).get(v,0),'reasons':reasons.get(slide.id,{}).get(v,{})} for v in ('A','B','C')}
            result.append({'id':slide.id,'title':slide.title,'archetype':slide.archetype,'lead':slide.message,'balanced_message':slide.balanced_message,'support_points':slide.support_points,'takeaway':slide.takeaway,'selected':selection.get(slide.id,'A'),'options':options})
        return result

    def _compose(self,job,payload,folder,started,stage):
        parent=self.store.job(payload['parent_job_id']);result=json.loads(parent.result)
        if parent.state!='ready':raise ValueError('Parent job is not ready')
        original=GenerateRequest.model_validate(result['request'])
        if 'selection' in payload['request']:
            request=ExportRequest.model_validate(payload['request'])
            if set(request.selection)!=set(result['selection']):raise ValueError('Selection must contain every slide exactly once')
            selection=dict(request.selection)
        else:
            request=SelectionRequest.model_validate(payload['request'])
            if request.slide_id not in result['selection']:raise ValueError('Unknown slide ID')
            selection={**result['selection'],request.slide_id:request.variant}
        tr=self.store.record(original.template_id,'template');template=artifact_path(tr.path).read_bytes()
        design=DesignIR.model_validate_json(artifact_path(result['artifacts']['design']).read_bytes())
        content=ContentIR.model_validate_json(artifact_path(result['artifacts']['content']).read_bytes())
        scenes=[SceneIR.model_validate_json(artifact_path(result['variants'][v]['scene']).read_bytes()) for v in ('A','B','C')]
        stage('layout_export_render');selected=compose_scene(scenes,selection,job.id+'-selected')
        material=self._materialize(selected,folder,template,design,content,'presentation');presentation=self._entry(*material[:7])
        slides=[]
        for item in result['slides']:
            copy=dict(item);copy['selected']=selection[copy['id']];slides.append(copy)
        elapsed=round(time.monotonic()-started,3)
        manifest={**result,'model_calls':[],'timings':{'selected_export_render':round(material[7],3)},'elapsed_seconds':elapsed,'deadline_met':elapsed<=config('pipeline.yaml')['deadline_seconds'],'selection':selection,'slides':slides,'presentation':presentation}
        manifest.pop('manifest',None);write_json(folder/'manifest.json',manifest);manifest['manifest']=str((folder/'manifest.json').relative_to(artifact_path('.')))
        return manifest
