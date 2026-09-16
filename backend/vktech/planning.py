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


def normalize_plan(plan: PresentationPlan) -> PresentationPlan:
    """Remove incomplete optional visual intents without changing sourced claims."""
    result=plan.model_copy(deep=True)
    for slide in result.slides:
        if slide.visual=='image' and not slide.asset_id:slide.visual='none'
        if slide.visual in {'chart','table'} and not slide.dataset_id:slide.visual='none'
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
    instruction=re.compile(r'правила|инструкция|типограф|палитр|шрифт|пример|используйте|рекоменду|макет|как использовать',re.I)
    for p in design.prototypes:
        title=next((s for s in p.slots if s.role=='title'),None)
        bodies=body_slots(p)
        if title and title.box.w>.35 and title.box.h>.06 and bodies:
            score=max(s.box.w*s.box.h for s in bodies)+.25*sum(s.box.w*s.box.h for s in bodies)
            score-=.8 if instruction.search(' '.join(s.source_text[:180] for s in p.slots if s.role=='title')) else 0
            score-=.02*len(bodies)
            score-=1 if any(re.search(r'\bpadding\b|\bmargin\b|\bbody\s*\{',s.source_text) for s in bodies) else 0
            candidates.append((score,p))
    if not candidates: raise NeedsInput('Template has no usable title/body composition. Add a sample content slide.')
    return [p for _,p in sorted(candidates,key=lambda x:(-x[0],x[1].id))]


def body_slots(p):
    return [s for s in p.slots if s.role=='body' and s.box.w>.25 and s.box.h>.1 and 10<=s.style.size<=36]


def fit_node(node,design):
    """Choose a size from the template scale using measured or conservative metrics.

    Missing fonts remain unknown in the audit. Fallback estimates only guide layout.
    """
    from .audit import font_for
    from PIL import ImageFont
    from .settings import ROOT
    sizes={node.style.size}|{s for s in design.font_sizes if 8<=s<node.style.size}|{8.0,9.0,10.0}
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
            # Select distinct original compositions, without template-specific IDs.
            pool=prototypes[:min(6,len(prototypes))]
            p=pool[(si+vi*max(1,len(pool)//3))%len(pool)]
            title=next(s for s in p.slots if s.role=='title')
            bodies=sorted(body_slots(p),key=lambda s:(s.box.y,s.box.x))
            nodes=[Node(id=f'{ps.id}-title',kind='text',role='title',box=title.box.model_copy(),style=title.style.model_copy(deep=True),text=ps.title,binding=title.id)]
            texts=[part.strip() for part in re.split(r'\n+|(?<=[.!?])\s+',ps.message) if part.strip()] or [ps.message]
            slot=max(bodies,key=lambda s:s.box.w*s.box.h)
            nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=slot.box.model_copy(),style=slot.style.model_copy(deep=True),text=ps.message,claim_ids=ps.claim_ids,binding=slot.id))
            visual=ps.visual
            if ps.dataset_id: visual='table' if variant=='C' else 'chart'
            elif visual in {'sequence','list','hierarchy'} and variant=='A': visual='none'
            if visual!='none':
                slot=max(bodies,key=lambda s:s.box.w*s.box.h)
                # Derive a safe content frame from the actual template title and body anchors.
                # Data widgets require a larger continuous area than a single annotation slot.
                left=max(.025,min(title.box.x,min(s.box.x for s in bodies)))
                right=min(.975,max(title.box.x+title.box.w,max(s.box.x+s.box.w for s in bodies)))
                top=max(title.box.y+title.box.h+.035,min(s.box.y for s in bodies));bottom=.88
                frame=Box(x=left,y=top,w=right-left,h=max(.1,bottom-top))
                body_nodes=[n for n in nodes if n.role=='body']
                if body_nodes:
                    # Consolidate claims to free a continuous visual region; preserve their order.
                    for n in body_nodes:nodes.remove(n)
                    st=min(bodies,key=lambda s:s.style.size).style.model_copy(deep=True)
                    nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=frame.x,y=frame.y,w=frame.w*.32,h=frame.h),style=st,text='\n'.join(texts),claim_ids=ps.claim_ids))
                    vb=Box(x=frame.x+frame.w*.38,y=frame.y,w=frame.w*.62,h=frame.h)
                else:vb=frame
                kind='smartart' if visual in {'sequence','list','hierarchy'} else visual
                data=datasets[ps.dataset_id].model_dump() if ps.dataset_id else assets[ps.asset_id].model_dump() if ps.asset_id else {'layout':visual,'items':texts}
                visualstyle=min(bodies,key=lambda s:s.style.size).style.model_copy(deep=True)
                nodes.append(Node(id=f'{ps.id}-visual',kind=kind,role='visual',box=vb,style=visualstyle,data=data,claim_ids=ps.claim_ids if kind=='smartart' else []))
            title_bottom=nodes[0].box.y+nodes[0].box.h+.015
            for node in nodes:
                if node.role=='body' and node.box.x<nodes[0].box.x+nodes[0].box.w and node.box.x+node.box.w>nodes[0].box.x and node.box.y<title_bottom:
                    delta=title_bottom-node.box.y;node.box.y=title_bottom;node.box.h=max(.03,node.box.h-delta)
            for node in nodes:
                if node.kind=='text':fit_node(node,design)
            slides.append(SceneSlide(id=ps.id,title=ps.title,role=ps.role,prototype_id=p.id,background=p.background,nodes=nodes))
        scenes.append(SceneIR(id=f'{job_id}-{variant}',variant=variant,template_id=design.id,content_id=content.id,width=design.width,height=design.height,slides=slides))
    return scenes
