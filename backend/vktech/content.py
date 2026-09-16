from __future__ import annotations
import csv
import hashlib
import io
import re
from collections.abc import Callable
from PIL import Image
from .contracts import ContentIR, Claim, Dataset, Asset
from .opc import Package, NS


AssetWriter = Callable[[bytes, str, str], str]


def _clean_text(value: str) -> str:
    return re.sub(r'[ \t\r\f\v]+', ' ', value).strip()


def _paragraphs(root) -> list[str]:
    result=[]
    for paragraph in root.findall('.//a:p',NS):
        text=_clean_text(''.join(paragraph.xpath('.//a:t/text()',namespaces=NS)))
        if text and text not in result:result.append(text)
    return result


def _notes(pkg: Package, slide_part: str) -> list[str]:
    part=pkg.related(slide_part,'notesSlide')
    if not part:return []
    root=pkg.root(part);result=[]
    for shape in root.findall('.//p:sp',NS):
        placeholder=shape.find('p:nvSpPr/p:nvPr/p:ph',NS)
        if placeholder is not None and placeholder.get('type') not in {None,'body'}:continue
        for text in _paragraphs(shape):
            if text and text not in result:result.append(text)
    return result


def _required_slide(text: str, index: int) -> bool:
    """Keep factual slides mandatory while allowing prompts and dividers to be condensed."""
    if index==1:return True
    compact=' '.join(text.split())
    if not compact:return False
    if '?' in compact:return False
    if re.search(r'^(разбер[её]м|выбери|попрактикуемся|сегодня мы|итоги)\b',compact,re.I):return False
    if re.search(r'наш план на сегодня|попрактикуемся',compact,re.I):return False
    if len(compact)<80 and re.search(r'^(повторение|практика|теория|что дальше)\b',compact,re.I):return False
    return True


def _similarity(left: str, right: str) -> float:
    words=lambda value:set(re.findall(r'[\w+#<>]+',value.lower()))
    a,b=words(left),words(right)
    return len(a&b)/max(1,len(a|b))


def _slide_claims(pkg: Package, filename: str, slide_parts: list[str]):
    records=[]
    for index,part in enumerate(slide_parts,1):
        paragraphs=_paragraphs(pkg.root(part));text='\n'.join(paragraphs)
        records.append({'index':index,'part':part,'paragraphs':paragraphs,'text':text,'required':_required_slide(text,index)})
    groups=[]
    for record in records:
        previous=groups[-1][-1] if groups else None
        if previous and previous['required'] and record['required'] and _similarity(previous['text'],record['text'])>=.33:
            groups[-1].append(record)
        else:groups.append([record])
    claims=[];slide_claim_ids={}
    for group in groups:
        start,end=group[0]['index'],group[-1]['index']
        claim_id=f'claim-slide-{start}' if start==end else f'claim-slides-{start}-{end}'
        lines=[]
        for record in group:
            slide_claim_ids[record['index']]=claim_id
            for line in record['paragraphs']:
                if line not in lines:lines.append(line)
        text='\n'.join(lines) or f'Визуальный пример без извлекаемого текста на слайде {start}'
        source=f'{filename}#slide={start}' if start==end else f'{filename}#slides={start}-{end}'
        claims.append(Claim(id=claim_id,text=text,source=source,required=any(r['required'] for r in group)))
        for record in group:
            notes=_notes(pkg,record['part'])
            if notes:claims.append(Claim(id=f"claim-slide-{record['index']}-notes",text='\n'.join(notes),source=f"{filename}#slide={record['index']};notes",required=False))
    return claims,slide_claim_ids


def _pptx_assets(pkg: Package, filename: str, slide_parts: list[str], slide_claim_ids: dict[int,str], writer: AssetWriter | None) -> list[Asset]:
    if writer is None:return []
    by_digest:dict[str,Asset]={}
    for index,part in enumerate(slide_parts,1):
        claim_id=slide_claim_ids[index]
        title=next(iter(_paragraphs(pkg.root(part))),f'Слайд {index}')
        for blip in pkg.root(part).findall('.//a:blip',NS):
            rid=blip.get('{'+NS['r']+'}embed')
            target=pkg.target(part,rid) if rid else None
            if not target or target not in pkg.parts:continue
            raw=pkg.parts[target];digest=hashlib.sha256(raw).hexdigest()
            if digest in by_digest:
                asset=by_digest[digest]
                if claim_id not in asset.claim_ids:asset.claim_ids.append(claim_id)
                continue
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    image.verify();fmt=image.format
                with Image.open(io.BytesIO(raw)) as image:
                    width,height=image.size
            except Exception:continue
            media={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(fmt)
            if not media:continue
            extension={'PNG':'png','JPEG':'jpg','WEBP':'webp'}[fmt]
            asset_id='asset-'+digest[:20]
            path=writer(raw,f'{asset_id}.{extension}',extension)
            by_digest[digest]=Asset(id=asset_id,path=path,description=f'Иллюстрация со слайда {index} «{title[:120]}», {width}×{height}',source=f'{filename}#slide={index};part={target}',media_type=media,claim_ids=[claim_id])
    return list(by_digest.values())


def import_content(data: bytes, filename: str, asset_writer: AssetWriter | None = None) -> ContentIR:
    digest=hashlib.sha256(data).hexdigest()[:24]
    ext=filename.rsplit('.',1)[-1].lower()
    if ext=='json':
        content=ContentIR.model_validate_json(data)
        content.id=digest
        return content
    if ext=='pptx':
        pkg=Package(data);slide_parts=pkg.slides();claims,slide_claim_ids=_slide_claims(pkg,filename,slide_parts)
        assets=_pptx_assets(pkg,filename,slide_parts,slide_claim_ids,asset_writer)
        return ContentIR(id=digest,title=filename,claims=claims,assets=assets)
    if ext=='pdf':
        from pypdf import PdfReader
        paragraphs=[]
        for i,page in enumerate(PdfReader(io.BytesIO(data)).pages):
            paragraphs.extend((line.strip(),f'{filename}#page={i+1}') for line in (page.extract_text() or '').splitlines() if line.strip())
    elif ext in {'txt','md'}:
        paragraphs=[(line.strip().lstrip('# ').strip(),f'{filename}#line={i+1}') for i,line in enumerate(data.decode('utf-8-sig').splitlines()) if line.strip()]
    elif ext=='csv':
        rows=list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        if len(rows)<2 or len(rows[0])<2: raise ValueError('CSV requires category and numeric series columns')
        series={h:[float(r[i]) for r in rows[1:]] for i,h in enumerate(rows[0]) if i>0}
        return ContentIR(id=digest,title=filename,claims=[],datasets=[Dataset(id='data-1',title=filename,categories=[r[0] for r in rows[1:]],series=series,unit='значение',source=filename)])
    else: raise ValueError('Supported content formats: JSON, PPTX, TXT, MD, PDF, CSV')
    return ContentIR(id=digest,title=filename,claims=[Claim(id=f'claim-{i+1}',text=t,source=s) for i,(t,s) in enumerate(paragraphs)])
