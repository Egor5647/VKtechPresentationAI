"""Structural/render smoke test on local templates with a named synthetic plan.

This is not a real model demonstration and never supplies production inference.
"""
import argparse
import json
import time
from pathlib import Path
from vktech.contracts import ContentIR,PresentationPlan,PlanSlide
from vktech.template import import_template
from vktech.planning import build_scenes,validate_plan
from vktech.export import export_pptx,render,export_html
from vktech.audit import audit_scene

parser=argparse.ArgumentParser();parser.add_argument('templates',type=Path);parser.add_argument('--output',type=Path,default=Path('output/smoke'));args=parser.parse_args()
content=ContentIR.model_validate_json(Path('fixtures/content.example.json').read_bytes())
plan=PresentationPlan(slides=[PlanSlide(id=f'slide-{i+1}',title=c.text,message=c.text,claim_ids=[c.id],dataset_id='metrics' if i==5 else None,visual='chart' if i==5 else 'sequence' if i==3 else 'none',role='cover' if i==0 else 'content') for i,c in enumerate(content.claims)])
validate_plan(plan,content,12);report=[]
for index,file in enumerate(sorted(args.templates.glob('*.pptx'))):
 start=time.monotonic();design=import_template(file.read_bytes());folder=args.output/f'template-{index+1}';folder.mkdir(parents=True,exist_ok=True)
 (folder/'design.json').write_text(design.model_dump_json(indent=2))
 for scene in build_scenes(design,content,plan,'smoke'):
  out=folder/scene.variant;out.mkdir(exist_ok=True);pptx=out/'presentation.pptx'
  export_pptx(file.read_bytes(),design,scene,pptx);pdf,images=render(pptx,out/'render')
  decor=out/'decor.pptx';export_pptx(file.read_bytes(),design,scene,decor,decor_only=True);_,backgrounds=render(decor,out/'background')
  export_html(scene,out/'presentation.html',backgrounds)
  audit=audit_scene(scene,design,content,opened=True)
  (out/'scene.json').write_text(scene.model_dump_json(indent=2));(out/'audit.json').write_text(audit.model_dump_json(indent=2))
  report.append({'template':file.name,'variant':scene.variant,'slide_count':len(images),'failures':sum(i.status=='fail' for i in audit.issues),'unknowns':sum(i.status=='unknown' for i in audit.issues),'pptx_bytes':pptx.stat().st_size})
 print(file.name,round(time.monotonic()-start,2),'s',flush=True)
args.output.mkdir(parents=True,exist_ok=True);(args.output/'report.json').write_text(json.dumps({'inference':'explicit synthetic plan; no model call','results':report},ensure_ascii=False,indent=2))
