from __future__ import annotations
import math
from collections import Counter
from .opc import Package, NS
from .contracts import DesignIR, Prototype, Slot, Box, Style


def color(node, theme):
    if node is None:
        return None
    c = node.find("a:srgbClr", NS)
    if c is not None:
        value = c.get("val")
    else:
        c = node.find("a:schemeClr", NS)
        value = theme.get(c.get("val"), "202020") if c is not None else None
    if not value or len(value) != 6:
        return None
    # Apply common DrawingML luminance and tint transformations.
    rgb = [int(value[i:i+2], 16) / 255 for i in (0, 2, 4)]
    for t in c:
        v = int(t.get("val", "0")) / 100000
        name = t.tag.split("}")[-1]
        if name in {"lumMod", "shade"}: rgb = [x*v for x in rgb]
        elif name == "lumOff": rgb = [x+v for x in rgb]
        elif name == "tint": rgb = [x+(1-x)*v for x in rgb]
    return "".join(f"{round(max(0,min(1,x))*255):02X}" for x in rgb)


class Resolver:
    def __init__(self, package, slide):
        self.package = package
        self.layout_part = package.related(slide, "slideLayout")
        self.master_part = package.related(self.layout_part, "slideMaster") if self.layout_part else None
        self.layout = package.root(self.layout_part) if self.layout_part else None
        self.master = package.root(self.master_part) if self.master_part else None
        self.theme_part = package.related(self.master_part, "theme") if self.master_part else None
        self.theme = package.root(self.theme_part) if self.theme_part else None
        self.colors = {"tx1":"202020", "bg1":"FFFFFF", "tx2":"202020", "bg2":"FFFFFF"}
        self.fonts = {"+mj-lt":"Arial", "+mn-lt":"Arial"}
        if self.theme is not None:
            for n in self.theme.findall("a:themeElements/a:clrScheme/*", NS):
                child = next(iter(n), None)
                if child is not None:
                    self.colors[n.tag.split("}")[-1]] = child.get("val") if child.tag.endswith("srgbClr") else child.get("lastClr", "202020")
            for role, key in (("majorFont", "+mj-lt"), ("minorFont", "+mn-lt")):
                el = self.theme.find(f"a:themeElements/a:fontScheme/a:{role}/a:latin", NS)
                if el is not None: self.fonts[key] = el.get("typeface") or "Arial"
        cmap = self.master.find("p:clrMap", NS) if self.master is not None else None
        if cmap is not None:
            for key, v in cmap.attrib.items(): self.colors[key] = self.colors.get(v,"202020")

    def inherited(self, shape):
        ph = shape.find(".//p:ph", NS)
        if ph is None:
            return []
        result = []
        for root in (self.layout, self.master):
            if root is None: continue
            matches = root.findall(".//p:sp", NS)
            candidate = next((s for s in matches if s.find(".//p:ph", NS) is not None and
                              s.find(".//p:ph", NS).get("idx", "0") == ph.get("idx", "0")), None)
            if candidate is None:
                candidate = next((s for s in matches if s.find(".//p:ph", NS) is not None and
                                  s.find(".//p:ph", NS).get("type", "body") == ph.get("type", "body")), None)
            if candidate is not None: result.append(candidate)
        return result

    def style(self, shape):
        inherited = self.inherited(shape)
        candidates = []
        # Resolve each property independently, nearest definition wins.
        for s, label in [(shape,"slide")]+[(s,"layout" if i==0 else "master") for i,s in enumerate(inherited)]:
            for path in (".//a:r/a:rPr", ".//a:p/a:pPr/a:defRPr", ".//a:p/a:endParaRPr", ".//a:lstStyle/a:lvl1pPr/a:defRPr"):
                n = s.find(path, NS)
                if n is not None: candidates.append((n,label))
        ph = shape.find(".//p:ph", NS)
        role = "titleStyle" if ph is not None and ph.get("type") in ("title","ctrTitle") else "bodyStyle" if ph is not None else "otherStyle"
        if self.master is not None:
            n = self.master.find(f"p:txStyles/p:{role}/a:lvl1pPr/a:defRPr", NS)
            if n is not None: candidates.append((n,"master_text_style"))
        st = Style()
        for prop in ("font","size","color","bold"):
            for n,label in candidates:
                value = None
                if prop == "font":
                    latin = n.find("a:latin", NS)
                    value = latin.get("typeface") if latin is not None else None
                    value = self.fonts.get(value,value)
                elif prop == "size": value = int(n.get("sz"))/100 if n.get("sz") else None
                elif prop == "bold": value = n.get("b") in ("1","true") if n.get("b") is not None else None
                elif prop == "color": value = color(n.find("a:solidFill", NS),self.colors)
                if value is not None:
                    setattr(st,prop,value); st.source[prop] = label; break
        if "font" not in st.source:
            ref = shape.find("p:style/a:fontRef",NS)
            st.font = self.fonts["+mj-lt" if ref is not None and ref.get("idx")=="major" else "+mn-lt"]
            st.source["font"] = "theme"
        st.fill = color(shape.find("p:spPr/a:solidFill",NS),self.colors)
        pp = shape.find(".//a:p/a:pPr",NS)
        if pp is not None: st.align = {"ctr":"center","r":"right"}.get(pp.get("algn"),"left")
        return st

    def geometry(self, shape):
        candidates = [shape] + self.inherited(shape)
        for s in candidates:
            x = s.find("p:spPr/a:xfrm",NS)
            if x is None: x = s.find("p:xfrm",NS)
            if x is not None:
                off,ext = x.find("a:off",NS),x.find("a:ext",NS)
                if off is not None and ext is not None:
                    return [float(off.get("x")),float(off.get("y")),float(ext.get("cx")),float(ext.get("cy"))], x
        return [0,0,0,0],None


def import_template(data: bytes) -> DesignIR:
    pkg = Package(data)
    pres = pkg.root("ppt/presentation.xml")
    size = pres.find("p:sldSz",NS)
    width,height = int(size.get("cx")),int(size.get("cy"))
    prototypes, warnings = [],[]
    fonts,sizes,palette = Counter(),Counter(),Counter()
    for index,part in enumerate(pkg.slides()):
        root = pkg.root(part); resolver = Resolver(pkg,part); slots=[]
        bg = None
        for r in (root,resolver.layout,resolver.master):
            if r is not None:
                bg = color(r.find("p:cSld/p:bg/p:bgPr/a:solidFill",NS),resolver.colors)
                if bg: break
        def walk(parent, matrix=(1.,0.,0.,1.,0.,0.), path=""):
            a,b,c,d,e,f = matrix
            for i,shape in enumerate(parent):
                tag = shape.tag.split("}")[-1]; spath=f"{path}/{i}"
                if tag == "grpSp":
                    x = shape.find("p:grpSpPr/a:xfrm",NS)
                    if x is None: walk(shape,matrix,spath); continue
                    vals = {k:x.find("a:"+k,NS) for k in ("off","ext","chOff","chExt")}
                    if any(v is None for v in vals.values()): walk(shape,matrix,spath); continue
                    sx=int(vals['ext'].get('cx'))/max(1,int(vals['chExt'].get('cx')))
                    sy=int(vals['ext'].get('cy'))/max(1,int(vals['chExt'].get('cy')))
                    tx=int(vals['off'].get('x'))-sx*int(vals['chOff'].get('x'))
                    ty=int(vals['off'].get('y'))-sy*int(vals['chOff'].get('y'))
                    angle=int(x.get("rot","0"))/60000*math.pi/180
                    ca,sa=math.cos(angle),math.sin(angle)
                    # Rotate around the group centre and apply flip flags.
                    fx=-1 if x.get('flipH')=='1' else 1; fy=-1 if x.get('flipV')=='1' else 1
                    ga,gb,gc,gd=ca*sx*fx,sa*sx*fx,-sa*sy*fy,ca*sy*fy
                    cx=int(vals['chOff'].get('x'))+int(vals['chExt'].get('cx'))/2
                    cy=int(vals['chOff'].get('y'))+int(vals['chExt'].get('cy'))/2
                    tx=int(vals['off'].get('x'))+int(vals['ext'].get('cx'))/2-ga*cx-gc*cy
                    ty=int(vals['off'].get('y'))+int(vals['ext'].get('cy'))/2-gb*cx-gd*cy
                    walk(shape,(a*ga+c*gb,b*ga+d*gb,a*gc+c*gd,b*gc+d*gd,a*tx+c*ty+e,b*tx+d*ty+f),spath)
                elif tag in {"sp","pic","graphicFrame"}:
                    nv=shape.find(".//p:cNvPr",NS)
                    if nv is None: continue
                    sid=int(nv.get('id')); text="\n".join("".join(p.itertext()) for p in shape.findall("p:txBody/a:p",NS))
                    # itertext includes only actual text for normal paragraphs, use a:t explicitly.
                    text="\n".join("".join(p.xpath('.//a:t/text()',namespaces=NS)) for p in shape.findall("p:txBody/a:p",NS)).strip()
                    box,x=resolver.geometry(shape); xx,yy,w,h=box
                    pts=[(a*px+c*py+e,b*px+d*py+f) for px,py in ((xx,yy),(xx+w,yy),(xx,yy+h),(xx+w,yy+h))]
                    box=Box(x=min(p[0] for p in pts)/width,y=min(p[1] for p in pts)/height,w=(max(p[0] for p in pts)-min(p[0] for p in pts))/width,h=(max(p[1] for p in pts)-min(p[1] for p in pts))/height)
                    st=resolver.style(shape)
                    ph=shape.find('.//p:ph',NS); typ=ph.get('type','obj') if ph is not None else ''
                    content_visual=(tag in ('pic','graphicFrame') and box.w*box.h>.04) or (not text and .15<box.y<.88 and box.y+box.h<.9 and 0<box.w*box.h<.65)
                    role='footer' if typ in ('ftr','dt','sldNum') or (box.y>.9 and len(text)<90) else 'body' if text else 'visual' if content_visual else 'decor'
                    if typ in ('title','ctrTitle'): role='title'
                    elif tag=='sp' and typ in ('body','obj','subTitle'):
                        # Empty placeholders are intentional editable text slots;
                        # their geometry and typography can come from the layout.
                        role='body'
                    slots.append(Slot(id=f"s{sid}",shape_id=sid,role=role,box=box,style=st,source_text=text,source_path=spath))
                    if text or role in ('title','body'):
                        fonts[st.font]+=max(1,len(text)); sizes[st.size]+=1; palette[st.color]+=1
                    if st.fill: palette[st.fill]+=1
        tree=root.find('p:cSld/p:spTree',NS)
        if tree is not None: walk(tree)
        bodies=[s for s in slots if s.role=='body']
        if not any(s.role=='title' for s in slots) and bodies:
            candidate=min(bodies,key=lambda s:(s.box.y, -s.style.size))
            if candidate.box.y<.4: candidate.role='title'
        proto=Prototype(id=f"slide-{index+1}",slide_part=part,layout_part=resolver.layout_part or '',slots=slots,background=bg or 'FFFFFF')
        prototypes.append(proto); palette[proto.background]+=1
    if not any(any(s.role=='title' for s in p.slots) for p in prototypes):
        warnings.append('No title slots found: generated title placement requires review.')
    if any('VK Sans' in s.source_text for p in prototypes for s in p.slots) and 'VK Sans Display Medium' not in fonts:
        warnings.append('Brand text mentions VK Sans; effective PPTX formatting takes precedence.')
    external=[{"part":p,"id":r.get('Id'),"target":r.get('Target')} for p in pkg.parts if p.endswith('.xml') for r in pkg.relations(p) if r.get('TargetMode')=='External']
    return DesignIR(id=pkg.source_hash[:24],source_hash=pkg.source_hash,width=width,height=height,fonts=list(fonts),font_sizes=sorted(sizes),palette=list(palette),prototypes=prototypes,warnings=warnings,evidence={"fonts":dict(fonts),"font_sizes":dict(sizes),"external_relationships":external,"source":"OOXML property resolver","confidence":"structural; visual verification required"})
