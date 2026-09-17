from __future__ import annotations
import base64
import copy
import html
import io
import json
import os
import posixpath
import subprocess
import tempfile
from pathlib import Path
from lxml import etree as E
from pptx import Presentation
from pptx.util import Pt
from pptx.dml.color import RGBColor
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from .contracts import SceneIR, DesignIR
from .opc import Package, NS, serialize, relpath
from .smartart import inject
from .settings import artifact_path


def _card(out,node,x,y,w,h,text,name,fill,foreground,bold=False):
    sh=out.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,x,y,w,h)
    sh.name=name;sh.fill.solid();sh.fill.fore_color.rgb=RGBColor.from_string(fill)
    sh.line.color.rgb=RGBColor.from_string(node.data.get('accent','0077FF'));sh.line.width=Pt(1.4)
    tf=sh.text_frame;tf.clear();tf.word_wrap=True;tf.vertical_anchor=MSO_ANCHOR.MIDDLE
    tf.margin_left=tf.margin_right=Pt(9);tf.margin_top=tf.margin_bottom=Pt(6)
    p=tf.paragraphs[0];p.text=text;p.alignment=PP_ALIGN.CENTER
    p.font.name=node.style.font;p.font.size=Pt(node.style.size);p.font.bold=bold;p.font.color.rgb=RGBColor.from_string(foreground)
    return sh


def _diagram(out,node,xywh):
    from .audit import contrast
    x,y,w,h=xywh;items=node.data.get('items',[])[:4];layout=node.data.get('layout','list')
    if not items:return
    accent=node.data.get('accent','0077FF');surface=node.data.get('surface') or node.style.fill or 'E8EEF6'
    dark='202020' if contrast('202020',surface)>=4.5 else 'FFFFFF'
    white='FFFFFF' if contrast('FFFFFF',accent)>=3 else '202020'
    gap=max(round(w*.025),Pt(8));vgap=max(round(h*.07),Pt(8))
    def connector(x1,y1,x2,y2,index):
        line=out.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,x1,y1,x2,y2)
        line.name=f'{node.id}-connector-{index}';line.line.color.rgb=RGBColor.from_string(accent);line.line.width=Pt(2)
    if layout=='sequence':
        count=len(items)
        if count>=4 or max(map(len,items),default=0)>52:
            cols=2;rows=(count+1)//2;cw=(w-gap)//2;ch=(h-vgap*(rows-1))//rows
            positions=[]
            for i,item in enumerate(items):
                row=i//2;col=i%2 if row%2==0 else 1-i%2
                positions.append((x+col*(cw+gap),y+row*(ch+vgap)))
            for i,((cx,cy),(nx,ny)) in enumerate(zip(positions,positions[1:]),1):
                connector(cx+cw//2,cy+ch//2,nx+cw//2,ny+ch//2,i)
            for i,(item,(cx,cy)) in enumerate(zip(items,positions)):
                _card(out,node,cx,cy,cw,ch,item,f'{node.id}-card-{i+1}',accent if i==0 else surface,white if i==0 else dark,i==0)
        else:
            cw=(w-gap*(count-1))//count;ch=min(round(h*.62),h);cy=y+(h-ch)//2
            for i,item in enumerate(items):
                cx=x+i*(cw+gap)
                if i:connector(cx-gap,cy+ch//2,cx,cy+ch//2,i)
                _card(out,node,cx,cy,cw,ch,item,f'{node.id}-card-{i+1}',accent if i==0 else surface,white if i==0 else dark,i==0)
    elif layout=='hierarchy':
        root_h=round(h*.30);root_w=round(w*.48);root_x=x+(w-root_w)//2
        child_count=max(1,len(items)-1);child_y=y+round(h*.57);child_h=h-(child_y-y)
        child_w=(w-gap*(child_count-1))//child_count
        for i in range(child_count):
            cx=x+i*(child_w+gap)
            connector(root_x+root_w//2,y+root_h,cx+child_w//2,child_y,i+1)
        _card(out,node,root_x,y,root_w,root_h,items[0],f'{node.id}-card-1',accent,white,True)
        for i,item in enumerate(items[1:] or items[:1]):
            cx=x+i*(child_w+gap)
            _card(out,node,cx,child_y,child_w,child_h,item,f'{node.id}-card-{i+2}',surface,dark)
    else:
        cols=2 if len(items)>1 else 1;rows=(len(items)+cols-1)//cols
        cw=(w-gap*(cols-1))//cols;ch=(h-vgap*(rows-1))//rows
        for i,item in enumerate(items):
            col=i%cols;row=i//cols
            _card(out,node,x+col*(cw+gap),y+row*(ch+vgap),cw,ch,item,f'{node.id}-card-{i+1}',accent if i==0 else surface,white if i==0 else dark,i==0)


def temporary_objects(scene,slide):
    prs=Presentation();prs.slide_width=scene.width;prs.slide_height=scene.height
    out=prs.slides.add_slide(prs.slide_layouts[6])
    for node in slide.nodes:
        b=node.box;xywh=[round(v*s) for v,s in zip((b.x,b.y,b.w,b.h),(scene.width,scene.height,scene.width,scene.height))]
        if node.kind=='text':
            sh=out.shapes.add_textbox(*xywh);tf=sh.text_frame;tf.word_wrap=True
            if node.style.fill:
                sh.fill.solid();sh.fill.fore_color.rgb=RGBColor.from_string(node.style.fill)
                tf.margin_left=tf.margin_right=Pt(10);tf.margin_top=tf.margin_bottom=Pt(8)
            else:tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
            for i,line in enumerate(node.text.split('\n')):
                p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
                p.text=line;p.font.name=node.style.font;p.font.size=Pt(node.style.size);p.font.bold=node.style.bold;p.font.color.rgb=RGBColor.from_string(node.style.color)
                p.alignment={'center':PP_ALIGN.CENTER,'right':PP_ALIGN.RIGHT}.get(node.style.align,PP_ALIGN.LEFT)
        elif node.kind=='chart':
            data=CategoryChartData();data.categories=node.data['categories']
            for name,values in node.data['series'].items():data.add_series(name,values)
            typ=XL_CHART_TYPE.LINE_MARKERS if scene.variant=='B' else XL_CHART_TYPE.COLUMN_CLUSTERED
            sh=out.shapes.add_chart(typ,*xywh,data);chart=sh.chart
            chart.has_legend=len(node.data['series'])>1
            if chart.has_legend:chart.legend.position=XL_LEGEND_POSITION.BOTTOM
            chart.value_axis.has_title=True;chart.value_axis.axis_title.text_frame.text=node.data['unit']
            chart.category_axis.has_title=True;chart.category_axis.axis_title.text_frame.text=node.data['title']
            for axis in (chart.category_axis,chart.value_axis):
                axis.tick_labels.font.name=node.style.font;axis.tick_labels.font.size=Pt(10);axis.tick_labels.font.color.rgb=RGBColor.from_string(node.style.color)
                for para in axis.axis_title.text_frame.paragraphs:
                    para.font.name=node.style.font;para.font.size=Pt(12);para.font.color.rgb=RGBColor.from_string(node.style.color)
            if chart.has_legend:
                chart.legend.font.name=node.style.font;chart.legend.font.size=Pt(10);chart.legend.font.color.rgb=RGBColor.from_string(node.style.color)
            for i,series in enumerate(chart.series):
                if typ==XL_CHART_TYPE.COLUMN_CLUSTERED:
                    series.format.fill.solid();series.format.fill.fore_color.rgb=RGBColor.from_string(node.style.color)
        elif node.kind=='table':
            cols=1+len(node.data['series']);rows=1+len(node.data['categories'])
            sh=out.shapes.add_table(rows,cols,*xywh)
            headers=[node.data['title']]+[name+' ('+node.data['unit']+')' for name in node.data['series']]
            values=[headers]+[[cat]+[str(v[i]) for v in node.data['series'].values()] for i,cat in enumerate(node.data['categories'])]
            for i,row in enumerate(values):
                for j,value in enumerate(row):
                    cell=sh.table.cell(i,j);cell.text=value
                    for p in cell.text_frame.paragraphs:
                        p.font.name=node.style.font;p.font.size=Pt(max(8,min(node.style.size,14)));p.font.color.rgb=RGBColor.from_string(node.style.color)
        elif node.kind=='image':
            source=artifact_path(node.data['path'])
            from PIL import Image,ImageChops
            picture=str(source)
            with Image.open(source) as opened:
                im=opened.convert('RGB')
                if str(node.data.get('source','')).startswith('Z-Image'):
                    background=Image.new('RGB',im.size,im.getpixel((0,0)))
                    diff=ImageChops.difference(im,background).convert('L').point(lambda value:255 if value>18 else 0)
                    bbox=diff.getbbox()
                    if bbox:
                        pad=max(12,round(min(im.size)*.04));l,t,r,bt=bbox
                        im=im.crop((max(0,l-pad),max(0,t-pad),min(im.width,r+pad),min(im.height,bt+pad)))
                    stream=io.BytesIO();im.save(stream,format='PNG');stream.seek(0);picture=stream
                iw,ih=im.size
            factor=min(xywh[2]/iw,xywh[3]/ih)
            w,h=round(iw*factor),round(ih*factor)
            sh=out.shapes.add_picture(picture,xywh[0]+(xywh[2]-w)//2,xywh[1]+(xywh[3]-h)//2,w,h)
        elif node.kind=='diagram':
            _diagram(out,node,xywh)
            continue
        else:continue
        sh.name=node.id
    buf=io.BytesIO();prs.save(buf);return Package(buf.getvalue())


def strip_content(root,prototype):
    remove={s.shape_id for s in prototype.slots if s.role in {'title','body','visual'}}
    # Sample decks often contain phones, screenshots and empty demo frames built
    # from ordinary vector shapes. Keep only small edge decorations and footers;
    # the master/layout still carries the brand background, logo and page chrome.
    for slot in prototype.slots:
        area=slot.box.w*slot.box.h
        edge=slot.box.x<.035 or slot.box.y<.035 or slot.box.x+slot.box.w>.965 or slot.box.y+slot.box.h>.965
        if slot.role=='decor' and not (edge and area<=.02):remove.add(slot.shape_id)
    for shape in list(root.findall('.//p:sp',NS))+list(root.findall('.//p:pic',NS))+list(root.findall('.//p:graphicFrame',NS)):
        nv=shape.find('.//p:cNvPr',NS)
        if nv is not None and int(nv.get('id')) in remove:shape.getparent().remove(shape)


def export_pptx(template: bytes,design: DesignIR,scene: SceneIR,path: Path,decor_only=False):
    source=Package(template);pkg=Package(template);prototypes={p.id:p for p in design.prototypes};slideparts=[]
    for i,slide in enumerate(scene.slides):
        proto=prototypes[slide.prototype_id]
        dest=pkg.clone_graph(source,proto.slide_part,f'v{scene.version}_{scene.variant}_{i}')
        root=pkg.root(dest);strip_content(root,proto)
        tree=root.find('p:cSld/p:spTree',NS)
        # Speaker notes from a template may contain unrelated content.
        rr=pkg.root(relpath(dest))
        for r in list(rr):
            if r.get('Type','').endswith('/notesSlide'):rr.remove(r)
        pkg.parts[relpath(dest)]=serialize(rr)
        if not decor_only:
            objects=temporary_objects(scene,slide)
            temp_part=objects.slides()[0]
            objroot=objects.root(temp_part)
            rr=pkg.root(relpath(dest));nextid=max([int(n.get('id','0')) for n in tree.findall('.//p:cNvPr',NS)]+[1])+1
            for shape in objroot.find('p:cSld/p:spTree',NS):
                if shape.tag.split('}')[-1] not in {'sp','cxnSp','pic','graphicFrame'}:continue
                shape=copy.deepcopy(shape)
                for nv in shape.findall('.//p:cNvPr',NS):nv.set('id',str(nextid));nextid+=1
                for el in shape.iter():
                    for attr,value in list(el.attrib.items()):
                        if attr.startswith('{'+NS['r']+'}'):
                            target=objects.target(temp_part,value)
                            rel=next(r for r in objects.relations(temp_part) if r.get('Id')==value)
                            newrid=f'rIdObject{i}_{nextid}_{value}'
                            newrel=copy.deepcopy(rel);newrel.set('Id',newrid)
                            if target:
                                newtarget=pkg.clone_graph(objects,target,f'v{scene.version}_{scene.variant}_{i}_{nextid}')
                                newrel.set('Target',posixpath.relpath(newtarget,posixpath.dirname(dest)))
                            rr.append(newrel);el.set(attr,newrid)
                tree.append(shape)
            pkg.parts[relpath(dest)]=serialize(rr)
            for node in slide.nodes:
                if node.kind=='smartart':
                    tree.append(inject(pkg,dest,node,nextid,scene.width,scene.height));nextid+=1
        pkg.parts[dest]=serialize(root);slideparts.append(dest)
    pkg.set_slides(slideparts);pkg.prune();pkg.validate();path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(pkg.bytes())
    # Independent native object reader.
    assert len(Presentation(str(path)).slides)==len(scene.slides)


def render(pptx: Path,outdir: Path,timeout=90):
    outdir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vktech-lo-') as profile:
        command=[os.environ.get('SOFFICE','soffice'),'-env:UserInstallation='+Path(profile).as_uri(),'--headless','--convert-to','pdf','--outdir',str(outdir),str(pptx)]
        result=subprocess.run(command,capture_output=True,timeout=timeout)
        pdf=outdir/(pptx.stem+'.pdf')
        if result.returncode or not pdf.exists():raise RuntimeError('LibreOffice export failed: '+result.stderr.decode(errors='replace')[-400:])
    from pypdf import PdfReader
    pages=len(PdfReader(pdf).pages)
    result=subprocess.run([os.environ.get('PDFTOPPM','pdftoppm'),'-scale-to','1400','-png',str(pdf),str(outdir/'slide')],capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('PDF preview rendering failed')
    images=sorted(outdir.glob('slide-*.png'),key=lambda p:int(p.stem.rsplit('-',1)[-1]))
    if len(images)!=pages:raise RuntimeError('Preview page count mismatch')
    return pdf,images


def chart_svg(node,variant):
    data=node.data;cats=data['categories'];series=list(data['series'].items());values=[v for _,vs in series for v in vs]
    low=min(0,min(values));high=max(0,max(values));span=high-low or 1
    y=lambda v:230-(v-low)/span*190
    parts=[f'<svg viewBox="0 0 600 280" role="img" aria-label="{html.escape(data["title"],quote=True)}"><title>{html.escape(data["title"])} ({html.escape(data["unit"])})</title>',f'<line x1="50" y1="{y(0)}" x2="580" y2="{y(0)}" stroke="currentColor"/>']
    for si,(name,vs) in enumerate(series):
        points=[]
        for i,v in enumerate(vs):
            x=60+(i+.5)*510/len(cats)
            if variant=='B':points.append(f'{x},{y(v)}')
            else:
                bw=450/len(cats)/len(series);xx=x-225/len(cats)+si*bw
                parts.append(f'<rect x="{xx}" y="{min(y(v),y(0))}" width="{bw*.85}" height="{abs(y(v)-y(0))}" fill="#{node.style.color}" opacity="{1-si/max(1,len(series))*0.5}"><title>{html.escape(name)}: {v}</title></rect>')
            parts.append(f'<text x="{x}" y="{y(v)-5}" font-size="11" text-anchor="middle">{v}</text>')
        if variant=='B':parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#{node.style.color}" stroke-width="3"/>')
    for i,cat in enumerate(cats):parts.append(f'<text x="{60+(i+.5)*510/len(cats)}" y="255" font-size="11" text-anchor="middle">{html.escape(cat)}</text>')
    parts.append(f'<text x="50" y="20" font-size="12">{html.escape(data["unit"])}</text></svg>')
    return ''.join(parts)


def export_html(scene,path,backgrounds=None):
    slides=[]
    for i,slide in enumerate(scene.slides):
        bg=''
        if backgrounds:
            bg='<img class="decor" aria-hidden="true" alt="" src="data:image/png;base64,'+base64.b64encode(backgrounds[i].read_bytes()).decode()+'">'
        nodes=[]
        for n in slide.nodes:
            b=n.box;fs=n.style.size/72/(scene.width/914400)*100
            style=f'left:{b.x*100}%;top:{b.y*100}%;width:{b.w*100}%;height:{b.h*100}%;font-family:{html.escape(json.dumps(n.style.font),quote=True)},sans-serif;font-size:{fs}cqw;color:#{n.style.color};text-align:{n.style.align};font-weight:{700 if n.style.bold else 400};background:{"#"+n.style.fill if n.style.fill else "transparent"};padding:{"0.45em" if n.style.fill else "0"}'
            if n.kind=='text':body=html.escape(n.text).replace('\n','<br>')
            elif n.kind=='chart':body=chart_svg(n,scene.variant)
            elif n.kind=='table':
                data=n.data;headers=[data['title']]+[name+' ('+data['unit']+')' for name in data['series']]
                rows=[headers]+[[cat]+[str(v[j]) for v in data['series'].values()] for j,cat in enumerate(data['categories'])]
                body='<table>'+''.join('<tr>'+''.join(f'<{"th" if ri==0 else "td"}>{html.escape(cell)}</{"th" if ri==0 else "td"}>' for cell in row)+'</tr>' for ri,row in enumerate(rows))+'</table>'
            elif n.kind in {'diagram','smartart'}:body='<ol class="diagram '+n.data.get('layout','list')+'">'+''.join('<li>'+html.escape(t)+'</li>' for t in n.data.get('items',[]))+'</ol>'
            elif n.kind=='image':body='<img alt="'+html.escape(n.data['description'],quote=True)+'" src="data:'+n.data.get('media_type','image/png')+';base64,'+base64.b64encode(artifact_path(n.data['path']).read_bytes()).decode()+'">'
            else:body=''
            nodes.append(f'<div class="node {n.kind}" data-element-id="{html.escape(n.id,quote=True)}" style="{style}">{body}</div>')
        slides.append(f'<section class="slide" aria-label="{html.escape(slide.title,quote=True)}" style="background:#{slide.background}">{bg}{"".join(nodes)}</section>')
    doc='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Презентация</title><style>
body{margin:0;background:#d9dce3;font-family:Arial}main{max-width:1400px;margin:auto}.slide{position:relative;aspect-ratio:16/9;container-type:inline-size;margin:24px 0;overflow:hidden}.decor{position:absolute;width:100%;height:100%;inset:0}.node{position:absolute;box-sizing:border-box;line-height:1.2;overflow:visible}.node img,.node svg{width:100%;height:100%;object-fit:contain}.node table{border-collapse:collapse;width:100%;height:100%;font-size:.7em}.node td,.node th{padding:.2em;border:1px solid currentColor}.diagram{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;height:100%;gap:4%;font-size:.8em}.diagram li{background:#e8eef6;padding:.4em;border-radius:8px;flex:1}.diagram.sequence{flex-direction:row}.controls{position:fixed;right:20px;bottom:20px;z-index:20;background:#172438;color:white;border:0;border-radius:8px;padding:10px 14px}.controls button{color:white;background:transparent;border:0;font-size:18px}.controls span{padding:0 8px}@media print{body{background:white}.slide{margin:0;break-after:page}.controls{display:none}main{max-width:none}}@supports not (font-size:1cqw){.node{font-size:18px!important}}
</style><main>'''+''.join(slides)+'''</main><nav class="controls" aria-label="Навигация"><button type="button" data-dir="-1" aria-label="Предыдущий слайд">←</button><span></span><button type="button" data-dir="1" aria-label="Следующий слайд">→</button></nav><script>
const slides=[...document.querySelectorAll('.slide')],label=document.querySelector('.controls span');let index=0;
function show(i){index=Math.max(0,Math.min(slides.length-1,i));slides.forEach((s,j)=>s.hidden=j!==index);label.textContent=`${index+1} / ${slides.length}`;slides[index].focus?.()}
document.querySelectorAll('[data-dir]').forEach(b=>b.onclick=()=>show(index+Number(b.dataset.dir)));addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='PageDown')show(index+1);if(e.key==='ArrowLeft'||e.key==='PageUp')show(index-1)});show(0);
</script></html>'''
    path.write_text(doc,encoding='utf-8')
