from __future__ import annotations
import io
import pytest
from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from vktech.contracts import ContentIR,PresentationPlan,PlanSlide
from vktech.settings import ROOT


@pytest.fixture
def template_bytes():
    prs=Presentation();prs.slide_width=Inches(10);prs.slide_height=Inches(5.625)
    for i in range(6):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        slide.background.fill.solid();slide.background.fill.fore_color.rgb=RGBColor(255,255,255)
        title=slide.shapes.add_textbox(Inches(.5),Inches(.3),Inches(9),Inches(.8));title.text='Заголовок'
        p=title.text_frame.paragraphs[0];p.font.name='Arial';p.font.size=Pt(28);p.font.color.rgb=RGBColor.from_string('202020')
        body=slide.shapes.add_textbox(Inches(.5+i*.04),Inches(1.4),Inches(8.8-i*.08),Inches(3.6));body.text='Текст шаблона'
        p=body.text_frame.paragraphs[0];p.font.name='Arial';p.font.size=Pt(18);p.font.color.rgb=RGBColor.from_string('202020')
    b=io.BytesIO();prs.save(b);return b.getvalue()


@pytest.fixture
def content():return ContentIR.model_validate_json((ROOT/'fixtures/content.example.json').read_bytes())


@pytest.fixture
def plan(content):
    return PresentationPlan(slides=[PlanSlide(id=f'slide-{i+1}',title=c.text,message=c.text,claim_ids=[c.id],dataset_id='metrics' if i==5 else None,visual='chart' if i==5 else 'sequence' if i==3 else 'none',role='cover' if i==0 else 'content') for i,c in enumerate(content.claims)])


class FixtureGateway:
    """Explicit test double. Never reachable from the production API/config."""
    profile='test'
    manifest={'test_double':True}
    calls=[]
    def __init__(self,plan):self.plan=plan
    def structured(self,role,payload,schema,images=None):
        if role=='planning':return self.plan
        from vktech.contracts import ContextualReport
        return ContextualReport(issues=[])
