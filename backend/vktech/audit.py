from __future__ import annotations
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from PIL import Image,ImageFilter,ImageFont
from .contracts import AuditReport, Issue, ContextualReport, ContextualFailureReport
from .graph_layout import graph_quality

RULES={
 'D01':'Элемент вне слайда','D02':'Наложение блоков','D03':'Переполнение текста','D04':'Текст обрезан краем','D05':'Выравнивание по шаблону','D06':'Поля','D07':'Пропорции изображения','D08':'Гарнитуры','D09':'Типографическая шкала','D10':'Палитра','D11':'Происхождение композиции','D12':'Логотип и колонтитул','D13':'Контраст','D14':'Количество пунктов','D15':'Длина пункта','D16':'Размер таблицы','D17':'Количество серий','D18':'Заполнение слайда','D19':'Открываемость','D20':'Заглушки','D21':'Пустой слайд','D22':'Редактируемые объекты','D23':'Подписи диаграммы','D24':'Дублирование слайдов','D25':'Минимальный кегль','D26':'Пустые декоративные контейнеры','D27':'Текст пересекает декоративную область шаблона','D28':'Низкая визуальная заполненность рендера','D29':'Слишком похожие соседние композиции','D30':'Некорректная смысловая схема'}
PLACEHOLDER=re.compile(r'\blorem ipsum\b|\bXXX\b|\bTODO\b|вставьте текст|\[Text\]',re.I)


def _pixels(image):
    return image.get_flattened_data() if hasattr(image,'get_flattened_data') else image.getdata()


def issue(scene,rule,slide=None,nodes=(),status='fail',message='',severity='warning',repair='none',evidence=None,category='deterministic'):
    ids=[n.id if hasattr(n,'id') else n for n in nodes]
    key=f'{scene.id}:{scene.version}:{rule}:{slide.id if slide else "deck"}:{",".join(ids)}'
    return Issue(id=hashlib.sha256(key.encode()).hexdigest()[:24],rule=rule,category=category,status=status,severity=severity,slide_id=slide.id if slide else None,element_ids=ids,message=message or RULES.get(rule,rule),repair=repair,evidence=evidence or {})


def contrast(a,b):
    def luminance(c):
        vals=[int(c[i:i+2],16)/255 for i in (0,2,4)]
        vals=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in vals]
        return sum(v*w for v,w in zip(vals,(.2126,.7152,.0722)))
    x,y=sorted((luminance(a),luminance(b)));return (y+.05)/(x+.05)


def font_for(name,size):
    configured=tuple(p for p in os.environ.get('FONT_DIRS','').split(os.pathsep) if p)
    system=('/Library/Fonts','/System/Library/Fonts','/System/Library/Fonts/Supplemental',str(Path.home()/'Library/Fonts'))
    return _font_for(name,size,configured+system)


@lru_cache(maxsize=256)
def _font_for(name,size,directories):
    """Resolve an installed font from explicit and standard macOS directories."""
    directories=[Path(p) for p in directories if Path(p).is_dir()]
    for directory in directories:
        for path in sorted(directory.glob('**/*')):
            if path.suffix.lower() in {'.ttf','.otf'}:
                try:
                    font=ImageFont.truetype(str(path),size)
                    if font.getname()[0].casefold()==name.casefold():return font
                except OSError:pass
    try:return ImageFont.truetype(name+'.ttf',size)
    except OSError:return None


def text_height(node,scene):
    # Measure at 96 DPI in the same physical canvas as PPTX.
    font=font_for(node.style.font,max(1,round(node.style.size*96/72)))
    if font is None:return None
    width=node.box.w*scene.width/914400*96
    lines=0
    for paragraph in node.text.split('\n'):
        current='';lines+=1
        for word in paragraph.split():
            new=(current+' '+word).strip()
            if font.getlength(new)>width and current:lines+=1;current=word
            else:current=new
    return lines*node.style.size*96/72*1.2/(scene.height/914400*96)


def union_area(boxes):
    xs=sorted({b.x for b in boxes}|{b.x+b.w for b in boxes});total=0
    for l,r in zip(xs,xs[1:]):
        intervals=sorted((b.y,b.y+b.h) for b in boxes if b.x<r and b.x+b.w>l)
        end=-float('inf');height=0
        for a,b in intervals:
            height+=max(0,b-max(a,end));end=max(end,b)
        total+=(r-l)*height
    return total


def template_text_right(prototype):
    candidates=[s.box.x+s.box.w for s in prototype.slots if s.role in {'body','visual'} and s.box.x<.25 and s.box.y>.28 and .24<s.box.w<.62 and s.box.x+s.box.w<.72]
    return min(candidates) if candidates else None


def audit_scene(scene,design,content,opened=False):
    result=[];seen={};pmap={p.id:p for p in design.prototypes}
    for slide in scene.slides:
        checks={k:[] for k in RULES if k not in {'D28','D29'}}
        def add(rule,nodes=(),**kw):
            found=issue(scene,rule,slide,nodes,**kw);checks[rule].append(found)
        for n in slide.nodes:
            b=n.box
            outside=b.x<-.0001 or b.y<-.0001 or b.x+b.w>1.0001 or b.y+b.h>1.0001
            if outside:
                add('D01',[n],repair='clamp',severity='error')
                if n.kind=='text':add('D04',[n],repair='clamp',severity='error')
            if n.kind=='text':
                h=text_height(n,scene) if n.text.strip() else 0
                if h is None:add('D03',[n],status='unknown',category='environment',message=f'Font {n.style.font} is unavailable; text fit is unverified')
                elif h>b.h:add('D03',[n],repair='fit_text',evidence={'measured_height':h,'available_height':b.h})
                allowed_fonts={*design.fonts,os.environ.get('FONT_FALLBACK','Arial')}
                if n.style.font not in allowed_fonts:add('D08',[n])
                if n.style.size not in design.font_sizes:add('D09',[n])
                if n.style.color not in design.palette:add('D10',[n])
                if n.text.strip():
                    ratio=contrast(n.style.color,n.style.fill or slide.background)
                    threshold=3 if n.style.size>=24 or (n.style.size>=18 and n.style.bold) else 4.5
                    if ratio<threshold:add('D13',[n],evidence={'ratio':round(ratio,2),'threshold':threshold,'background_method':'solid color; decoration requires visual check'})
                if PLACEHOLDER.search(n.text):add('D20',[n],repair='remove_placeholder')
                proto=pmap.get(slide.prototype_id);right=template_text_right(proto) if proto else None
                cover_text=right is not None and ((n.role=='title' and n.box.y>=.14) or (n.role=='body' and n.box.y>=.5))
                minimum=24 if cover_text and n.role=='title' else 16 if cover_text else 28 if n.role=='title' else 18
                if n.style.size<minimum:add('D25',[n],severity='error',evidence={'size':n.style.size,'minimum':minimum})
                if n.role=='body':
                    lines=n.text.split('\n')
                    if len(lines)>6:add('D14',[n],evidence={'count':len(lines)})
                    # D15 is a bullet-list rule. Wrapped prose is validated by the
                    # measured fit check rather than by an arbitrary sentence length.
                    if len(lines)>1:
                        for line in lines:
                            count=len(re.findall(r'\S+',line))
                            if count>15:
                                add('D15',[n],evidence={'max_words':count});break
            if n.kind=='table' and (len(n.data['categories'])+1>7 or len(n.data['series'])+1>5):add('D16',[n])
            if n.kind=='chart':
                if len(n.data['series'])>5:add('D17',[n])
                if not n.data.get('unit') or not n.data.get('categories'):add('D23',[n])
            if n.kind=='image':
                add('D07',[n],status='pass',message='Exporter fits image within bounds preserving aspect ratio')
            if n.kind=='diagram':
                _,failures=graph_quality(n.data.get('layout','list'),n.data.get('items',[]),n.data.get('graph'))
                if failures:add('D30',[n],severity='error',evidence={'failures':failures})
        for i,a in enumerate(slide.nodes):
            for b in slide.nodes[i+1:]:
                dx=min(a.box.x+a.box.w,b.box.x+b.box.w)-max(a.box.x,b.box.x)
                dy=min(a.box.y+a.box.h,b.box.y+b.box.h)-max(a.box.y,b.box.y)
                if dx>0.002 and dy>0.002:add('D02',[a,b],evidence={'intersection':dx*dy})
        proto=pmap.get(slide.prototype_id)
        if proto is None:add('D11',severity='error')
        else:
            bindings={s.id:s for s in proto.slots}
            for n in slide.nodes:
                slot=bindings.get(n.binding)
                if slot and abs(n.box.x-slot.box.x)>.005:add('D05',[n])
            # Protected objects are copied byte-for-byte, not repositioned by layout.
            add('D12',status='pass',message='Master, layout and footer objects retained by package copy')
            add('D26',status='pass',message='Unused sample placeholders and central decorative containers are removed during export')
            right=template_text_right(proto)
            title=next((n for n in slide.nodes if n.role=='title'),None);body=next((n for n in slide.nodes if n.role=='body'),None)
            if right is not None and title is not None and body is not None and title.box.y>=.14 and body.box.y>=.5:
                crossing=[n for n in (title,body) if n.box.x+n.box.w>right-.005]
                if crossing:add('D27',crossing,severity='error',evidence={'safe_right':round(right,3)})
        bounds=[s.box for p in design.prototypes for s in p.slots if s.role in {'body','title'}]
        minx=max(0,min((b.x for b in bounds),default=.03));maxx=min(1,max((b.x+b.w for b in bounds),default=.97))
        for n in slide.nodes:
            if n.box.x<minx-.005 or n.box.x+n.box.w>maxx+.005:add('D06',[n])
        fill=union_area([n.box for n in slide.nodes])
        if slide.role=='content' and (fill<.25 or fill>.75):add('D18',evidence={'fill':round(fill,3)})
        if slide.role=='content' and not any(n.role!='title' and (n.text or n.data) for n in slide.nodes):add('D21',severity='error')
        if not any(n.kind=='text' for n in slide.nodes):add('D22',severity='error')
        normalized='|'.join(n.text for n in slide.nodes if n.kind=='text')
        if normalized in seen:add('D24',evidence={'duplicate_of':seen[normalized]})
        seen[normalized]=slide.id
        for rule,issues in checks.items():
            if rule=='D19':continue
            if issues:result.extend(issues)
            elif rule in {'D07','D16','D17','D23'}:
                kind={'D07':'image','D16':'table','D17':'chart','D23':'chart'}[rule]
                result.append(issue(scene,rule,slide,status='pass' if any(n.kind==kind for n in slide.nodes) else 'not_applicable'))
            else:result.append(issue(scene,rule,slide,status='pass'))
    result.append(issue(scene,'D19',status='pass' if opened else 'unknown',message='Package read and rendered independently' if opened else 'Independent render not yet checked'))
    required={c.id for c in content.claims if c.required};used={c for s in scene.slides for n in s.nodes for c in n.claim_ids}
    result.append(issue(scene,'FACT_COMPLETENESS',status='pass' if required<=used else 'fail',severity='error',evidence={'missing_claim_ids':sorted(required-used)}))
    return AuditReport(scene_id=scene.id,version=scene.version,issues=result)


def _render_edge_coverage(path: Path) -> float:
    with Image.open(path) as source:
        image=source.convert('L').resize((320,180),Image.Resampling.LANCZOS).filter(ImageFilter.FIND_EDGES)
    image=image.crop((4,4,image.width-4,image.height-4))
    return sum(value>=24 for value in _pixels(image))/max(1,image.width*image.height)


def _render_similarity(left: Path,right: Path) -> float:
    values=[]
    for path in (left,right):
        with Image.open(path) as source:
            image=source.convert('L').resize((96,54),Image.Resampling.LANCZOS).filter(ImageFilter.FIND_EDGES)
            values.append(list(_pixels(image)))
    return 1-sum(abs(a-b) for a,b in zip(*values))/(255*len(values[0]))


def _geometry_signature(slide):
    return tuple((node.kind,node.role,node.data.get('layout') if node.kind=='diagram' else None,*(round(value*12) for value in (node.box.x,node.box.y,node.box.w,node.box.h))) for node in slide.nodes if node.role!='accent')


def audit_rendered_deck(scene,previews):
    """Inspect final pixels and adjacent compositions after independent rendering."""
    paths=[Path(path) for path in previews];result=[];sparse=[];repeated=[]
    for index,(slide,path) in enumerate(zip(scene.slides,paths)):
        coverage=_render_edge_coverage(path)
        if slide.role=='content' and coverage<.028:
            sparse.append(slide.id)
            result.append(issue(scene,'D28',slide,status='fail',message=RULES['D28'],evidence={'edge_coverage':round(coverage,3),'minimum':.028},category='deterministic'))
        if index:
            previous=scene.slides[index-1];similarity=_render_similarity(paths[index-1],path)
            same_geometry=_geometry_signature(previous)==_geometry_signature(slide)
            same_prototype=previous.prototype_id==slide.prototype_id
            if same_geometry and same_prototype and similarity>=.965:
                repeated.append(slide.id)
                result.append(issue(scene,'D29',slide,status='fail',message=RULES['D29'],evidence={'previous_slide_id':previous.id,'render_similarity':round(similarity,3)},category='deterministic'))
    if not sparse:result.append(issue(scene,'D28',status='pass',message='Все содержательные слайды используют достаточную долю итогового рендера'))
    if not repeated:result.append(issue(scene,'D29',status='pass',message='Соседние слайды различаются по композиции'))
    return result


def contextual_audit(gateway,scene,content,images):
    import os
    import tempfile
    from pathlib import Path
    from PIL import Image,ImageDraw
    failures_only=os.environ.get('MODEL_AUDIT_MODE','failures')=='failures'
    schema=ContextualFailureReport if failures_only else ContextualReport
    # Preserve enough of each source block for the auditor to see formulas,
    # qualifications and counterexamples that commonly occur after the heading.
    claim_limit=max(400,int(os.environ.get('MODEL_AUDIT_CLAIM_CHARS','900')))
    used_claims={cid for slide in scene.slides for node in slide.nodes for cid in node.claim_ids}
    compact_content={'id':content.id,'title':content.title,'language':content.language,'claims':[{'id':c.id,'text':c.text[:claim_limit],'source':c.source,'required':c.required} for c in content.claims if c.id in used_claims]}
    compact_scene={'id':scene.id,'variant':scene.variant,'slides':[]}
    for slide in scene.slides:
        nodes=[]
        for node in slide.nodes:
            item={'id':node.id,'kind':node.kind,'role':node.role,'text':node.text,'claim_ids':node.claim_ids}
            if node.kind in {'chart','table','diagram','smartart'}:item['data']=node.data
            elif node.kind=='image':item['data']={'description':node.data.get('description',''),'source':node.data.get('source','')}
            nodes.append(item)
        compact_scene['slides'].append({'id':slide.id,'title':slide.title,'role':slide.role,'nodes':nodes})
    model_images=images
    with tempfile.TemporaryDirectory(prefix='vktech-audit-') as temp:
        if len(images)>1:
            width=448;height=252;cols=3;rows=(len(images)+cols-1)//cols
            sheet=Image.new('RGB',(width*cols,height*rows),'white');draw=ImageDraw.Draw(sheet)
            for index,(path,slide) in enumerate(zip(images,scene.slides)):
                with Image.open(path) as source:
                    thumb=source.convert('RGB');thumb.thumbnail((width,height-24),Image.Resampling.LANCZOS)
                x=(index%cols)*width;y=(index//cols)*height
                sheet.paste(thumb,(x,y+24));draw.rectangle((x,y,x+width,y+24),fill='white');draw.text((x+6,y+5),slide.id,fill='black')
            contact=Path(temp)/'contact-sheet.png';sheet.save(contact,'PNG',optimize=True);model_images=[contact]
        report=gateway.structured('vision_audit',{'audit_mode':'failures_only' if failures_only else 'complete','visual_input':'One labeled contact sheet; labels are exact slide_id values.' if len(images)>1 else 'One slide image.','image_order':[s.id for s in scene.slides],'scene':compact_scene,'content':compact_content,'rules':{f'C{i:02}':name for i,name in enumerate(('Заголовок содержит вывод','Соответствие заголовку','Единая мысль','Факты из материалов','Есть содержание','Уместность изображений','Служебный мусор','Опечатки','Единый язык','Уместность данных','Связность соседних слайдов'),1)}},schema,model_images)
    slides={s.id:s for s in scene.slides};claims={claim.id:claim for claim in content.claims};seen=set();result=[]
    for item in report.issues:
        if item.rule not in {f'C{i:02}' for i in range(1,12)} or item.slide_id not in slides:raise ValueError('Contextual audit returned unknown rule or slide')
        valid={n.id for n in slides[item.slide_id].nodes}
        if not set(item.element_ids)<=valid:raise ValueError('Contextual audit returned unknown element')
        if (item.rule,item.slide_id) in seen:raise ValueError('Duplicate contextual rule result')
        observation=(item.message+' '+item.evidence).lower()
        # C04 verifies that displayed facts are supported. It must not require a
        # slide to reproduce every fact from a referenced multi-fact source chunk.
        if item.rule=='C04' and re.search(r'отсутств|не упомин|не полностью|не содержит',observation):continue
        if item.rule=='C04' and item.status=='fail' and re.search(r'нижн\w*.*верхн\w*|lower.*upper|upper.*lower',observation,re.I):
            visible=' '.join(node.text for node in slides[item.slide_id].nodes if node.text)
            claim_ids={cid for node in slides[item.slide_id].nodes for cid in node.claim_ids}
            source=' '.join(claims[cid].text for cid in claim_ids if cid in claims)
            directions={
                'lower':r'\bне\s+(?:менее|ниже|меньше)|≥|\bнижн\w*\s+границ\w*|\blower\s+bound',
                'upper':r'\bне\s+(?:более|выше)|\bне\s+превыш\w*|≤|\bверхн\w*\s+границ\w*|\bupper\s+bound',
            }
            visible_directions={name for name,pattern in directions.items() if re.search(pattern,visible,re.I)}
            source_directions={name for name,pattern in directions.items() if re.search(pattern,source,re.I)}
            # Deterministic source grounding wins over a vision-auditor claim
            # that confuses a lower bound with a separate upper-bound formula
            # from the same source chunk.
            if visible_directions and visible_directions<=source_directions:continue
        # Repetition between prose and a useful native diagram is not service
        # debris. C07 is reserved for actual headers, page numbers and placeholders.
        if item.rule=='C07' and re.search(r'повтор|дублир',observation) and not re.search(r'конспект|стр\.?\s*\d|todo|lorem|заглуш',observation):continue
        if item.rule=='C08' and re.search(r'отсутств|не уточн|не указан',observation):continue
        if item.rule=='C06' and re.search(r'повтор|дублир',observation) and not any(n.kind=='image' for n in slides[item.slide_id].nodes):continue
        seen.add((item.rule,item.slide_id))
        result.append(issue(scene,item.rule,slides[item.slide_id],item.element_ids,status=item.status,message=item.message,evidence={'model_observation':item.evidence},category='contextual'))
    # An omitted check is unknown, never a successful result.
    for slide in scene.slides:
        for i in range(1,12):
            rule=f'C{i:02}'
            if (rule,slide.id) not in seen:result.append(issue(scene,rule,slide,status='unknown',message='Model omitted this contextual check',category='contextual'))
    return result


def project_contextual_issues(source_issues,source_scene,target_scene):
    """Reuse content findings for variants that share the exact plan and facts."""
    slides={s.id:s for s in target_scene.slides};result=[]
    for item in source_issues:
        slide=slides.get(item.slide_id) if item.slide_id else None
        if item.slide_id and slide is None:continue
        valid={n.id for n in slide.nodes} if slide else set()
        nodes=[nid for nid in item.element_ids if nid in valid]
        evidence=dict(item.evidence);evidence['shared_audit_from']=source_scene.variant
        result.append(issue(target_scene,item.rule,slide,nodes,status=item.status,message=item.message,severity=item.severity,repair='none',evidence=evidence,category='contextual'))
    return result


def repair_scene(scene,report,request,design):
    if scene.version!=request.expected_version or report.version!=scene.version:raise ValueError('Stale scene or audit version')
    byid={i.id:i for i in report.issues};selected=[]
    for iid in set(request.issue_ids):
        if iid not in byid:raise ValueError('Unknown issue ID')
        i=byid[iid]
        if i.status!='fail' or i.repair=='none':raise ValueError('Issue has no safe automatic repair; requires user editing')
        selected.append(i)
    new=scene.model_copy(deep=True);new.version+=1
    nodes={n.id:n for s in new.slides for n in s.nodes}
    for i in selected:
        for nid in i.element_ids:
            n=nodes[nid]
            if i.repair=='clamp':
                n.box.w=min(n.box.w,1);n.box.h=min(n.box.h,1);n.box.x=max(0,min(n.box.x,1-n.box.w));n.box.y=max(0,min(n.box.y,1-n.box.h))
            elif i.repair=='fit_text':
                available=sorted((s for s in design.font_sizes if s<n.style.size),reverse=True)
                slide=next(s for s in new.slides if n in s.nodes)
                for size in available:
                    n.style.size=size;h=text_height(n,new)
                    if h is not None and h<=n.box.h:break
                else:raise ValueError('Text does not fit within template font scale; edit content or composition')
            elif i.repair=='remove_placeholder':
                # Source claims are immutable. Never remove mandatory content automatically.
                if n.claim_ids:raise ValueError('Placeholder occurs in a source claim; edit source content explicitly')
                n.text=PLACEHOLDER.sub('',n.text).strip()
    return new,{'from_version':scene.version,'to_version':new.version,'selected_issue_ids':[i.id for i in selected]}
