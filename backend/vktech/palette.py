from __future__ import annotations

import colorsys
import io
import re
from pathlib import Path

from .audit import contrast
from .contracts import ContentIR, DesignIR, PaletteSpec, SceneIR
from .opc import NS, Package, serialize
from .settings import artifact_path


def _rgb(value: str) -> tuple[float,float,float]:
    value=value.lstrip('#')
    return tuple(int(value[index:index+2],16)/255 for index in (0,2,4))


def _hex(rgb) -> str:
    return ''.join(f'{round(max(0,min(1,value))*255):02X}' for value in rgb)


def _hls(value: str):
    return colorsys.rgb_to_hls(*_rgb(value))


def _hue_distance(left: float,right: float) -> float:
    distance=abs(left-right)
    return min(distance,1-distance)


def palette_from_design(design: DesignIR) -> PaletteSpec:
    colors=list(dict.fromkeys(color.upper() for color in design.palette if re.fullmatch(r'[0-9A-Fa-f]{6}',color or '')))
    colors+=['FFFFFF','E8EEF6','0077FF','31C48D','172438']
    values=[(color,*_hls(color)) for color in colors]
    accent=next((color for color in colors if color in {'0077FF','2688EB','005FF9'}),None)
    if accent is None:accent=max(values,key=lambda item:item[3]*(1-abs(item[2]-.48)))[0]
    ah=_hls(accent)[0]
    secondary_candidates=[item for item in values if item[2]<.76 and _hue_distance(item[1],ah)>.12]
    secondary=max(secondary_candidates or values,key=lambda item:item[3]*_hue_distance(item[1],ah)*(1-abs(item[2]-.55)))[0]
    background=max(values,key=lambda item:item[2]-item[3]*.15)[0]
    light=[item for item in values if .76<=item[2]<.98 and item[3]<=.65]
    surface=max(light,key=lambda item:item[3]*.7+(1-_hue_distance(item[1],ah))*.3,default=("E8EEF6",0,0,0))[0]
    dark=[item for item in values if item[2]<.38]
    text=min(dark,key=lambda item:item[2],default=("172438",0,0,0))[0]
    on='FFFFFF' if contrast('FFFFFF',accent)>=3 else text
    return PaletteSpec(background=background,surface=surface,accent=accent,accent_secondary=secondary,text_primary=text,text_on_accent=on)


def _source_accent(design: DesignIR) -> str:
    source=palette_from_design(design)
    return source.accent


def map_color(value: str,design: DesignIR,palette: PaletteSpec) -> str:
    if not re.fullmatch(r'[0-9A-Fa-f]{6}',value or ''):return value
    value=value.upper();h,l,s=_hls(value)
    if l>=.94:return palette.background
    if l>=.82:return palette.surface
    if s<=.12:
        if l<=.38:return palette.text_primary
        start=_rgb(palette.text_primary);end=_rgb(palette.surface);ratio=(l-.38)/.44
        return _hex(tuple(a+(b-a)*ratio for a,b in zip(start,end)))
    source=_source_accent(design);source_h,source_l,_=_hls(source)
    target=palette.accent if _hue_distance(h,source_h)<.20 else palette.accent_secondary
    target_h,target_l,target_s=_hls(target)
    lightness=max(.12,min(.9,target_l+(l-source_l)*.68))
    return _hex(colorsys.hls_to_rgb(target_h,lightness,max(.35,target_s)))


def readable_text(background: str,palette: PaletteSpec,large=False) -> str:
    threshold=3 if large else 4.5
    candidates=(palette.text_primary,palette.text_on_accent,'202020','FFFFFF')
    valid=[color for color in candidates if contrast(color,background)>=threshold]
    return max(valid or candidates,key=lambda color:contrast(color,background))


def recolor_scene(scene: SceneIR,design: DesignIR,palette: PaletteSpec) -> SceneIR:
    result=scene.model_copy(deep=True)
    for slide in result.slides:
        slide.background=map_color(slide.background,design,palette)
        for node in slide.nodes:
            if node.style.fill:node.style.fill=map_color(node.style.fill,design,palette)
            if node.role=='accent' and not node.text.strip():
                marker=palette.accent if contrast(palette.accent,slide.background)>=3 else palette.text_on_accent
                node.style.color=node.style.fill=marker
            elif node.kind=='text':
                background=node.style.fill or slide.background
                node.style.color=readable_text(background,palette,node.style.size>=24 or node.style.bold)
            else:node.style.color=map_color(node.style.color,design,palette)
            if node.kind in {'diagram','smartart'}:
                node.data['accent']=palette.accent if contrast(palette.accent,slide.background)>=3 else palette.text_on_accent
                node.data['surface']=palette.surface
    result.version+=1
    return result


def recolor_generated_assets(content: ContentIR,design: DesignIR,palette: PaletteSpec,folder: Path) -> tuple[ContentIR,dict[str,str]]:
    """Shift generated illustration hues while leaving supplied source images intact."""
    from PIL import Image
    result=content.model_copy(deep=True);replacements={}
    for asset in result.assets:
        if not (asset.source.startswith('Z-Image') or asset.source.startswith('AI-generated')):continue
        source=artifact_path(asset.path)
        if not source.exists():continue
        with Image.open(source) as opened:
            recolored=_tone_image(opened,design,palette)
            target=folder/(asset.id+'-palette.png');recolored.save(target,'PNG')
        old=asset.path;asset.path=str(target.relative_to(artifact_path('.')));asset.media_type='image/png';replacements[old]=asset.path
    return result,replacements


def _tone_image(opened,design: DesignIR,palette: PaletteSpec):
    from PIL import Image
    alpha=opened.getchannel('A') if opened.mode=='RGBA' else None
    h,s,v=opened.convert('RGB').convert('HSV').split()
    source_h,source_l,source_s=_hls(_source_accent(design))
    target_h,target_l,target_s=_hls(palette.accent);secondary_h,_,_=_hls(palette.accent_secondary)
    def mapped_hue(value):
        current=value/255
        return round((target_h if _hue_distance(current,source_h)<.20 else secondary_h)*255)
    h=h.point(mapped_hue)
    s=s.point(lambda value:min(255,round(value*max(.65,min(1.35,target_s/max(.01,source_s))))))
    darker=v.point(lambda value:min(255,round(value*max(.55,min(1.25,target_l/max(.01,source_l))))))
    # Saturated pixels carry the illustration palette; neutral and white pixels
    # keep their original value so backgrounds, type and logos stay clean.
    v=Image.composite(darker,v,s)
    result=Image.merge('HSV',(h,s,v)).convert('RGB')
    if alpha is not None:
        result=result.convert('RGBA');result.putalpha(alpha)
    return result


def _is_flat_decoration(opened) -> bool:
    sample=opened.convert('RGB');sample.thumbnail((128,128))
    if max(opened.size)/max(1,min(opened.size))>4:return False
    _,s,_=sample.convert('HSV').split();pixels=sample.width*sample.height
    saturated=sum(s.histogram()[52:])/max(1,pixels)
    counts=sorted(sample.quantize(colors=16).getcolors() or [],reverse=True)
    dominant=sum(count for count,_ in counts[:4])/max(1,pixels)
    return saturated>.08 and sample.entropy()<6 and dominant>.74


def replace_scene_asset_paths(scene: SceneIR,replacements: dict[str,str]) -> SceneIR:
    result=scene.model_copy(deep=True)
    for slide in result.slides:
        for node in slide.nodes:
            if node.kind=='image' and node.data.get('path') in replacements:
                node.data['path']=replacements[node.data['path']]
                node.data['media_type']='image/png'
    return result


def recolor_design(design: DesignIR,palette: PaletteSpec) -> DesignIR:
    result=design.model_copy(deep=True)
    result.palette=list(dict.fromkeys([palette.background,palette.surface,palette.accent,palette.accent_secondary,palette.text_primary,palette.text_on_accent,*[map_color(color,design,palette) for color in design.palette]]))
    for prototype in result.prototypes:
        prototype.background=map_color(prototype.background,design,palette)
        for slot in prototype.slots:
            if slot.style.fill:slot.style.fill=map_color(slot.style.fill,design,palette)
            slot.style.color=map_color(slot.style.color,design,palette)
    result.evidence={**result.evidence,'palette_override':palette.model_dump()}
    return result


def _protected_color(node) -> bool:
    current=node
    while current is not None:
        if current.tag in {'{'+NS['p']+'}sp','{'+NS['p']+'}pic','{'+NS['p']+'}graphicFrame'}:
            marker=current.find('.//p:cNvPr',NS)
            label=' '.join((marker.get('name',''),marker.get('descr',''),marker.get('title',''))) if marker is not None else ''
            return bool(re.search(r'logo|logotype|логотип|\bvk\b',label,re.I))
        current=current.getparent()
    return False


def recolor_template(data: bytes,design: DesignIR,palette: PaletteSpec) -> bytes:
    package=Package(data)
    prefixes=('ppt/slides/','ppt/slideLayouts/','ppt/slideMasters/','ppt/theme/')
    for name in list(package.parts):
        if not name.endswith('.xml') or not name.startswith(prefixes):continue
        root=package.root(name);changed=False
        for node in root.findall('.//a:srgbClr',NS):
            value=node.get('val','')
            if re.fullmatch(r'[0-9A-Fa-f]{6}',value) and not _protected_color(node):
                node.set('val',map_color(value,design,palette));changed=True
        for node in root.findall('.//a:sysClr',NS):
            value=node.get('lastClr','')
            if re.fullmatch(r'[0-9A-Fa-f]{6}',value) and not _protected_color(node):
                node.set('lastClr',map_color(value,design,palette));changed=True
        if changed:package.parts[name]=serialize(root)
    from PIL import Image
    for name,value in list(package.parts.items()):
        if not name.startswith('ppt/media/') or not name.lower().endswith(('.png','.jpg','.jpeg','.webp')):continue
        try:
            with Image.open(io.BytesIO(value)) as opened:
                if not _is_flat_decoration(opened):continue
                recolored=_tone_image(opened,design,palette);output=io.BytesIO()
                recolored.save(output,'PNG' if name.lower().endswith('.png') else opened.format)
                package.parts[name]=output.getvalue()
        except (OSError,ValueError):
            continue
    package.validate();return package.bytes()
