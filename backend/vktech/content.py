from __future__ import annotations
import csv
import hashlib
import io
from .contracts import ContentIR, Claim, Dataset
from .opc import Package, NS


def import_content(data: bytes, filename: str) -> ContentIR:
    digest=hashlib.sha256(data).hexdigest()[:24]
    ext=filename.rsplit('.',1)[-1].lower()
    if ext=='json':
        content=ContentIR.model_validate_json(data)
        content.id=digest
        return content
    if ext=='pptx':
        pkg=Package(data); claims=[]
        for i,part in enumerate(pkg.slides()):
            root=pkg.root(part)
            for j,p in enumerate(root.findall('.//a:p',NS)):
                text=''.join(p.xpath('.//a:t/text()',namespaces=NS)).strip()
                if text: claims.append(Claim(id=f'claim-{i+1}-{j+1}',text=text,source=f'{filename}#slide={i+1};paragraph={j+1}'))
        return ContentIR(id=digest,title=filename,claims=claims)
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
