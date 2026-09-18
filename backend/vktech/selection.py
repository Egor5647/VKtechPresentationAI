from __future__ import annotations
import re
from pathlib import Path
from PIL import Image,ImageFilter,ImageStat
from .contracts import SceneIR
from .audit import union_area


def _pixels(image):
    return image.get_flattened_data() if hasattr(image,'get_flattened_data') else image.getdata()


def score_candidate(scene,slide,preview:Path|None=None):
    """Score a rendered slide with transparent, deterministic quality signals."""
    boxes=[n.box for n in slide.nodes]
    fill=union_area(boxes)
    visual=any(n.kind in {'image','diagram','chart','table'} for n in slide.nodes)
    targets={'A':.46,'B':.55,'C':.58} if visual else {'A':.34,'B':.46,'C':.58}
    target=targets.get(scene.variant,.48 if visual else .40)
    score=100-abs(fill-target)*95
    reasons={'fill':round(fill,3),'target_fill':target}
    overlaps=0
    for i,a in enumerate(slide.nodes):
        for b in slide.nodes[i+1:]:
            dx=min(a.box.x+a.box.w,b.box.x+b.box.w)-max(a.box.x,b.box.x)
            dy=min(a.box.y+a.box.h,b.box.y+b.box.h)-max(a.box.y,b.box.y)
            if dx>.002 and dy>.002:overlaps+=1
    score-=overlaps*18;reasons['overlaps']=overlaps
    texts=[n for n in slide.nodes if n.kind=='text' and n.text.strip()]
    if texts:
        sizes=[n.style.size for n in texts if n.role!='accent'];spread=max(sizes)-min(sizes) if sizes else 0
        score-=max(0,spread-24)*.5;reasons['font_spread']=spread
    supports=sum(n.role in {'support','takeaway'} for n in slide.nodes)
    score+=min(6,supports*3);reasons['support_blocks']=supports
    visuals=[n for n in slide.nodes if n.kind in {'image','diagram','chart','table'}]
    if visuals:
        area=sum(n.box.w*n.box.h for n in visuals);score+=8 if .18<=area<=.42 else -8
        reasons['visual_area']=round(area,3)
    if slide.background.upper() not in {'FFFFFF','FEFEFE'}:score+=4;reasons['branded_background']=True
    image=next((n for n in slide.nodes if n.kind=='image'),None)
    if image:
        title_words=set(re.findall(r'[\w+#<>]{4,}',slide.title.lower()))
        desc_words=set(re.findall(r'[\w+#<>]{4,}',image.data.get('description','').lower()))
        relevance=len(title_words&desc_words)/max(1,len(title_words));score+=min(8,relevance*12);reasons['image_relevance']=round(relevance,3)
    if preview and preview.exists():
        with Image.open(preview) as source:
            im=source.convert('L');im.thumbnail((320,180));variation=ImageStat.Stat(im).stddev[0]
            edges=im.filter(ImageFilter.FIND_EDGES).crop((3,3,max(4,im.width-3),max(4,im.height-3)))
            edge_coverage=sum(value>=24 for value in _pixels(edges))/max(1,edges.width*edges.height)
        if variation<8:score-=10
        if slide.role=='content' and edge_coverage<.035:score-=12
        reasons['render_variation']=round(variation,2);reasons['render_coverage']=round(edge_coverage,3)
    return round(max(0,min(100,score)),2),reasons


def layout_signature(slide):
    """Describe visible geometry without depending on generated text."""
    nodes=[]
    for node in slide.nodes:
        if node.role=='accent':continue
        box=tuple(round(value*12) for value in (node.box.x,node.box.y,node.box.w,node.box.h))
        nodes.append((node.kind,node.role,box))
    background='dark' if slide.background.upper() not in {'FFFFFF','FEFEFE','F4F7F4','F7F9FB'} else 'light'
    return slide.role,background,tuple(nodes)


def preview_similarity(left:Path|None,right:Path|None) -> float:
    """Compare the edge structure of two renders; colour changes do not hide repeats."""
    if not left or not right or not left.exists() or not right.exists():return 0
    values=[]
    for path in (left,right):
        with Image.open(path) as source:
            image=source.convert('L').resize((64,36),Image.Resampling.LANCZOS).filter(ImageFilter.FIND_EDGES)
            values.append(list(_pixels(image)))
    distance=sum(abs(a-b) for a,b in zip(*values))/(255*len(values[0]))
    return max(0,1-distance)


def choose_variants(scores:dict[str,dict[str,float]],slide_ids:list[str],scenes:list[SceneIR]|None=None,previews:dict[str,list[Path]]|None=None):
    """Choose the best whole-deck sequence with explicit diversity penalties."""
    variants=tuple(sorted(next(iter(scores.values())))) if scores else ()
    scene_map={scene.variant:scene for scene in scenes or []}
    # Dynamic programming keeps the previous two choices. That is enough to
    # penalize adjacent visual repeats and three identical density modes.
    states={(None,None):(0.0,[])}
    for index,slide_id in enumerate(slide_ids):
        next_states={}
        for (previous2,previous),(total,history) in states.items():
            for variant in variants:
                adjusted=scores[slide_id][variant]
                if previous==variant:adjusted-=3
                if previous2==previous==variant:adjusted-=15
                if previous and scene_map:
                    current_slide=scene_map[variant].slides[index]
                    previous_slide=scene_map[previous].slides[index-1]
                    current_sig=layout_signature(current_slide);previous_sig=layout_signature(previous_slide)
                    if current_sig==previous_sig:adjusted-=16
                    elif current_sig[1:]==previous_sig[1:]:adjusted-=9
                    elif current_slide.prototype_id==previous_slide.prototype_id:adjusted-=4
                    if previews:
                        similarity=preview_similarity(Path(previews[previous][index-1]),Path(previews[variant][index]))
                        if similarity>=.985:adjusted-=18
                        elif similarity>=.965:adjusted-=9
                key=(previous,variant);candidate=(total+adjusted,history+[variant])
                if key not in next_states or candidate[0]>next_states[key][0]:next_states[key]=candidate
        states=next_states
    history=max(states.values(),key=lambda item:item[0])[1] if states else []
    return dict(zip(slide_ids,history))


def compose_scene(scenes:list[SceneIR],selection:dict[str,str],scene_id:str):
    by_variant={scene.variant:scene for scene in scenes};base=scenes[0]
    slides=[]
    for index,slide in enumerate(base.slides):
        variant=selection.get(slide.id,base.variant)
        slides.append(by_variant[variant].slides[index].model_copy(deep=True))
    return SceneIR(id=scene_id,variant='selected',template_id=base.template_id,content_id=base.content_id,width=base.width,height=base.height,slides=slides)
