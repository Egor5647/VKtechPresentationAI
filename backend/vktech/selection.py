from __future__ import annotations
import re
from pathlib import Path
from PIL import Image,ImageStat
from .contracts import SceneIR
from .audit import union_area


def score_candidate(scene,slide,preview:Path|None=None):
    """Score a rendered slide with transparent, deterministic quality signals."""
    boxes=[n.box for n in slide.nodes]
    fill=union_area(boxes)
    target=.48 if any(n.kind in {'image','diagram','chart','table'} for n in slide.nodes) else .40
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
        if variation<8:score-=10
        reasons['render_variation']=round(variation,2)
    return round(max(0,min(100,score)),2),reasons


def choose_variants(scores:dict[str,dict[str,float]],slide_ids:list[str]):
    """Choose the strongest sequence while avoiding monotonous repetition."""
    selected={};history=[]
    for slide_id in slide_ids:
        ranked=[]
        for variant,value in scores[slide_id].items():
            adjusted=value
            if history and history[-1]==variant:adjusted-=3
            if len(history)>=2 and history[-2:]==[variant,variant]:adjusted-=15
            ranked.append((adjusted,value,variant))
        _,_,winner=max(ranked)
        selected[slide_id]=winner;history.append(winner)
    return selected


def compose_scene(scenes:list[SceneIR],selection:dict[str,str],scene_id:str):
    by_variant={scene.variant:scene for scene in scenes};base=scenes[0]
    slides=[]
    for index,slide in enumerate(base.slides):
        variant=selection.get(slide.id,base.variant)
        slides.append(by_variant[variant].slides[index].model_copy(deep=True))
    return SceneIR(id=scene_id,variant='selected',template_id=base.template_id,content_id=base.content_id,width=base.width,height=base.height,slides=slides)
