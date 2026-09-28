from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .contracts import ContentIR,DesignIR,GenerateRequest,SceneIR,AuditReport,RepairRequest,SelectionRequest,ExportRequest,RegenerateSlideRequest,VisualUpdateRequest,PaletteRequest,PaletteSpec,PresentationPlan,Asset,ImageSelection
from .store import Store
from .settings import artifact_path,ROOT,config
from .model import ModelGateway
from .planning import plan_with_model,build_scenes,regenerate_slide_with_model,enrich_plan,fit_semantic_variants,validate_plan,assign_visual_strategies,protect_text_from_template_decor,visual_contract_for,usable_prototypes
from .template import import_template
from .export import export_pptx,render,export_html
from .audit import audit_scene,audit_rendered_deck,contextual_audit,project_contextual_issues,repair_scene
from .selection import score_candidate,choose_variants,compose_scene
from .palette import palette_from_design,recolor_design,recolor_generated_assets,recolor_scene,recolor_template,replace_scene_asset_paths
from .logic import audit_deck_logic,enrich_logic_contracts,restructure_known_curriculum,validate_variant_semantics


def write_json(path,doc):path.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')


def workflow_manifest():
    files=sorted(p for folder in ('config','prompts','skills','agents') for p in (ROOT/folder).glob('**/*') if p.is_file())
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def illustration_prompt(slide):
    text=(slide.title+' '+slide.message).lower()
    suffix='Isometric editorial illustration, VK Education palette with vivid blue, cyan and magenta accents, soft depth, clean pale background, wide 16:9 composition, no words, no letters, no numbers, no logo'
    if 'конкурент' in text and ('абстракт' in text or 'параллел' in text):return 'A tangled cluster of low-level concurrent threads on the left transforms into a clean abstract task graph with parallel branches on the right. '+suffix
    if 'реализац' in text or 'contention' in text or 'overhead' in text:return 'A coherent wide editorial cutaway of one multicore computer: three identical compute tiles, organized cache layers, one shared memory plane and one clearly visible contention bottleneck. Clean structural hierarchy, few purposeful objects, no floating hardware, no loose cables, no server room. '+suffix
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
        if job.kind=='recolor':return self._recolor(job,payload,folder,started,stage)
        stage('loading')
        if job.kind=='compose':return self._compose(job,payload,folder,started,stage)
        parentresult=None;regenerate=None;visual_update=None;recompose=False;image_candidates={}
        if job.kind in {'repair','regenerate_slide','regenerate_visual','recompose'}:
            parent=self.store.job(payload['parent_job_id']);parentresult=json.loads(parent.result)
            if parent.state!='ready':raise ValueError('Parent job is not ready')
            original=GenerateRequest.model_validate(parentresult['request'])
            if job.kind=='repair':
                request=RepairRequest.model_validate(payload['request'])
                old=parentresult['variants'][request.variant]
                scene=SceneIR.model_validate_json(artifact_path(old['scene']).read_bytes())
                report=AuditReport.model_validate_json(artifact_path(old['audit']).read_bytes())
            elif job.kind=='regenerate_slide':regenerate=RegenerateSlideRequest.model_validate(payload['request'])
            elif job.kind=='regenerate_visual':visual_update=VisualUpdateRequest.model_validate(payload['request'])
            else:recompose=True
            image_candidates=dict(parentresult.get('image_candidates',{}))
        else:original=GenerateRequest.model_validate(payload)
        tr=self.store.record(original.template_id,'template');cr=self.store.record(original.content_id,'content')
        template=artifact_path(tr.path).read_bytes()
        if regenerate or visual_update or recompose:
            design=DesignIR.model_validate_json(artifact_path(parentresult['artifacts']['design']).read_bytes())
            content=ContentIR.model_validate_json(artifact_path(parentresult['artifacts']['content']).read_bytes())
            plan=PresentationPlan.model_validate_json(artifact_path(parentresult['artifacts']['plan']).read_bytes())
        else:
            # Re-import the immutable PPTX so retries benefit from parser fixes.
            design=import_template(template);content=ContentIR.model_validate(json.loads(cr.document))
        # Reject an unusable template before starting paid API generation.
        usable_prototypes(design)
        if parentresult and parentresult.get('palette'):
            source_design_path=parentresult.get('artifacts',{}).get('source_design',parentresult['artifacts']['design'])
            source_design=DesignIR.model_validate_json(artifact_path(source_design_path).read_bytes())
            template=recolor_template(template,source_design,PaletteSpec.model_validate(parentresult['palette']))
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
            plan.slides[index]=revised;plan=enrich_logic_contracts(enrich_plan(enrich_logic_contracts(plan),content))
            write_json(folder/'plan.json',plan.model_dump());scenes=build_scenes(design,content,plan,job.id)
        elif visual_update:
            try:index=next(i for i,s in enumerate(plan.slides) if s.id==visual_update.slide_id)
            except StopIteration as exc:raise ValueError('Unknown slide ID') from exc
            slide=plan.slides[index]
            if visual_update.mode=='none':
                slide.asset_id=None;slide.visual='none';slide.visual_strategy='none';slide.visual_score=1;slide.visual_reason='Визуал отключён в редакторе.'
            elif visual_update.mode=='diagram':
                slide.asset_id=None;slide.visual='hierarchy';slide.visual_strategy='diagram';slide.visual_score=1;slide.visual_reason='Выбрана редактируемая схема.'
            elif visual_update.mode=='image' and visual_update.candidate_asset_id:
                allowed={item['asset_id'] for item in image_candidates.get(slide.id,[])}
                if visual_update.candidate_asset_id not in allowed:raise ValueError('Unknown image candidate')
                slide.asset_id=visual_update.candidate_asset_id;slide.visual='image';slide.visual_strategy='generated_image';slide.visual_score=1;slide.visual_reason='Выбран вариант иллюстрации.'
                for item in image_candidates.get(slide.id,[]):item['selected']=item['asset_id']==slide.asset_id
            elif visual_update.mode=='auto':
                replanned=assign_visual_strategies(plan,content,original.generate_images);slide=replanned.slides[index];plan.slides[index]=slide
                if slide.visual_strategy=='generated_image' and original.generate_images:image_candidates[slide.id]=self._generate_image_candidates(slide,content,folder,job.id,visual_update.instruction)
            else:
                if not original.generate_images:raise ValueError('Генерация изображений отключена для этой презентации')
                slide.visual_strategy='generated_image';slide.visual_contract=visual_contract_for(slide)
                image_candidates[slide.id]=self._generate_image_candidates(slide,content,folder,job.id,visual_update.instruction)
            plan=enrich_logic_contracts(enrich_plan(enrich_logic_contracts(plan),content));write_json(folder/'plan.json',plan.model_dump())
            rebuilt=build_scenes(design,content,plan,job.id)
            # A visual edit must not reshuffle every other slide. Preserve the
            # parent's exact scenes and replace only the edited slide.
            scenes=[]
            for candidate in rebuilt:
                previous=SceneIR.model_validate_json(artifact_path(parentresult['variants'][candidate.variant]['scene']).read_bytes())
                previous=previous.model_copy(deep=True);previous.id=candidate.id;previous.version+=1
                previous.slides[index]=candidate.slides[index];scenes.append(previous)
        elif recompose:
            # First normalize any legacy overflow, then restore a checkpoint
            # that semantic compaction may have moved out of the visible text.
            plan=restructure_known_curriculum(fit_semantic_variants(plan),content)
            plan=assign_visual_strategies(plan,content,original.generate_images)
            # Density variants were validated when the model plan was
            # accepted.  This pass guards facts, lengths and references after
            # curriculum repair while keeping legacy plans readable.
            validate_plan(plan,content,original.slide_count,validate_density=False)
            plan=enrich_logic_contracts(enrich_plan(enrich_logic_contracts(plan),content))
            write_json(folder/'plan.json',plan.model_dump());scenes=build_scenes(design,content,plan,job.id)
        else:
            before=time.monotonic();plan=plan_with_model(self.gateway,content,original);timings['planning']=time.monotonic()-before
            plan=restructure_known_curriculum(fit_semantic_variants(plan),content)
            plan=assign_visual_strategies(plan,content,original.generate_images)
            validate_plan(plan,content,original.slide_count,validate_density=False)
            if original.generate_images:
                candidates=sorted((ps for ps in plan.slides if ps.visual_strategy=='generated_image'),key=lambda ps:(-ps.visual_score,ps.id))[:config('pipeline.yaml')['max_generated_image_slides']]
                image_started=time.monotonic();generated_count=0
                for ps in candidates:
                    image_candidates[ps.id]=self._generate_image_candidates(ps,content,folder,job.id)
                    generated_count+=len(image_candidates[ps.id])
                timings['image_generation']=round(time.monotonic()-image_started,3);timings['generated_image_count']=generated_count
            plan=enrich_logic_contracts(enrich_plan(enrich_logic_contracts(plan),content))
            write_json(folder/'plan.json',plan.model_dump());scenes=build_scenes(design,content,plan,job.id)
        logic_findings=audit_deck_logic(plan,content) if plan else []
        if plan:validate_variant_semantics(plan,scenes)
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
            preview_map={variant:material_by_variant[variant][5] for variant in ('A','B','C')}
            selection=choose_variants(scores,[s.id for s in scenes[0].slides],scenes,preview_map)
            changed=regenerate or visual_update
            if changed and parentresult.get('selection'):
                base_selection=changed.selection or parentresult['selection']
                selection={**base_selection,changed.slide_id:selection[changed.slide_id]}
            all_scenes=scenes;plan_path=str((folder/'plan.json').relative_to(artifact_path('.')))
        selected=compose_scene(all_scenes,selection,job.id+'-selected')
        selected_material=self._materialize(selected,folder,template,design,content,'presentation')
        selected_material[6].issues.extend(project_contextual_issues(reference_context,reference[0],selected))
        presentation=self._entry(*selected_material[:7])
        timings['selected_export_render']=round(selected_material[7],3)
        if job.kind=='repair':
            scores=parentresult.get('candidate_scores',{});reasons=parentresult.get('candidate_reasons',{})
        slide_options=self._slide_options(plan,variants,selection,scores,reasons,image_candidates,logic_findings) if plan else parentresult.get('slides',[])
        elapsed=round(time.monotonic()-started,3)
        current_palette=(parentresult.get('palette') if parentresult else None) or palette_from_design(design).model_dump()
        source_palette=(parentresult.get('source_palette') if parentresult else None) or current_palette
        source_design=parentresult.get('artifacts',{}).get('source_design') if parentresult else None
        source_variants=parentresult.get('artifacts',{}).get('source_variants') if parentresult else None
        artifacts={'plan':plan_path,'content':str((folder/'content.json').relative_to(artifact_path('.'))),'design':str((folder/'design.json').relative_to(artifact_path('.')))}
        artifacts['source_design']=source_design or artifacts['design'];artifacts['source_variants']=source_variants or {variant:value['scene'] for variant,value in variants.items()}
        manifest={'request':original.model_dump(),'profile':self.gateway.profile,'model_manifest':self.gateway.manifest,'model_calls':self.gateway.calls,'workflow_hashes':workflow_manifest(),'timings':timings,'elapsed_seconds':elapsed,'deadline_met':elapsed<=config('pipeline.yaml')['deadline_seconds'],'font_verification':'see environment/unknown issues in audit','contextual_audit_strategy':'candidate B checked once; content findings projected because candidates share the same facts','logic_findings':[finding.model_dump() for finding in logic_findings],'palette':current_palette,'source_palette':source_palette,'variants':variants,'candidate_scores':scores,'candidate_reasons':reasons,'image_candidates':image_candidates,'selection':selection,'slides':slide_options,'presentation':presentation,'artifacts':artifacts}
        write_json(folder/'manifest.json',manifest)
        manifest['manifest']=str((folder/'manifest.json').relative_to(artifact_path('.')))
        return manifest

    def _generate_image(self,slide,content,folder,job_id,suffix=''):
        output=folder/(slide.id+'-generated.png')
        self.gateway.image(illustration_prompt(slide)+suffix,output)
        from PIL import Image
        with Image.open(output) as generated:media={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[generated.format]
        provider=getattr(getattr(self.gateway,'image_endpoint',None),'provider','configured-provider')
        image_model=getattr(getattr(self.gateway,'image_endpoint',None),'model','configured-image-model')
        source=f'AI-generated via {provider}/{image_model}; illustrative, not factual evidence'
        asset=Asset(id=slide.id+'-generated-'+job_id[:8],path=str(output.relative_to(artifact_path('.'))),description=slide.visual_brief or slide.message,source=source,media_type=media,claim_ids=slide.claim_ids)
        content.assets.append(asset);return asset

    def _generate_image_candidates(self,slide,content,folder,job_id,instruction=''):
        """Generate a small contact set, score it with the vision model and keep only a defensible choice."""
        count=config('pipeline.yaml')['image_candidate_count'];paths=[];assets=[]
        variations=(' editorial cutaway view',' clean side-on composition',' restrained wide overview')
        for index in range(count):
            output=folder/f'{slide.id}-candidate-{index+1}.png'
            prompt=illustration_prompt(slide)+variations[index%len(variations)]+('. '+instruction.strip() if instruction.strip() else '')
            self.gateway.image(prompt,output);paths.append(output)
        from PIL import Image
        for index,output in enumerate(paths):
            with Image.open(output) as generated:media={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[generated.format]
            provider=getattr(getattr(self.gateway,'image_endpoint',None),'provider','configured-provider')
            image_model=getattr(getattr(self.gateway,'image_endpoint',None),'model','configured-image-model')
            source=f'AI-generated via {provider}/{image_model}; illustrative, not factual evidence'
            asset=Asset(id=f'{slide.id}-candidate-{index+1}-{job_id[:8]}',path=str(output.relative_to(artifact_path('.'))),description=slide.visual_contract.goal or slide.message,source=source,media_type=media,claim_ids=slide.claim_ids)
            content.assets.append(asset);assets.append(asset)
        payload={'slide_id':slide.id,'title':slide.title,'message':slide.message,'visual_contract':slide.visual_contract.model_dump(),'candidate_indices':list(range(count))}
        selection=self.gateway.structured('image_selection',payload,ImageSelection,images=paths)
        scored={item.index:item for item in selection.candidates};best=scored[selection.selected_index]
        if set(scored)!=set(range(count)):raise ValueError('Image evaluator must score every generated candidate exactly once')
        threshold=config('pipeline.yaml')['image_candidate_accept_score'];fallback=selection.fallback_to_diagram or not best.accepted or best.score<threshold
        if fallback:
            slide.asset_id=None;slide.visual='hierarchy';slide.visual_strategy='diagram';slide.visual_score=.9;slide.visual_reason='Кандидаты иллюстрации отклонены автоматической проверкой; использована редактируемая схема.'
        else:
            slide.asset_id=assets[selection.selected_index].id;slide.visual='image';slide.visual_strategy='generated_image';slide.visual_score=round(best.score/100,2);slide.visual_reason=selection.reason
        return [{'asset_id':asset.id,'path':asset.path,'score':scored[index].score,'semantic_fit':scored[index].semantic_fit,'naturalness':scored[index].naturalness,'composition':scored[index].composition,'accepted':scored[index].accepted,'reason':scored[index].reason,'selected':asset.id==slide.asset_id} for index,asset in enumerate(assets)]

    def _materialize(self,scene,folder,template,design,content,dirname=None):
        start=time.monotonic();out=folder/(dirname or scene.variant);out.mkdir(exist_ok=True)
        pptx=out/'presentation.pptx';decor=out/'decoration.pptx'
        export_pptx(template,design,scene,pptx);export_pptx(template,design,scene,decor,decor_only=True)
        pdf,previews=render(pptx,out/'render');_,backgrounds=render(decor,out/'background')
        html=out/'presentation.html';export_html(scene,html,backgrounds)
        report=audit_scene(scene,design,content,opened=True)
        report.issues.extend(audit_rendered_deck(scene,previews))
        return scene,out,pptx,pdf,html,previews,report,time.monotonic()-start

    def _entry(self,scene,out,pptx,pdf,html,previews,report):
        sp=out/'scene.json';ap=out/'audit.json';write_json(sp,scene.model_dump());write_json(ap,report.model_dump())
        rel=lambda p:str(p.relative_to(artifact_path('.')))
        return {'version':scene.version,'scene':rel(sp),'audit':rel(ap),'pptx':rel(pptx),'pdf':rel(pdf),'html':rel(html),'previews':[rel(p) for p in previews],'issue_count':sum(i.status=='fail' for i in report.issues)}

    def _slide_options(self,plan,variants,selection,scores,reasons,image_candidates=None,logic_findings=None):
        labels={'A':'Крупно и кратко','B':'Сбалансированно','C':'Подробно'};result=[]
        for index,slide in enumerate(plan.slides):
            options={v:{'label':labels[v],'preview':variants[v]['previews'][index],'score':scores.get(slide.id,{}).get(v,0),'reasons':reasons.get(slide.id,{}).get(v,{})} for v in ('A','B','C')}
            findings=[finding.model_dump() for finding in (logic_findings or []) if finding.slide_id in {None,slide.id}]
            result.append({'id':slide.id,'title':slide.title,'archetype':slide.archetype,'lead':slide.message,'balanced_message':slide.balanced_message,'support_points':slide.support_points,'takeaway':slide.takeaway,'visual_strategy':slide.visual_strategy,'visual_score':slide.visual_score,'visual_reason':slide.visual_reason,'visual_contract':slide.visual_contract.model_dump(),'logic_contract':slide.logic_contract.model_dump(),'logic_status':'review' if any(item['severity'] in {'error','warning'} for item in findings) else 'ok','logic_findings':findings,'image_candidates':(image_candidates or {}).get(slide.id,[]),'selected':selection.get(slide.id,'A'),'options':options})
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
        source_design_path=result.get('artifacts',{}).get('source_design',result['artifacts']['design'])
        source_design=DesignIR.model_validate_json(artifact_path(source_design_path).read_bytes())
        if result.get('palette'):template=recolor_template(template,source_design,PaletteSpec.model_validate(result['palette']))
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

    def _recolor(self,job,payload,folder,started,stage):
        parent=self.store.job(payload['parent_job_id']);parentresult=json.loads(parent.result)
        if parent.state!='ready':raise ValueError('Parent job is not ready')
        request=PaletteRequest.model_validate(payload['request']);palette=request.palette
        original=GenerateRequest.model_validate(parentresult['request'])
        selection=dict(request.selection or parentresult['selection'])
        if set(selection)!=set(parentresult['selection']):raise ValueError('Selection must contain every slide exactly once')
        source_design_path=parentresult.get('artifacts',{}).get('source_design',parentresult['artifacts']['design'])
        source_design=DesignIR.model_validate_json(artifact_path(source_design_path).read_bytes())
        design=recolor_design(source_design,palette)
        content=ContentIR.model_validate_json(artifact_path(parentresult['artifacts']['content']).read_bytes())
        content,replacements=recolor_generated_assets(content,source_design,palette,folder)
        image_candidates=json.loads(json.dumps(parentresult.get('image_candidates',{})))
        for candidates in image_candidates.values():
            for candidate in candidates:candidate['path']=replacements.get(candidate['path'],candidate['path'])
        plan=PresentationPlan.model_validate_json(artifact_path(parentresult['artifacts']['plan']).read_bytes())
        logic_findings=audit_deck_logic(plan,content)
        # A recompose job may carry old source_variants only to preserve palette
        # provenance.  While the deck is still in its source palette, the
        # parent's current variants are the authoritative geometry.  Once a
        # custom palette is active, reuse the saved source variants so repeated
        # colour changes do not accumulate hue shifts.
        source_palette=parentresult.get('source_palette')
        current_palette=parentresult.get('palette')
        source_variants=(
            {variant:parentresult['variants'][variant]['scene'] for variant in ('A','B','C')}
            if source_palette and current_palette==source_palette
            else parentresult.get('artifacts',{}).get('source_variants')
        ) or {variant:parentresult['variants'][variant]['scene'] for variant in ('A','B','C')}
        source_scenes={variant:SceneIR.model_validate_json(artifact_path(path).read_bytes()) for variant,path in source_variants.items()}
        scenes=[protect_text_from_template_decor(replace_scene_asset_paths(recolor_scene(source_scenes[variant],source_design,palette),replacements),design) for variant in ('A','B','C')]
        used_colors=[]
        for scene in scenes:
            for slide in scene.slides:
                used_colors.append(slide.background)
                for node in slide.nodes:used_colors.extend(filter(None,(node.style.color,node.style.fill)))
        design.palette=list(dict.fromkeys([*design.palette,*used_colors]))
        raw=artifact_path(self.store.record(original.template_id,'template').path).read_bytes()
        template=recolor_template(raw,source_design,palette)
        write_json(folder/'content.json',content.model_dump());write_json(folder/'design.json',design.model_dump())
        stage('layout_export_render')
        with ThreadPoolExecutor(max_workers=2) as pool:materials=list(pool.map(lambda scene:self._materialize(scene,folder,template,design,content),scenes))
        variants={};material_by_variant={material[0].variant:material for material in materials};timings={}
        for material in materials:
            scene,out,pptx,pdf,html,previews,report,seconds=material
            source_report=AuditReport.model_validate_json(artifact_path(parentresult['variants'][scene.variant]['audit']).read_bytes())
            contextual=[issue for issue in source_report.issues if issue.category=='contextual']
            report.issues.extend(project_contextual_issues(contextual,source_scenes[scene.variant],scene))
            variants[scene.variant]=self._entry(scene,out,pptx,pdf,html,previews,report)
            timings[scene.variant+'_export_render']=round(seconds,3)
        scores={};reasons={}
        for index,slide in enumerate(scenes[0].slides):
            scores[slide.id]={};reasons[slide.id]={}
            for variant in ('A','B','C'):
                material=material_by_variant[variant];value,detail=score_candidate(material[0],material[0].slides[index],material[5][index])
                scores[slide.id][variant]=value;reasons[slide.id][variant]=detail
        selected=compose_scene(scenes,selection,job.id+'-selected')
        selected_material=self._materialize(selected,folder,template,design,content,'presentation')
        source_selected=SceneIR.model_validate_json(artifact_path(parentresult['presentation']['scene']).read_bytes())
        source_selected_report=AuditReport.model_validate_json(artifact_path(parentresult['presentation']['audit']).read_bytes())
        selected_material[6].issues.extend(project_contextual_issues([issue for issue in source_selected_report.issues if issue.category=='contextual'],source_selected,selected))
        presentation=self._entry(*selected_material[:7]);timings['selected_export_render']=round(selected_material[7],3)
        slides=self._slide_options(plan,variants,selection,scores,reasons,image_candidates,logic_findings)
        elapsed=round(time.monotonic()-started,3)
        artifacts={'plan':parentresult['artifacts']['plan'],'content':str((folder/'content.json').relative_to(artifact_path('.'))),'design':str((folder/'design.json').relative_to(artifact_path('.'))),'source_design':source_design_path,'source_variants':source_variants}
        manifest={'request':original.model_dump(),'profile':parentresult.get('profile','selection'),'model_manifest':parentresult.get('model_manifest',{}),'model_calls':[],'workflow_hashes':workflow_manifest(),'timings':timings,'elapsed_seconds':elapsed,'deadline_met':elapsed<=config('pipeline.yaml')['deadline_seconds'],'logic_findings':[finding.model_dump() for finding in logic_findings],'palette':palette.model_dump(),'source_palette':parentresult.get('source_palette') or palette_from_design(source_design).model_dump(),'variants':variants,'candidate_scores':scores,'candidate_reasons':reasons,'image_candidates':image_candidates,'selection':selection,'slides':slides,'presentation':presentation,'artifacts':artifacts}
        write_json(folder/'manifest.json',manifest);manifest['manifest']=str((folder/'manifest.json').relative_to(artifact_path('.')))
        return manifest
