from __future__ import annotations
import hashlib
import re
from .contracts import PresentationPlan, ContentIR, DesignIR, SceneIR, SceneSlide, Node, Style, Box


class NeedsInput(ValueError):
    pass


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
    plan=gateway.structured('planning',{'content':content.model_dump(),'brief':request.brief,'purpose':request.purpose,'slide_count':request.slide_count},PresentationPlan)
    validate_plan(plan,content,request.slide_count)
    return plan


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
    for size in sorted({node.style.size}|{s for s in design.font_sizes if 10<=s<node.style.size},reverse=True):
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
            texts=[claims[c].text for c in ps.claim_ids]
            # Source claims are emitted verbatim until a verified paraphrase contract exists.
            for i,slot in enumerate(bodies):
                subset=[(cid,claims[cid].text) for j,cid in enumerate(ps.claim_ids) if j%len(bodies)==i]
                if subset:
                    nodes.append(Node(id=f'{ps.id}-body-{i+1}',kind='text',role='body',box=slot.box.model_copy(),style=slot.style.model_copy(deep=True),text='\n'.join(t for _,t in subset),claim_ids=[c for c,_ in subset],binding=slot.id))
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
