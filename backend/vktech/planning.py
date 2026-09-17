from __future__ import annotations
import hashlib
import logging
import os
import re
from .settings import artifact_path
from .contracts import PresentationPlan, ContentIR, DesignIR, SceneIR, SceneSlide, Node, Style, Box, SlideRevision

log=logging.getLogger(__name__)


class NeedsInput(ValueError):
    pass


def planning_claims(content: ContentIR, brief: str) -> list:
    """Bound model context while retaining every explicitly mandatory claim."""
    limit=max(1,int(os.environ.get('MODEL_MAX_PLANNING_CLAIMS','32')))
    char_limit=max(1000,int(os.environ.get('MODEL_MAX_PLANNING_CHARS','18000')))
    required=[claim for claim in content.claims if claim.required]
    required_chars=sum(len(claim.text) for claim in required)
    if len(required)>limit or required_chars>char_limit:
        raise NeedsInput(
            f'Исходные материалы содержат {len(required)} обязательных блоков объёмом {required_chars} символов. '
            'Разделите документ или отметьте второстепенные разделы как необязательные.'
        )
    terms=set(re.findall(r'[\w+#<>]{3,}',brief.lower()))
    def rank(claim):
        words=set(re.findall(r'[\w+#<>]{3,}',claim.text.lower()))
        return (-len(words&terms),len(claim.text),claim.source,claim.id)
    selected=list(required);used={claim.id for claim in selected};chars=required_chars
    for claim in sorted((c for c in content.claims if c.id not in used),key=rank):
        if len(selected)>=limit:break
        if chars+len(claim.text)>char_limit:continue
        selected.append(claim);chars+=len(claim.text)
    if not selected and content.claims:selected=[min(content.claims,key=lambda c:(len(c.text),c.id))]
    return selected


def _visual_kind(slide) -> str:
    text=(slide.title+' '+slide.message).lower()
    if re.search(r'этап|послед|алгоритм|планиров|scheduler|fork|join|reduc|распростран|propagat|\bset\b',text):return 'sequence'
    if re.search(r'dag|граф|дерев|иерарх|завис|work|span',text):return 'hierarchy'
    return 'list'


def normalize_plan(plan: PresentationPlan) -> PresentationPlan:
    """Repair optional visual intents and enforce a useful native-visual quota."""
    result=plan.model_copy(deep=True)
    for slide in result.slides:
        if slide.visual=='image' and not slide.asset_id:slide.visual='none'
        if slide.visual in {'chart','table'} and not slide.dataset_id:slide.visual='none'
    candidates=[s for s in result.slides if s.role=='content' and not s.dataset_id and not s.asset_id]
    target=round(len(candidates)*.4)
    have=sum(s.visual in {'sequence','list','hierarchy'} for s in candidates)
    ranked=sorted((s for s in candidates if s.visual=='none'),key=lambda s:(0 if re.search(r'PRAM|DAG|Work|Span|планиров|алгоритм|сравн|этап',s.title+' '+s.message,re.I) else 1,s.id))
    for slide in ranked[:max(0,target-have)]:slide.visual=_visual_kind(slide)
    return result


def _archetype(slide,index=0,count=1):
    text=(slide.title+' '+slide.message).lower()
    if slide.role=='cover' or index==0:return 'cover'
    if slide.role=='divider':return 'divider'
    if index==count-1 or re.search(r'вывод|итог|заключен|резюме',text):return 'summary'
    if re.search(r'задач|упражнен|самопровер|домашн',text):return 'exercise'
    if slide.visual=='image':return 'illustration'
    if re.search(r'формул|теорем|оценк|границ|θ\s*\(|o\s*\(',text,re.I):return 'formula'
    if re.search(r'пример|case|сценари',text):return 'example'
    if slide.visual=='sequence' or re.search(r'этап|алгоритм|процесс|порядок|scheduler',text):return 'process'
    if slide.visual=='list' or re.search(r'различ|сравн|versus| vs\b|режим',text):return 'comparison'
    return 'explanation'


def enrich_plan(plan: PresentationPlan,content: ContentIR) -> PresentationPlan:
    """Add a source-backed editorial structure used by the layout director."""
    result=plan.model_copy(deep=True);claims={c.id:c for c in content.claims}
    for index,slide in enumerate(result.slides):
        slide.archetype=_archetype(slide,index,len(result.slides))
        if not slide.support_points:slide.support_points=_supporting_points(slide,claims,3)
        slide.support_points=[_clean_text(point) for point in slide.support_points[:3] if point.strip()]
        if not slide.takeaway:
            sentences=[x.strip() for x in re.split(r'(?<=[.!?])\s+',slide.message) if x.strip()]
            slide.takeaway=_clean_text(sentences[-1] if sentences else slide.message)
        else:slide.takeaway=_clean_text(slide.takeaway)
        if not slide.visual_brief:
            slide.visual_brief=_complete_excerpt(slide.title+': '+slide.message,260)
        else:slide.visual_brief=_complete_excerpt(slide.visual_brief,260)
        slide.message=_clean_text(slide.message)
    return result


def validate_plan(plan: PresentationPlan, content: ContentIR, count: int):
    if plan.status=='needs_input': raise NeedsInput(plan.reason)
    if len(plan.slides)!=count: raise ValueError('Model did not preserve requested slide count')
    if len({s.id for s in plan.slides})!=count: raise ValueError('Duplicate slide IDs')
    claims={c.id for c in content.claims}; datasets={d.id for d in content.datasets}; assets={a.id for a in content.assets}
    used=set();fingerprints=set();titles=set();messages=set()
    source_numbers=set(re.findall(r'\d+(?:[.,]\d+)?', ' '.join(c.text for c in content.claims)+ ' '.join(str(x) for d in content.datasets for v in d.series.values() for x in v)))
    for slide in plan.slides:
        if not set(slide.claim_ids)<=claims: raise ValueError('Unknown claim reference')
        if slide.dataset_id and slide.dataset_id not in datasets: raise ValueError('Unknown dataset reference')
        if slide.asset_id and slide.asset_id not in assets: raise ValueError('Unknown asset reference')
        if slide.visual in {'chart','table'} and not slide.dataset_id: raise ValueError('Numeric visuals require a source dataset')
        if slide.visual=='image' and not slide.asset_id: raise ValueError('Image visual requires an asset')
        normalized_title=re.sub(r'\W+',' ',slide.title.casefold()).strip()
        normalized_message=re.sub(r'\W+',' ',slide.message.casefold()).strip()
        if normalized_title in titles:raise ValueError('Plan contains duplicate slide title: '+slide.title)
        if normalized_message in messages:raise ValueError('Plan contains duplicate main idea: '+slide.title)
        titles.add(normalized_title);messages.add(normalized_message)
        fingerprint=(normalized_title,normalized_message)
        if fingerprint in fingerprints:raise ValueError('Plan contains duplicate slides: '+slide.title)
        fingerprints.add(fingerprint)
        visible=' '.join([slide.title,slide.message,slide.takeaway,*slide.support_points])
        if re.search(r'…|\.\.\.',visible):raise ValueError('Plan contains mechanically truncated text: '+slide.title)
        # Prompts target 150/90/110 characters. These wider guardrails absorb
        # small-model counting variance while still rejecting prose that cannot
        # reasonably fit any composition. Layouts select whole authored fields;
        # they never clip a field to meet these limits.
        if len(slide.message)>220:raise ValueError(f'Plan message exceeds 220 characters on slide: {slide.title}')
        if slide.takeaway and len(slide.takeaway)>130:raise ValueError(f'Plan takeaway exceeds 130 characters on slide: {slide.title}')
        if any(len(point)>150 for point in slide.support_points):raise ValueError(f'Plan support point exceeds 150 characters on slide: {slide.title}')
        invented=set(re.findall(r'\d+(?:[.,]\d+)?',visible))-source_numbers
        if invented: raise ValueError('Plan introduces numbers absent from source: '+', '.join(sorted(invented)))
        used.update(slide.claim_ids)
    required={c.id for c in content.claims if c.required}
    if not required<=used: raise ValueError('Plan omits mandatory claims: '+', '.join(sorted(required-used)))


def disambiguate_titles(plan: PresentationPlan, existing_titles=()) -> PresentationPlan:
    """Replace repeated labels with a unique source-backed statement.

    A title can collide even when the slide messages are different. In that case
    the message itself is the safest label because it has already been checked
    against source numbers and claim references.
    """
    result=plan.model_copy(deep=True)
    used={re.sub(r'\W+',' ',value.casefold()).strip() for value in existing_titles}
    for slide in result.slides:
        normalized=re.sub(r'\W+',' ',slide.title.casefold()).strip()
        if normalized in used:
            candidates=[re.split(r'(?<=[.!?])\s+',slide.message)[0],slide.takeaway,*slide.support_points]
            replacement=next((_complete_excerpt(value,96) for value in candidates if value.strip() and re.sub(r'\W+',' ',_complete_excerpt(value,96).casefold()).strip() not in used),None)
            if replacement:slide.title=replacement;normalized=re.sub(r'\W+',' ',replacement.casefold()).strip()
        used.add(normalized)
    return result


def plan_with_model(gateway,content,request):
    import tempfile
    from pathlib import Path
    from PIL import Image,ImageDraw
    claim_lengths={c.id:len(c.text) for c in content.claims}
    required_set={c.id for c in content.claims if c.required}
    candidates=[]
    for asset in content.assets:
        if asset.purpose!='output':continue
        path=artifact_path(asset.path)
        if not path.exists():continue
        shortest=min((claim_lengths.get(cid,10_000) for cid in asset.claim_ids),default=10_000)
        candidates.append((shortest,path.stat().st_size,bool(set(asset.claim_ids)&required_set),asset,path))
    limit=max(0,int(os.environ.get('MODEL_MAX_SOURCE_IMAGES','4')))
    selected=sorted(candidates,key=lambda item:(item[0]>=80,-item[1],item[3].id))[:limit]
    catalog_limit=max(limit,max(0,int(os.environ.get('MODEL_MAX_PLANNING_ASSETS','8'))))
    catalog=sorted(candidates,key=lambda item:(not item[2],-item[1],item[3].id))[:catalog_limit]
    planning_content=content.model_copy(deep=True)
    selected_claims=planning_claims(content,request.brief)
    selected_ids={claim.id for claim in selected_claims}
    # Source order gives the planner a stable narrative spine. Ranking is used
    # only to choose what fits in the bounded context.
    planning_content.claims=[claim for claim in content.claims if claim.id in selected_ids]
    planning_assets={item[3].id:item[3] for item in catalog+selected}
    planning_content.assets=list(planning_assets.values())
    required=[c.id for c in content.claims if c.required]
    payload={'content':planning_content.model_dump(),'brief':request.brief,'purpose':request.purpose,'slide_count':request.slide_count,'required_claim_ids':required,'source_image_order':[item[3].id for item in selected]}
    with tempfile.TemporaryDirectory(prefix='vktech-source-') as temp:
        model_images=[]
        if selected:
            width,height,cols=320,200,2;rows=(len(selected)+cols-1)//cols
            sheet=Image.new('RGB',(width*cols,height*rows),'white');draw=ImageDraw.Draw(sheet)
            for index,item in enumerate(selected):
                with Image.open(item[4]) as source:
                    thumb=source.convert('RGB');thumb.thumbnail((width-10,height-30),Image.Resampling.LANCZOS)
                x=(index%cols)*width;y=(index//cols)*height
                sheet.paste(thumb,(x+(width-thumb.width)//2,y+25));draw.text((x+5,y+5),item[3].id,fill='black')
            contact=Path(temp)/'source-assets.png';sheet.save(contact,'PNG',optimize=True);model_images=[contact]
            payload['visual_input']='One contact sheet. Labels are exact asset_id values from source_image_order.'
        def request_plan(batch_payload,batch_content,batch_count,existing_titles=(),existing_messages=(),assignments=()):
            error=None
            for attempt in range(3):
                plan=normalize_plan(gateway.structured('planning',batch_payload,PresentationPlan,images=model_images))
                # IDs are transport metadata, not authored content. Small local
                # models often repeat them even when every slide is distinct.
                # Assign deterministic batch IDs here; global IDs are assigned
                # again when the source-ordered batches are combined.
                for local_index,slide in enumerate(plan.slides):slide.id=f'slide-{local_index+1}'
                plan=disambiguate_titles(plan,existing_titles)
                try:
                    validate_plan(plan,batch_content,batch_count)
                    if assignments:
                        for slide,assignment in zip(plan.slides,assignments):
                            if set(slide.claim_ids)!=set(assignment['claim_ids']):
                                raise ValueError(f"Slide position {assignment['position']} must use exactly these claim_ids: {', '.join(assignment['claim_ids'])}")
                    forbidden_titles={re.sub(r'\W+',' ',value.casefold()).strip() for value in existing_titles}
                    forbidden_messages={re.sub(r'\W+',' ',value.casefold()).strip() for value in existing_messages}
                    conflict=next((slide.title for slide in plan.slides if re.sub(r'\W+',' ',slide.title.casefold()).strip() in forbidden_titles),None)
                    if conflict:raise ValueError('Plan repeats a title from an earlier segment: '+conflict)
                    conflict=next((slide.title for slide in plan.slides if re.sub(r'\W+',' ',slide.message.casefold()).strip() in forbidden_messages),None)
                    if conflict:raise ValueError('Plan repeats a main idea from an earlier segment: '+conflict)
                    return plan
                except NeedsInput:
                    raise
                except ValueError as exc:
                    error=exc
                    log.warning('Planning validation attempt %s failed: %s',attempt+1,exc)
                    if attempt<2:
                        batch_payload['validation_feedback']=str(exc)
                        batch_payload['correction_attempt']=attempt+2
                        batch_payload['previous_slide_outline']=[
                            {'position':index+1,'title':slide.title,'message':slide.message,'claim_ids':slide.claim_ids}
                            for index,slide in enumerate(plan.slides)
                        ]
                        batch_payload['correction']='Return a complete corrected plan. Inspect previous_slide_outline and replace every repeated title or main idea with a distinct source-backed teaching step. Do not return the same outline again. Every required_claim_id must occur in at least one slide.claim_ids.'
            raise error

        # Small local models tend to repeat a short tail when constrained to one
        # very large JSON object. Plan long decks in coherent source-ordered
        # segments, then validate the combined story as one presentation.
        if request.slide_count>12:
            batch_total=(request.slide_count+5)//6
            base=request.slide_count//batch_total;extra=request.slide_count%batch_total
            counts=[base+(index<extra) for index in range(batch_total)]
            claims=planning_content.claims;combined=[];existing_titles=[];existing_messages=[]
            claim_groups=[]
            if len(claims)>=request.slide_count:
                for position in range(request.slide_count):
                    start=len(claims)*position//request.slide_count;end=len(claims)*(position+1)//request.slide_count
                    claim_groups.append(claims[start:end])
            for index,batch_count in enumerate(counts):
                first=len(combined);last=first+batch_count
                if claim_groups:
                    groups=claim_groups[first:last];batch_claims=[claim for group in groups for claim in group]
                    assignments=[{'position':first+local+1,'claim_ids':[claim.id for claim in group]} for local,group in enumerate(groups)]
                else:
                    start=len(claims)*index//batch_total;end=len(claims)*(index+1)//batch_total
                    batch_claims=claims[start:end];assignments=[]
                batch_content=planning_content.model_copy(deep=True);batch_content.claims=batch_claims
                batch_required=[claim.id for claim in batch_content.claims if claim.required]
                batch_payload={**payload,'content':batch_content.model_dump(),'slide_count':batch_count,'required_claim_ids':batch_required,
                    'plan_segment':{'index':index+1,'total':batch_total,'first_position':first+1,'last_position':last,'existing_titles':existing_titles},
                    'slide_assignments':assignments}
                batch=request_plan(batch_payload,batch_content,batch_count,existing_titles,existing_messages,assignments)
                for slide in batch.slides:
                    slide.id=f'slide-{len(combined)+1}';combined.append(slide)
                existing_titles.extend(slide.title for slide in batch.slides)
                existing_messages.extend(slide.message for slide in batch.slides)
            plan=normalize_plan(PresentationPlan(slides=combined))
            validate_plan(plan,content,request.slide_count)
            return enrich_plan(plan,content)

        plan=request_plan(payload,content,request.slide_count)
        return enrich_plan(plan,content)


def regenerate_slide_with_model(gateway,slide,content,instruction):
    claims={c.id:c for c in content.claims};selected=[claims[cid] for cid in slide.claim_ids if cid in claims]
    payload={'instruction':instruction,'current_slide':slide.model_dump(),'claims':[c.model_dump() for c in selected]}
    revision=gateway.structured('regenerate_slide',payload,SlideRevision)
    source_numbers=set(re.findall(r'\d+(?:[.,]\d+)?',' '.join(c.text for c in selected)))
    visible=' '.join([revision.title,revision.message,revision.takeaway,*revision.support_points])
    if re.search(r'…|\.\.\.',visible):raise ValueError('Slide regeneration returned mechanically truncated text')
    if len(revision.message)>220 or len(revision.takeaway)>130 or any(len(point)>150 for point in revision.support_points):
        raise ValueError('Slide regeneration returned text that does not fit the selected density modes')
    invented=set(re.findall(r'\d+(?:[.,]\d+)?',visible))-source_numbers
    if invented:raise ValueError('Slide regeneration introduces numbers absent from source: '+', '.join(sorted(invented)))
    result=slide.model_copy(deep=True)
    for field in ('title','message','support_points','takeaway','visual','archetype','visual_brief'):
        setattr(result,field,getattr(revision,field))
    return result


def usable_prototypes(design):
    candidates=[]
    instruction=re.compile(r'правила|инструкция|типограф|палитр|используйте|рекоменду|макет|как использовать',re.I)
    for p in design.prototypes:
        title=next((s for s in p.slots if s.role=='title'),None)
        bodies=body_slots(p)
        if title and title.box.w>.35 and title.box.h>.06 and bodies:
            visual_area=sum(s.box.w*s.box.h for s in p.slots if s.role=='visual')
            central_decor=sum(s.box.w*s.box.h for s in p.slots if s.role=='decor' and s.box.y<.9 and s.box.w*s.box.h>.01)
            score=max(s.box.w*s.box.h for s in bodies)+.12*sum(s.box.w*s.box.h for s in bodies)
            score-=.7*visual_area+.5*central_decor
            if visual_area>.04 or central_decor>.04:score-=1
            score+=.18 if len(bodies)<=2 else 0
            score+=.12 if min(s.style.size for s in bodies)>=16 else 0
            score-=.8 if instruction.search(' '.join(s.source_text[:180] for s in p.slots if s.role=='title')) else 0
            score-=.02*len(bodies)
            score-=1 if any(re.search(r'\bpadding\b|\bmargin\b|\bbody\s*\{',s.source_text) for s in bodies) else 0
            candidates.append((score,p))
    if not candidates: raise NeedsInput('Template has no usable title/body composition. Add a sample content slide.')
    return [p for _,p in sorted(candidates,key=lambda x:(-x[0],x[1].id))]


def branded_prototypes(design):
    """Return sparse coloured layouts whose master graphics can frame new content."""
    instruction=re.compile(r'правила|инструкция|типограф|палитр|используйте|рекоменду|макет|как использовать',re.I)
    candidates=[]
    for p in design.prototypes:
        title=next((s for s in p.slots if s.role=='title'),None)
        if not title or p.background.upper() in {'FFFFFF','FEFEFE'}:continue
        bodies=[s for s in p.slots if s.role=='body']
        visual=max((s.box.w*s.box.h for s in p.slots if s.role=='visual'),default=0)
        body=max((s.box.w*s.box.h for s in bodies),default=0)
        if visual>.09 or body>.07:continue
        if instruction.search(' '.join(s.source_text[:180] for s in p.slots if s.role=='title')):continue
        score=title.box.w*title.box.h-.5*visual-.35*body
        candidates.append((score,p))
    return [p for _,p in sorted(candidates,key=lambda x:(-x[0],x[1].id))]


def body_slots(p):
    return [s for s in p.slots if s.role=='body' and s.box.w>.25 and s.box.h>.1 and 12<=s.style.size<=96]


def _scale_size(design, minimum, preferred):
    valid=sorted(s for s in design.font_sizes if s>=minimum and s<=preferred)
    return min(valid,key=lambda s:abs(s-preferred)) if valid else preferred


def _readable_color(color,background,design,threshold=4.5):
    from .audit import contrast
    candidates=list(dict.fromkeys([color,*design.palette,'202020','FFFFFF']))
    valid=[c for c in candidates if isinstance(c,str) and re.fullmatch(r'[0-9A-Fa-f]{6}',c)]
    best=max(valid,key=lambda c:contrast(c,background),default='202020')
    return color if re.fullmatch(r'[0-9A-Fa-f]{6}',color or '') and contrast(color,background)>=threshold else best


def _style(base,design,background,role='body',align='left'):
    result=base.model_copy(deep=True);result.align=align;result.fill=None
    from .audit import font_for
    fallback=os.environ.get('FONT_FALLBACK','Arial')
    if font_for(result.font,18) is None and font_for(fallback,18) is not None:result.font=fallback
    result.size=_scale_size(design,28,34 if role=='title' else 20) if role=='title' else _scale_size(design,18,20)
    result.bold=role=='title';result.color=_readable_color(result.color,background,design,3 if role=='title' else 4.5)
    return result


def _clean_text(value):
    return re.sub(r'\s+',' ',value).strip(' •–—-')


def _complete_excerpt(value,limit=105):
    """Select complete sentences or clauses without clipping characters."""
    value=_clean_text(value)
    if len(value)<=limit:return value
    units=[part.strip() for part in re.split(r'(?<=[.!?;])\s+|\s+[—–]\s+',value) if part.strip()]
    chosen=[]
    for unit in units:
        candidate=' '.join(chosen+[unit])
        if chosen and len(candidate)>limit:break
        chosen.append(unit)
        if len(candidate)>=limit:break
    return ' '.join(chosen) if chosen else value


def _semantic_lead(slide,variant):
    """Choose an authored complete thought for each density mode."""
    if variant in {'A','B'}:
        candidates=[slide.takeaway,*slide.support_points,slide.message]
        return _clean_text(next((value for value in candidates if value and value.strip()),slide.message))
    return _clean_text(slide.message)


def _diagram_items(ps,claims):
    query=set(re.findall(r'[\w+#<>]{4,}',(ps.title+' '+ps.message).lower()))
    message_parts=[x for x in re.split(r'\n+|(?<=[.!?;])\s+|\s+[—–]\s+',ps.message) if x.strip()]
    primary=([ps.title]+message_parts if ps.visual=='hierarchy' else message_parts)
    source=[]
    for cid in ps.claim_ids:
        claim=claims.get(cid)
        if not claim:continue
        source.extend(x for x in re.split(r'\n+|(?<=[.!?;])\s+',claim.text) if x.strip() and not re.search(r'^(ответ|вопрос|решите|домашн|самопровер|конспект лекции|параллельные алгоритмы\s*\|)|^\d+[.)]?$|\b\d{1,2}:\d{2}\b',x.strip(),re.I))
    def cleaned(values):
        result=[]
        for text in values:
            text=_complete_excerpt(text)
            if re.search(r'…|\.\.\.',text):continue
            if text and not re.fullmatch(r'\d+[.)]?',text) and text.casefold() not in {x.casefold() for x in result}:result.append(text)
        return result
    chosen=cleaned(primary)
    extras=[]
    for text in cleaned(source):
        words=set(re.findall(r'[\w+#<>]{4,}',text.lower()));score=len(words&query)
        if score:extras.append((-score,len(text),text))
    for _,_,text in sorted(extras):
        if text.casefold() not in {x.casefold() for x in chosen}:chosen.append(text)
        if len(chosen)>=4:break
    unique=[]
    for text in chosen:
        text=_complete_excerpt(text)
        if text and not re.fullmatch(r'\d+[.)]?',text) and text.casefold() not in {x.casefold() for x in unique}:unique.append(text)
    def rank(text):
        words=set(re.findall(r'[\w+#<>]{4,}',text.lower()))
        return (-len(words&query),len(text))
    items=unique[:4] if len(unique)>=2 else sorted(unique,key=rank)[:4]
    if len(items)<2:items=[_complete_excerpt(ps.title),_complete_excerpt(ps.message)]
    return items


def _supporting_points(ps,claims,limit=3):
    """Select concise source-backed details which add to, rather than repeat, the lead."""
    message_words=set(re.findall(r'[\w+#<>]{4,}',ps.message.lower()))
    query=set(re.findall(r'[\w+#<>]{4,}',(ps.title+' '+ps.message).lower()))
    found=[]
    for cid in ps.claim_ids:
        claim=claims.get(cid)
        if not claim:continue
        for part in re.split(r'\n+|(?<=[.!?;])\s+',claim.text):
            part=re.sub(r'^\s*[•–—*-]\s*','',part).strip()
            if not part or re.search(r'^(ответ|вопрос|решите|домашн|самопровер|конспект лекции|параллельные алгоритмы\s*\|)|^\d+[.)]?$|\b\d{1,2}:\d{2}\b',part,re.I):continue
            if re.search(r'…|\.\.\.',part):continue
            words=set(re.findall(r'[\w+#<>]{4,}',part.lower()))
            if not words:continue
            overlap=len(words&message_words)/max(1,len(words))
            if overlap>.72:continue
            found.append((-len(words&query),len(part),_complete_excerpt(part,105)))
    result=[]
    for _,_,text in sorted(found):
        if text.casefold() not in {x.casefold() for x in result}:result.append(text)
        if len(result)>=limit:break
    return result


def _dark_background(color):
    from .audit import contrast
    return contrast('FFFFFF',color)>=3.2


def fit_node(node,design):
    """Choose a size from the template scale using measured or conservative metrics.

    Missing fonts remain unknown in the audit. Fallback estimates only guide layout.
    """
    from .audit import font_for
    from PIL import ImageFont
    from .settings import ROOT
    minimum=28 if node.role=='title' else 18
    sizes={node.style.size}|{s for s in design.font_sizes if minimum<=s<node.style.size}|{float(minimum)}
    for size in sorted((s for s in sizes if s<=node.style.size),reverse=True):
        font=font_for(node.style.font,max(1,round(size*96/72)))
        if font is None:
            font=ImageFont.load_default(size=max(1,round(size*96/72)))
        width=node.box.w*design.width/914400*96;lines=0
        for paragraph in node.text.split('\n'):
            current='';lines+=1
            for word in paragraph.split():
                candidate=(current+' '+word).strip()
                if font.getlength(candidate)>width and current:lines+=1;current=word
                else:current=candidate
        height=lines*size*96/72*1.25/(design.height/914400*96)
        if height<=node.box.h:
            node.style.size=size;return
    raise NeedsInput(f'Content does not fit the template slot on {node.id}; reduce text or change composition.')


def build_scenes(design: DesignIR, content: ContentIR, plan: PresentationPlan, job_id: str):
    plain=usable_prototypes(design)
    branded=branded_prototypes(design)
    dark=[p for p in branded if _dark_background(p.background)]
    light=[p for p in branded if not _dark_background(p.background)]
    content_light=[p for p in light if next((s.box.y for s in p.slots if s.role=='title'),1)<.2]
    claims={c.id:c for c in content.claims};datasets={d.id:d for d in content.datasets};assets={a.id:a for a in content.assets}
    style_proto=plain[0]
    style_title=next(s for s in style_proto.slots if s.role=='title')
    style_body=max(body_slots(style_proto),key=lambda s:s.box.w*s.box.h).style
    surface='EBF3F9' if 'EBF3F9' in design.palette else 'E8EEF6'
    accent=next((c for c in ('0077FF','2688EB','005FF9') if c in design.palette),'0077FF')
    scenes=[]
    for vi,variant in enumerate(('A','B','C')):
        slides=[]
        for si,ps in enumerate(plan.slides):
            visual='table' if ps.dataset_id and variant=='C' else 'chart' if ps.dataset_id else ps.visual
            is_cover=ps.role=='cover' or si==0
            last=si==len(plan.slides)-1
            if (is_cover or last) and dark:
                p=dark[(si+vi)%len(dark)]
            elif variant=='C' or (variant=='B' and visual!='none'):
                p=plain[(si+vi)%min(3,len(plain))]
            elif ps.role=='divider' and (light or dark):
                pool=light or dark;p=pool[(si+vi)%len(pool)]
            elif visual=='none' and (content_light or light):
                pool=content_light or light;p=pool[(si+vi)%len(pool)]
            elif visual in {'sequence','list','hierarchy'} and (content_light or light) and (si+vi)%2==0:
                pool=content_light or light;p=pool[(si+vi)%len(pool)]
            else:
                p=plain[(si+vi)%min(3,len(plain))]
            title_style=_style(style_title.style,design,p.background,'title')
            body_style=_style(style_body,design,p.background)
            supports=ps.support_points or _supporting_points(ps,claims,3)
            lead_text=_semantic_lead(ps,variant)
            nodes=[]
            if is_cover or last:
                title_size={'A':48,'B':42,'C':36}[variant];body_size={'A':24,'B':22,'C':18}[variant]
                title_style.size=_scale_size(design,32,title_size);body_style.size=_scale_size(design,18,body_size)
                title_style.align=body_style.align='left';body_style.bold=True
                if _dark_background(p.background):title_style.color=body_style.color='FFFFFF'
                cover_w=.58 if p in branded else .88
                nodes.append(Node(id=f'{ps.id}-title',kind='text',role='title',box=Box(x=.06,y=.18,w=cover_w,h=.36),style=title_style,text=ps.title))
                nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=.59,w=cover_w,h=.29),style=body_style,text=lead_text,claim_ids=ps.claim_ids))
            else:
                branded_slide=p in branded
                content_right=.70 if branded_slide else .94
                title_h=.19 if len(ps.title)<=50 else .28
                title_style.size=_scale_size(design,28,{'A':38,'B':34,'C':32}[variant])
                nodes.append(Node(id=f'{ps.id}-title',kind='text',role='title',box=Box(x=.06,y=.07,w=content_right-.06,h=title_h),style=title_style,text=ps.title))
                nodes.append(Node(id=f'{ps.id}-accent',kind='text',role='accent',box=Box(x=.06,y=.07+title_h+.01,w=.10,h=.012),style=Style(font=body_style.font,size=18,color=accent,fill=accent),text=''))
                top=.07+title_h+.07
                if visual!='none':
                    if variant=='A':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,22,26)
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=content_right-.06,h=.23),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        vb=Box(x=.11,y=top+.27,w=content_right-.16,h=max(.25,.86-(top+.27)))
                    elif variant=='B':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,20,22)
                        left=.34 if branded_slide else .35
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=left,h=.23),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        if supports:
                            support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                            nodes.append(Node(id=f'{ps.id}-support-1',kind='text',role='support',box=Box(x=.06,y=top+.29,w=left,h=.20),style=support_style,text=supports[0],claim_ids=ps.claim_ids))
                        vb=Box(x=.46,y=top,w=content_right-.46,h=.50)
                    else:
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,18,18)
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=.45,h=.17),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                        for idx,text in enumerate(supports[:2]):
                            nodes.append(Node(id=f'{ps.id}-support-{idx+1}',kind='text',role='support',box=Box(x=.06,y=top+.22+idx*.17,w=.45,h=.14),style=support_style,text=text,claim_ids=ps.claim_ids))
                        vb=Box(x=.57,y=top,w=.37,h=.47)
                else:
                    lead_w=content_right-.07
                    if variant=='A':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,22,28)
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=lead_w,h=.34),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                    elif variant=='B':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,20,22)
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=lead_w,h=.20),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        if supports:
                            support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                            cols=min(2,len(supports));col_w=(lead_w-.05)/cols
                            for idx,text in enumerate(supports[:2]):
                                x=.06+idx*(col_w+.05)
                                nodes.append(Node(id=f'{ps.id}-support-accent-{idx+1}',kind='text',role='accent',box=Box(x=x,y=top+.26,w=.055,h=.009),style=Style(font=body_style.font,size=18,color=accent,fill=accent),text=''))
                                nodes.append(Node(id=f'{ps.id}-support-{idx+1}',kind='text',role='support',box=Box(x=x,y=top+.30,w=col_w,h=.21),style=support_style,text=text,claim_ids=ps.claim_ids))
                    else:
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,18,18)
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=lead_w,h=.15),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                        details=supports[:3] or ([ps.takeaway] if ps.takeaway else [])
                        cols=max(1,len(details));col_w=(lead_w-.04*(cols-1))/cols
                        for idx,text in enumerate(details):
                            x=.06+idx*(col_w+.04)
                            nodes.append(Node(id=f'{ps.id}-support-accent-{idx+1}',kind='text',role='accent',box=Box(x=x,y=top+.19,w=.045,h=.008),style=Style(font=body_style.font,size=18,color=accent,fill=accent),text=''))
                            nodes.append(Node(id=f'{ps.id}-support-{idx+1}',kind='text',role='support',box=Box(x=x,y=top+.22,w=col_w,h=.24),style=support_style,text=text,claim_ids=ps.claim_ids))
            if visual!='none' and not is_cover and not last:
                kind='diagram' if visual in {'sequence','list','hierarchy'} else visual
                items=_diagram_items(ps,claims)[:{'A':2,'B':3,'C':4}[variant]]
                data=datasets[ps.dataset_id].model_dump() if ps.dataset_id else assets[ps.asset_id].model_dump() if ps.asset_id else {'layout':visual,'items':items,'accent':accent,'surface':surface}
                visualstyle=_style(style_body,design,p.background);visualstyle.size=_scale_size(design,18,18)
                visualstyle.fill=surface
                nodes.append(Node(id=f'{ps.id}-visual',kind=kind,role='visual',box=vb,style=visualstyle,data=data,claim_ids=ps.claim_ids if kind=='diagram' else []))
            for node in nodes:
                visible_parts=[node.text] if node.kind=='text' else list(node.data.get('items',[])) if node.kind=='diagram' else []
                if any(re.search(r'…|\.\.\.',str(value)) for value in visible_parts):
                    raise NeedsInput(f'Слайд «{ps.title}» содержит незавершённый текст. Перегенерируйте содержание слайда.')
                if node.kind=='text' and node.text.strip():fit_node(node,design)
            slides.append(SceneSlide(id=ps.id,title=ps.title,role=ps.role,prototype_id=p.id,background=p.background,nodes=nodes))
        scenes.append(SceneIR(id=f'{job_id}-{variant}',variant=variant,template_id=design.id,content_id=content.id,width=design.width,height=design.height,slides=slides))
    return scenes
