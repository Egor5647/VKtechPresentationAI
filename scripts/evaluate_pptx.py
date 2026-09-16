#!/usr/bin/env python3
"""Run one real-model acceptance job from a PPTX content package."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('content',type=Path)
    parser.add_argument('template',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--slides',type=int,default=12)
    parser.add_argument('--brief',default='Учебная презентация о массивах и файловом вводе-выводе в C++ для повторения материала')
    args=parser.parse_args()

    args.output.mkdir(parents=True,exist_ok=True)
    os.environ['DATA_DIR']=str(args.output.resolve())
    os.environ['DATABASE_URL']='sqlite:///'+str((args.output/'evaluation.sqlite').resolve())

    from vktech.content import import_content
    from vktech.model import ModelGateway
    from vktech.store import Store
    from vktech.template import import_template
    from vktech.worker import execute

    store=Store();content_bytes=args.content.read_bytes()
    def writer(data,name,extension):
        rid=store.save_record('asset',name,data,{},extension)
        return store.record(rid,'asset').path
    content=import_content(content_bytes,args.content.name,writer)
    template_bytes=args.template.read_bytes();design=import_template(template_bytes)
    template_id=store.save_record('template',args.template.name,template_bytes,design.model_dump(),'pptx')
    content_id=store.save_record('content',args.content.name,content_bytes,content.model_dump(),'pptx')
    request={'template_id':template_id,'content_id':content_id,'brief':args.brief,'purpose':'education','slide_count':args.slides,'generate_images':False}
    job_id=store.enqueue('generate',request)
    execute(store,store.claim(),ModelGateway())
    job=store.job(job_id)
    summary={'job_id':job_id,'state':job.state,'stage':job.stage,'error':job.error,'content':{'claims':len(content.claims),'required_claims':sum(c.required for c in content.claims),'assets':len(content.assets)},'result':json.loads(job.result)}
    (args.output/'evaluation-result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    raise SystemExit(0 if job.state=='ready' else 1)


if __name__=='__main__':main()
