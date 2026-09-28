import io
import zipfile
import pytest
from lxml import etree as E
from vktech.template import import_template
from vktech.opc import Package,NS,serialize,relpath
from vktech.planning import usable_prototypes, NeedsInput


def test_import_and_relationships(template_bytes):
    design=import_template(template_bytes)
    assert len(design.prototypes)==6
    assert design.fonts==['Arial']
    assert design.width==9144000
    assert [s.role for s in design.prototypes[0].slots]==['title','body']
    pkg=Package(template_bytes);before=dict(pkg.parts)
    dest=pkg.clone_graph(pkg,pkg.slides()[0],'copied')
    assert dest!=pkg.slides()[0]
    assert pkg.related(dest,'slideLayout')==pkg.related(pkg.slides()[0],'slideLayout')
    assert all(pkg.parts[p]==b for p,b in before.items() if p!='[Content_Types].xml')
    pkg.set_slides([dest]);pkg.validate();assert pkg.slides()==[dest]


def test_archive_path_and_dtd_rejected(template_bytes):
    pkg=Package(template_bytes);pkg.parts['../escape']=b'x'
    with pytest.raises(ValueError,match='path'):Package(pkg.bytes())
    pkg.parts.pop('../escape');pkg.parts['ppt/presentation.xml']=b'<!DOCTYPE doc [<!ENTITY x SYSTEM "file:///etc/passwd">]><doc/>'
    with pytest.raises(ValueError,match='DTD'):Package(pkg.bytes())


def test_group_transform(template_bytes):
    pkg=Package(template_bytes);part=pkg.slides()[0];root=pkg.root(part);tree=root.find('p:cSld/p:spTree',NS);body=list(tree)[-1];tree.remove(body)
    group=E.SubElement(tree,'{'+NS['p']+'}grpSp');gp=E.SubElement(group,'{'+NS['p']+'}grpSpPr');xf=E.SubElement(gp,'{'+NS['a']+'}xfrm')
    for name,attrs in [('off',{'x':'914400','y':'914400'}),('ext',{'cx':'1828800','cy':'1828800'}),('chOff',{'x':'0','y':'0'}),('chExt',{'cx':'914400','cy':'914400'})]:E.SubElement(xf,'{'+NS['a']+'}'+name,**attrs)
    group.append(body);pkg.parts[part]=serialize(root)
    d=import_template(pkg.bytes());slot=next(s for s in d.prototypes[0].slots if s.source_text=='Текст шаблона')
    assert slot.box.x==pytest.approx(.2)
    assert slot.box.w==pytest.approx(1.76)


@pytest.mark.parametrize('kind', [None, 'obj', 'body', 'subTitle'])
def test_empty_text_placeholders_are_usable_with_inherited_style(empty_placeholder_template, kind):
    pkg=Package(empty_placeholder_template);part=pkg.slides()[0];root=pkg.root(part)
    body=next(s for s in root.findall('.//p:sp',NS) if s.find('.//p:ph',NS).get('idx')=='1')
    ph=body.find('.//p:ph',NS)
    if kind is None:ph.attrib.pop('type',None)
    else:ph.set('type',kind)
    pkg.parts[part]=serialize(root)
    design=import_template(pkg.bytes());proto=usable_prototypes(design)[0]
    title=next(s for s in proto.slots if s.role=='title')
    body=next(s for s in proto.slots if s.role=='body')
    assert not title.source_text and not body.source_text
    assert title.box.h<.06 and title.style.size==20
    assert body.box.w>.5 and body.box.h>.4 and body.style.size==18
    assert 'DejaVu Sans' in design.fonts
    assert {18,20}<=set(design.font_sizes)


def test_empty_picture_placeholder_is_not_a_text_body(empty_placeholder_template):
    pkg=Package(empty_placeholder_template);part=pkg.slides()[0];root=pkg.root(part)
    body=next(s for s in root.findall('.//p:sp',NS) if s.find('.//p:ph',NS).get('idx')=='1')
    body.find('.//p:ph',NS).set('type','pic');pkg.parts[part]=serialize(root)
    design=import_template(pkg.bytes())
    assert not any(s.role=='body' for s in design.prototypes[0].slots)
    with pytest.raises(NeedsInput):usable_prototypes(design)


def test_zero_height_title_is_still_rejected(empty_placeholder_template):
    design=import_template(empty_placeholder_template)
    next(s for s in design.prototypes[0].slots if s.role=='title').box.h=0
    with pytest.raises(NeedsInput):usable_prototypes(design)
