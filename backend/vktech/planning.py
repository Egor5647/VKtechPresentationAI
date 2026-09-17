from __future__ import annotations
import hashlib
import logging
import os
import re
from .settings import artifact_path
from .contracts import PresentationPlan, ContentIR, DesignIR, SceneIR, SceneSlide, Node, Style, Box

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
    target=round(len(candidates)*.5)
    have=sum(s.visual in {'sequence','list','hierarchy'} for s in candidates)
    ranked=sorted((s for s in candidates if s.visual=='none'),key=lambda s:(0 if re.search(r'PRAM|DAG|Work|Span|планиров|алгоритм|сравн|этап',s.title+' '+s.message,re.I) else 1,s.id))
    for slide in ranked[:max(0,target-have)]:slide.visual=_visual_kind(slide)
    return result


def validate_plan(plan: PresentationPlan, content: ContentIR, count: int):
    if plan.status=='needs_input': raise NeedsInput(plan.reason)
    if len(plan.slides)!=count: raise ValueError('Model did not preserve requested slide count')
    if len({s.id for s in plan.slides})!=count: raise ValueError('Duplicate slide IDs')
    claims={c.id for c in content.claims}; datasets={d.id for d in content.datasets}; assets={a.id for a in content.assets}
    used=set()
    source_numbers=set(re.findall(r'\d+(?:[.,]\d+)?', ' '.join(c.text for c in content.claims)+ ' '.join(str(x) for d in content.datasets for v in d.series.values() for x in v)))
    for slide in plan.slides:
        if not set(slide.claim_ids)<=claims: raise ValueError('Unknown claim reference')
        if slide.dataset_id and slide.dataset_id not in datasets: raise ValueError('Unknown dataset reference')
        if slide.asset_id and slide.asset_id not in assets: raise ValueError('Unknown asset reference')
        if slide.visual in {'chart','table'} and not slide.dataset_id: raise ValueError('Numeric visuals require a source dataset')
        if slide.visual=='image' and not slide.asset_id: raise ValueError('Image visual requires an asset')
        invented=set(re.findall(r'\d+(?:[.,]\d+)?',slide.title+' '+slide.message))-source_numbers
        if invented: raise ValueError('Plan introduces numbers absent from source: '+', '.join(sorted(invented)))
        used.update(slide.claim_ids)
    required={c.id for c in content.claims if c.required}
    if not required<=used: raise ValueError('Plan omits mandatory claims: '+', '.join(sorted(required-used)))


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
    planning_content.claims=planning_claims(content,request.brief)
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
        error=None
        for attempt in range(2):
            plan=normalize_plan(gateway.structured('planning',payload,PresentationPlan,images=model_images))
            try:
                validate_plan(plan,content,request.slide_count)
                return plan
            except NeedsInput:
                raise
            except ValueError as exc:
                error=exc
                log.warning('Planning validation attempt %s failed: %s',attempt+1,exc)
                if attempt==0:
                    payload['validation_feedback']=str(exc)
                    payload['correction']='Return a complete corrected plan. Every required_claim_id must occur in at least one slide.claim_ids.'
        raise error


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


def body_slots(p):
    return [s for s in p.slots if s.role=='body' and s.box.w>.25 and s.box.h>.1 and 12<=s.style.size<=96]


def _scale_size(design, minimum, preferred):
    valid=sorted(s for s in design.font_sizes if s>=minimum and s<=preferred*1.35)
    return min(valid,key=lambda s:abs(s-preferred)) if valid else preferred


def _readable_color(color,background,design,threshold=4.5):
    from .audit import contrast
    candidates=list(dict.fromkeys([color,*design.palette,'202020','FFFFFF']))
    valid=[c for c in candidates if isinstance(c,str) and re.fullmatch(r'[0-9A-Fa-f]{6}',c)]
    best=max(valid,key=lambda c:contrast(c,background),default='202020')
    return color if re.fullmatch(r'[0-9A-Fa-f]{6}',color or '') and contrast(color,background)>=threshold else best


def _style(base,design,background,role='body',align='left'):
    result=base.model_copy(deep=True);result.align=align;result.fill=None
    result.size=_scale_size(design,28,34 if role=='title' else 20) if role=='title' else _scale_size(design,18,20)
    result.bold=role=='title';result.color=_readable_color(result.color,background,design,3 if role=='title' else 4.5)
    return result


def _short(value,limit=72):
    value=re.sub(r'\s+',' ',value).strip(' •–—-')
    if len(value)<=limit:return value
    words=value.split();out=[]
    for word in words:
        if len(' '.join(out+[word]))>limit-1:break
        out.append(word)
    return (' '.join(out) or value[:limit-1]).rstrip(' ,;:.')+'…'


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
            text=_short(text)
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
        text=_short(text)
        if text and not re.fullmatch(r'\d+[.)]?',text) and text.casefold() not in {x.casefold() for x in unique}:unique.append(text)
    def rank(text):
        words=set(re.findall(r'[\w+#<>]{4,}',text.lower()))
        return (-len(words&query),len(text))
    items=unique[:4] if len(unique)>=2 else sorted(unique,key=rank)[:4]
    if len(items)<2:items=[_short(ps.title),_short(ps.message)]
    return items


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
    prototypes=usable_prototypes(design); claims={c.id:c for c in content.claims}; datasets={d.id:d for d in content.datasets}; assets={a.id:a for a in content.assets}
    scenes=[]
    for vi,variant in enumerate(('A','B','C')):
        slides=[]
        for si,ps in enumerate(plan.slides):
            # Use only the cleanest sample slides as branded backgrounds. Generated
            # content follows explicit readable frames instead of inheriting demo text.
            pool=prototypes[:min(3,len(prototypes))]
            p=pool[(si+vi)%len(pool)]
            title=next(s for s in p.slots if s.role=='title')
            bodies=sorted(body_slots(p),key=lambda s:(s.box.y,s.box.x))
            base=max(bodies,key=lambda s:s.box.w*s.box.h).style
            title_style=_style(title.style,design,p.background,'title')
            body_style=_style(base,design,p.background)
            surface='EBF3F9' if 'EBF3F9' in design.palette else 'E8EEF6'
            accent=next((c for c in ('0077FF','2688EB','005FF9') if c in design.palette),'0077FF')
            if ps.role=='cover':
                title_style.size=_scale_size(design,36,48);title_style.align='center'
                body_style.size=_scale_size(design,20,24);body_style.align='center'
                nodes=[Node(id=f'{ps.id}-accent',kind='text',role='accent',box=Box(x=.08,y=.12,w=.16,h=.015),style=Style(font=body_style.font,size=18,color=accent,fill=accent),text=''),Node(id=f'{ps.id}-title',kind='text',role='title',box=Box(x=.08,y=.17,w=.84,h=.31),style=title_style,text=ps.title),Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.15,y=.55,w=.70,h=.20),style=body_style,text=ps.message,claim_ids=ps.claim_ids)]
            else:
                nodes=[Node(id=f'{ps.id}-title',kind='text',role='title',box=Box(x=.07,y=.07,w=.86,h=.19),style=title_style,text=ps.title),Node(id=f'{ps.id}-accent',kind='text',role='accent',box=Box(x=.07,y=.265,w=.12,h=.012),style=Style(font=body_style.font,size=18,color=accent,fill=accent),text='')]
            visual=ps.visual
            if ps.dataset_id: visual='table' if variant=='C' else 'chart'
            if ps.role!='cover':
                if visual!='none' and variant=='A':
                    nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.08,y=.29,w=.84,h=.23),style=body_style,text=ps.message,claim_ids=ps.claim_ids));vb=Box(x=.08,y=.57,w=.84,h=.28)
                elif visual!='none' and variant=='B':
                    small=body_style.model_copy(deep=True);small.size=_scale_size(design,18,18);small.align='center'
                    nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.12,y=.72,w=.76,h=.17),style=small,text=ps.message,claim_ids=ps.claim_ids));vb=Box(x=.08,y=.29,w=.84,h=.37)
                elif visual!='none':
                    body_style.fill=surface
                    nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.07,y=.31,w=.36,h=.48),style=body_style,text=ps.message,claim_ids=ps.claim_ids));vb=Box(x=.50,y=.31,w=.43,h=.48)
                else:
                    body_style.size=_scale_size(design,20,24 if variant=='B' else 20);body_style.align='center' if variant=='B' else 'left'
                    body_style.fill=surface
                    nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.11 if variant=='B' else .08,y=.34,w=.78 if variant=='B' else .72,h=.32),style=body_style,text=ps.message,claim_ids=ps.claim_ids))
            if visual!='none' and ps.role!='cover':
                kind='smartart' if visual in {'sequence','list','hierarchy'} else visual
                data=datasets[ps.dataset_id].model_dump() if ps.dataset_id else assets[ps.asset_id].model_dump() if ps.asset_id else {'layout':visual,'items':_diagram_items(ps,claims)}
                # The comparison composition gives the visual half of the slide.
                # PowerPoint applies its own native SmartArt layout and can ignore
                # a cached 2x2 drawing, so use the stable vertical list algorithm.
                if kind=='smartart' and variant=='C' and len(data.get('items',[]))>2:
                    data['layout']='list'
                visualstyle=_style(base,design,p.background);visualstyle.size=_scale_size(design,16,18)
                from .audit import contrast
                visualstyle.fill='E8EEF6' if contrast(visualstyle.color,'E8EEF6')>=4.5 else '17324D'
                nodes.append(Node(id=f'{ps.id}-visual',kind=kind,role='visual',box=vb,style=visualstyle,data=data,claim_ids=ps.claim_ids if kind=='smartart' else []))
            title_bottom=nodes[0].box.y+nodes[0].box.h+.015
            for node in nodes:
                if node.role=='body' and node.box.x<nodes[0].box.x+nodes[0].box.w and node.box.x+node.box.w>nodes[0].box.x and node.box.y<title_bottom:
                    delta=title_bottom-node.box.y;node.box.y=title_bottom;node.box.h=max(.03,node.box.h-delta)
            for node in nodes:
                if node.kind=='text' and node.text.strip():fit_node(node,design)
            slides.append(SceneSlide(id=ps.id,title=ps.title,role=ps.role,prototype_id=p.id,background=p.background,nodes=nodes))
        scenes.append(SceneIR(id=f'{job_id}-{variant}',variant=variant,template_id=design.id,content_id=content.id,width=design.width,height=design.height,slides=slides))
    return scenes
