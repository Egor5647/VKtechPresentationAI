import io
import zipfile
import pytest
from lxml import etree as E
from vktech.template import import_template
from vktech.opc import Package,NS,serialize,relpath


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
