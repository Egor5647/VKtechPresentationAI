"""Native DrawingML diagram parts, with a synchronized Office drawing cache."""
from __future__ import annotations
import copy
import uuid
from lxml import etree as E
from .opc import NS, serialize, relpath
from .settings import ROOT

D=NS['dgm']; A=NS['a']; R=NS['r']; P=NS['p']; DSP='http://schemas.microsoft.com/office/drawing/2008/diagram'


def sub(root,ns,tag,**attrs):
    return E.SubElement(root,'{'+ns+'}'+tag,{k:str(v) for k,v in attrs.items()})


def uid(seed): return '{'+str(uuid.uuid5(uuid.NAMESPACE_URL,seed)).upper()+'}'


def text(root,text,style):
    sub(root,A,'bodyPr'); sub(root,A,'lstStyle'); p=sub(root,A,'p'); r=sub(p,A,'r')
    rp=sub(r,A,'rPr',sz=round(style.size*100),lang='ru-RU')
    sub(sub(rp,A,'solidFill'),A,'srgbClr',val=style.color)
    sub(rp,A,'latin',typeface=style.font)
    sub(r,A,'t').text=text


def inject(pkg,slide_part,node,shape_id,width,height):
    items=node.data.get('items',[])
    if not items: raise ValueError('SmartArt requires at least one item')
    layout_kind=node.data.get('layout','list')
    prefix='ppt/diagrams/'+node.id.replace('/','_')
    parts={k:prefix+'_'+k+'.xml' for k in ('data','layout','colors','quickStyle','drawing')}
    # A minimal semantic tree plus presentation nodes. Hierarchy uses first item as parent.
    model=E.Element('{'+D+'}dataModel',nsmap={'dgm':D,'a':A}); pts=sub(model,D,'ptLst'); cxns=sub(model,D,'cxnLst')
    docid=uid(node.id+'doc'); doc=sub(pts,D,'pt',modelId=docid,type='doc')
    sub(doc,D,'prSet',loTypeId='urn:vktech:layout:'+layout_kind,loCatId='list',qsTypeId='urn:vktech:style:flat',csTypeId='urn:vktech:colors:brand')
    sub(doc,D,'spPr'); text(sub(doc,D,'t'),'',node.style)
    rootpres=uid(node.id+'rootpres'); pr=sub(pts,D,'pt',modelId=rootpres,type='pres')
    sub(pr,D,'prSet',presAssocID=docid,presName='diagram',presStyleCnt=0);sub(pr,D,'spPr')
    sub(cxns,D,'cxn',modelId=uid(node.id+'rootmap'),type='presOf',srcId=docid,destId=rootpres,srcOrd=0,destOrd=0)
    ids=[uid(node.id+str(i)) for i in range(len(items))]
    for i,(label,cid) in enumerate(zip(items,ids)):
        pt=sub(pts,D,'pt',modelId=cid);sub(pt,D,'prSet');sub(pt,D,'spPr');text(sub(pt,D,'t'),label,node.style)
        pid=uid(node.id+'pres'+str(i));pr=sub(pts,D,'pt',modelId=pid,type='pres')
        sub(pr,D,'prSet',presAssocID=cid,presName='node',presStyleLbl='node1',presStyleIdx=i,presStyleCnt=len(items));sub(pr,D,'spPr')
        parent=ids[0] if layout_kind=='hierarchy' and i else docid
        sub(cxns,D,'cxn',modelId=uid(node.id+'cxn'+str(i)),srcId=parent,destId=cid,srcOrd=i,destOrd=0)
        sub(cxns,D,'cxn',modelId=uid(node.id+'map'+str(i)),type='presOf',srcId=cid,destId=pid,srcOrd=0,destOrd=0)
        sub(cxns,D,'cxn',modelId=uid(node.id+'prescxn'+str(i)),type='presParOf',srcId=rootpres,destId=pid,srcOrd=i,destOrd=0)
    sub(model,D,'bg');sub(model,D,'whole')
    ext=sub(sub(model,D,'extLst'),A,'ext',uri=DSP)
    sub(ext,DSP,'dataModelExt',relId='rIdDrawing',minVer=D)
    layout=E.parse(str(ROOT/'resources/smartart/layout1.xml')).getroot()
    layout.set('uniqueId','urn:vktech:layout:'+layout_kind)
    # Linear variants use DrawingML lin algorithm; the hierarchy starts as a tree.
    ln=layout.find('dgm:layoutNode',NS)
    for child in list(ln):
        if E.QName(child).localname=='choose': ln.remove(child)
    alg=E.Element('{'+D+'}alg',type='hierRoot' if layout_kind=='hierarchy' else 'lin')
    sub(alg,D,'param',type='linDir',val='fromL' if layout_kind=='sequence' else 'fromT')
    ln.insert(1,alg)
    for shape in layout.findall('.//dgm:shape',NS):
        shape.attrib.pop('{'+R+'}blip',None)
    colors=E.Element('{'+D+'}colorsDef',nsmap={'dgm':D,'a':A},uniqueId='urn:vktech:colors:brand')
    sub(colors,D,'title',val='Template colors');sub(colors,D,'desc',val='Colors extracted from template');sub(colors,D,'catLst')
    cl=sub(colors,D,'styleLbl',name='node1')
    for key in ('fillClrLst','linClrLst','txFillClrLst','txLinClrLst','effectClrLst'):
        c=sub(cl,D,key,meth='repeat');sub(c,A,'srgbClr',val=node.style.fill or ('E8EEF6' if key=='fillClrLst' else node.style.color))
    qs=E.Element('{'+D+'}styleDef',nsmap={'dgm':D,'a':A},uniqueId='urn:vktech:style:flat')
    sub(qs,D,'title',val='Flat');sub(qs,D,'desc',val='Native editable diagram');sub(qs,D,'catLst')
    lab=sub(qs,D,'styleLbl',name='node1');style=sub(lab,D,'style')
    for name,idx in (('lnRef',0),('fillRef',1),('effectRef',0)):
        sub(sub(style,A,name,idx=idx),A,'schemeClr',val='accent1')
    sub(sub(style,A,'fontRef',idx='minor'),A,'schemeClr',val='tx1')
    drawing=E.Element('{'+DSP+'}drawing',nsmap={'dsp':DSP,'a':A});tree=sub(drawing,DSP,'spTree')
    nv=sub(tree,DSP,'nvGrpSpPr');sub(nv,DSP,'cNvPr',id=0,name='');sub(nv,DSP,'cNvGrpSpPr');sub(nv,DSP,'nvPr')
    gp=sub(tree,DSP,'grpSpPr');xf=sub(gp,A,'xfrm')
    w,h=round(node.box.w*width),round(node.box.h*height)
    for name,attrs in [('off',{'x':0,'y':0}),('ext',{'cx':w,'cy':h}),('chOff',{'x':0,'y':0}),('chExt',{'cx':w,'cy':h})]:sub(xf,A,name,**attrs)
    for i,label in enumerate(items):
        horizontal=layout_kind=='sequence';bw=w/len(items) if horizontal else w;bh=h if horizontal else h/len(items)
        x=i*bw if horizontal else 0;y=0 if horizontal else i*bh
        sp=sub(tree,DSP,'sp',modelId=uid(node.id+'pres'+str(i)))
        nv=sub(sp,DSP,'nvSpPr');sub(nv,DSP,'cNvPr',id=i+1,name=f'Node {i+1}');sub(nv,DSP,'cNvSpPr');sub(nv,DSP,'nvPr')
        prop=sub(sp,DSP,'spPr');xf=sub(prop,A,'xfrm');sub(xf,A,'off',x=round(x),y=round(y));sub(xf,A,'ext',cx=round(bw*.95),cy=round(bh*.9))
        geom=sub(prop,A,'prstGeom',prst='roundRect');sub(geom,A,'avLst')
        sub(sub(prop,A,'solidFill'),A,'srgbClr',val=node.style.fill or 'E8EEF6')
        text(sub(sp,DSP,'txBody'),label,node.style)
    for kind,root in [('data',model),('layout',layout),('colors',colors),('quickStyle',qs),('drawing',drawing)]:pkg.parts[parts[kind]]=serialize(root)
    rr=E.Element('{'+NS['rel']+'}Relationships',nsmap={None:NS['rel']})
    sub(rr,NS['rel'],'Relationship',Id='rIdDrawing',Type='http://schemas.microsoft.com/office/2007/relationships/diagramDrawing',Target=parts['drawing'].rsplit('/',1)[-1])
    pkg.parts[relpath(parts['data'])]=serialize(rr)
    ct=pkg.root('[Content_Types].xml')
    for kind,part in parts.items():
        ctype='application/vnd.openxmlformats-officedocument.drawingml.diagram'+{'data':'Data','layout':'Layout','colors':'Colors','quickStyle':'Style'}.get(kind,'')+'+xml' if kind!='drawing' else 'application/vnd.ms-office.drawingml.diagramDrawing+xml'
        sub(ct,NS['ct'],'Override',PartName='/'+part,ContentType=ctype)
    pkg.parts['[Content_Types].xml']=serialize(ct)
    rr=pkg.root(relpath(slide_part));ridmap={}
    import posixpath
    for key,kind in [('dm','data'),('lo','layout'),('qs','quickStyle'),('cs','colors')]:
        rid=f'rIdSmartArt{shape_id}{key}';ridmap[key]=rid
        reltype={'data':'diagramData','layout':'diagramLayout','quickStyle':'diagramQuickStyle','colors':'diagramColors'}[kind]
        sub(rr,NS['rel'],'Relationship',Id=rid,Type=R+'/'+reltype,Target=posixpath.relpath(parts[kind],'ppt/slides'))
    pkg.parts[relpath(slide_part)]=serialize(rr)
    frame=E.Element('{'+P+'}graphicFrame',nsmap={'p':P,'a':A,'dgm':D,'r':R})
    nv=sub(frame,P,'nvGraphicFramePr');sub(nv,P,'cNvPr',id=shape_id,name=node.id);sub(nv,P,'cNvGraphicFramePr');sub(nv,P,'nvPr')
    xf=sub(frame,P,'xfrm');sub(xf,A,'off',x=round(node.box.x*width),y=round(node.box.y*height));sub(xf,A,'ext',cx=w,cy=h)
    gd=sub(sub(frame,A,'graphic'),A,'graphicData',uri=D)
    sub(gd,D,'relIds',**{'{'+R+'}'+k:v for k,v in ridmap.items()})
    return frame
