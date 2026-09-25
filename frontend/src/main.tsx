import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Export={version:number;scene:string;audit:string;pptx:string;html:string;pdf:string;previews:string[];issue_count:number};
type Option={label:string;preview:string;score:number;reasons:Record<string,unknown>};
type Variant='A'|'B'|'C';
type Format='pptx'|'pdf'|'html';
type Palette={background:string;surface:string;accent:string;accent_secondary:string;text_primary:string;text_on_accent:string};
type ImageCandidate={asset_id:string;path:string;score:number;semantic_fit:number;naturalness:number;composition:number;accepted:boolean;reason:string;selected:boolean};
type VisualContract={goal:string;entities:string[];relations:string[];forbidden:string[]};
type SlideLogicContract={teaching_goal:string;question_answered:string;main_assertion:string;semantic_payload_hash:string};
type LogicFinding={rule:string;severity:'error'|'warning'|'info';message:string;repair_level:string};
type SlideChoice={id:string;title:string;archetype:string;lead:string;support_points:string[];takeaway:string;visual_strategy?:string;visual_score?:number;visual_reason?:string;visual_contract?:VisualContract;logic_contract?:SlideLogicContract;logic_status?:'ok'|'review';logic_findings?:LogicFinding[];image_candidates?:ImageCandidate[];selected:Variant;options:Record<Variant,Option>};
type Result={request?:{template_id:string;content_id:string};presentation?:Export;variants?:Record<string,Export>;slides?:SlideChoice[];selection?:Record<string,string>;logic_findings?:LogicFinding[];palette?:Palette;source_palette?:Palette;elapsed_seconds?:number;deadline_met?:boolean};
type Job={id:string;state:string;stage:string;error:string;result:Result};
type Template={id:string;name:string;design:{prototypes:unknown[];fonts:string[];warnings:string[]}};
type Content={id:string;name:string;content:{claims:unknown[];datasets:unknown[]}};
type Health={model_configured:boolean;image_model_configured:boolean;renderer_available:boolean};

const stages:Record<string,string>={queued:'В очереди',starting:'Запуск',loading:'Чтение материалов',planning:'Планирование содержания',layout_export_render:'Сборка и экспорт',contextual_audit:'Проверка содержания',finalizing:'Сохранение результата',ready:'Готово',awaiting_input:'Нужны сведения',failed:'Ошибка',cancelled:'Отменено'};
const archetypes:Record<string,string>={cover:'Обложка',divider:'Раздел',explanation:'Объяснение',comparison:'Сравнение',process:'Процесс',example:'Пример',formula:'Формула',exercise:'Задание',summary:'Итог',illustration:'Иллюстрация'};
const variantNames:Record<Variant,string>={A:'Крупно и кратко',B:'Сбалансированно',C:'Подробно'};
const fallbackPalette:Palette={background:'FFFFFF',surface:'E8EEF6',accent:'0077FF',accent_secondary:'31C48D',text_primary:'172438',text_on_accent:'FFFFFF'};
const greenPalette:Palette={background:'F4F7F4',surface:'DCE9E1',accent:'0B5D3B',accent_secondary:'4D8A68',text_primary:'14251D',text_on_accent:'FFFFFF'};
const graphitePalette:Palette={background:'F5F6F7',surface:'E3E6E8',accent:'34464F',accent_secondary:'7A8F86',text_primary:'182126',text_on_accent:'FFFFFF'};
const paletteFields:[keyof Palette,string][]=[['accent','Акцент'],['accent_secondary','Дополнительный'],['surface','Карточки'],['background','Фон']];

async function api<T>(url:string,init?:RequestInit):Promise<T>{
  const response=await fetch(url,init);
  if(!response.ok){const body=await response.json().catch(()=>({detail:'Ошибка сервера'}));throw new Error(String(body.detail))}
  return response.json();
}
const fileURL=(job:string,path:string)=>`/api/jobs/${job}/file?path=${encodeURIComponent(path)}`;
const withHash=(value:string)=>'#'+value.replace('#','');
const withoutHash=(value:string)=>value.replace('#','').toUpperCase();
const normalizePalette=(value?:Partial<Palette>):Palette=>({background:value?.background||fallbackPalette.background,surface:value?.surface||fallbackPalette.surface,accent:value?.accent||fallbackPalette.accent,accent_secondary:value?.accent_secondary||fallbackPalette.accent_secondary,text_primary:value?.text_primary||fallbackPalette.text_primary,text_on_accent:value?.text_on_accent||fallbackPalette.text_on_accent});
function hue(value:string){const hex=withoutHash(value),r=parseInt(hex.slice(0,2),16)/255,g=parseInt(hex.slice(2,4),16)/255,b=parseInt(hex.slice(4,6),16)/255,max=Math.max(r,g,b),min=Math.min(r,g,b),d=max-min;if(!d)return 0;let h=max===r?((g-b)/d)%6:max===g?(b-r)/d+2:(r-g)/d+4;return (h*60+360)%360}

function App(){
  const [templates,setTemplates]=useState<Template[]>([]),[contents,setContents]=useState<Content[]>([]);
  const [template,setTemplate]=useState(''),[content,setContent]=useState(''),[brief,setBrief]=useState(''),[count,setCount]=useState(12),[purpose,setPurpose]=useState('project'),[images,setImages]=useState(true);
  const [job,setJob]=useState<Job|null>(null),[slide,setSlide]=useState(0),[instruction,setInstruction]=useState('Сделай слайд выразительнее и плотнее, сохрани все факты.'),[visualInstruction,setVisualInstruction]=useState('Покажи главный смысл без текста и лишних объектов.');
  const [draftSelection,setDraftSelection]=useState<Record<string,Variant>>({}),[exportTask,setExportTask]=useState<{id:string;format:Format}|null>(null);
  const [palette,setPalette]=useState<Palette>(fallbackPalette),[appliedPalette,setAppliedPalette]=useState<Palette>(fallbackPalette),[sourcePalette,setSourcePalette]=useState<Palette>(fallbackPalette);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[health,setHealth]=useState<Health|null>(null);

  async function refresh(){
    const [ts,cs]=await Promise.all([api<Template[]>('/api/templates'),api<Content[]>('/api/content')]);
    setTemplates(ts);setContents(cs);setTemplate(v=>v||ts[0]?.id||'');setContent(v=>v||cs[0]?.id||'');
  }
  useEffect(()=>{
    refresh().catch(e=>setError(e.message));
    api<Health>('/api/health').then(h=>{setHealth(h);if(!h.image_model_configured)setImages(false)}).catch(e=>setError(e.message));
    const id=new URLSearchParams(window.location.search).get('job')||localStorage.getItem('vktech-job');if(id)api<Job>('/api/jobs/'+id).then(setJob).catch(()=>localStorage.removeItem('vktech-job'));
  },[]);
  useEffect(()=>{
    if(!job||!['queued','running'].includes(job.state))return;
    const timer=setInterval(()=>api<Job>('/api/jobs/'+job.id).then(next=>{setJob(next);if(!['queued','running'].includes(next.state))setBusy(false)}).catch(e=>{setError(e.message);setBusy(false)}),1500);
    return()=>clearInterval(timer);
  },[job?.id,job?.state]);
  useEffect(()=>{
    if(!job)return;localStorage.setItem('vktech-job',job.id);
    if(job.state==='ready'&&job.result.selection){
      setDraftSelection(job.result.selection as Record<string,Variant>);
      if(job.result.request?.template_id)setTemplate(job.result.request.template_id);
      if(job.result.request?.content_id)setContent(job.result.request.content_id);
      const next=normalizePalette(job.result.palette||job.result.source_palette);setPalette(next);setAppliedPalette(next);setSourcePalette(normalizePalette(job.result.source_palette||next));
      setSlide(current=>Math.min(current,(job.result.slides?.length||1)-1));
      const url=new URL(window.location.href);url.searchParams.set('job',job.id);window.history.replaceState({},'',url);
    }
  },[job?.id,job?.state]);
  useEffect(()=>{
    if(!exportTask)return;let active=true;let timer=0;
    const poll=async()=>{try{const next=await api<Job>('/api/jobs/'+exportTask.id);if(!active)return;if(next.state==='ready'&&next.result.presentation){setJob(next);const path=next.result.presentation[exportTask.format];const link=document.createElement('a');link.href=fileURL(next.id,path);link.download=`presentation.${exportTask.format}`;link.click();setExportTask(null)}else if(['failed','awaiting_input','cancelled'].includes(next.state)){setError(next.error||'Не удалось собрать файл');setExportTask(null)}else timer=window.setTimeout(poll,1200)}catch(e){if(active){setError((e as Error).message);setExportTask(null)}}};poll();return()=>{active=false;window.clearTimeout(timer)};
  },[exportTask?.id]);

  async function upload(kind:'templates'|'content',file:File){
    setBusy(true);setError('');try{const body=new FormData();body.append('file',file);const result=await api<{id:string}>('/api/'+kind,{method:'POST',body});await refresh();kind==='templates'?setTemplate(result.id):setContent(result.id)}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function start(){
    setBusy(true);setError('');setSlide(0);
    try{const created=await api<{id:string}>('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template_id:template,content_id:content,brief,purpose,slide_count:count,generate_images:images})});setJob(await api<Job>('/api/jobs/'+created.id))}catch(e){setError((e as Error).message);setBusy(false)}
  }
  async function beginChild(path:string,body:unknown){
    if(!job)return;setBusy(true);setError('');
    try{const created=await api<{id:string}>(`/api/jobs/${job.id}/${path}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});setJob(await api<Job>('/api/jobs/'+created.id))}catch(e){setError((e as Error).message);setBusy(false)}
  }
  async function recompose(){
    if(!job)return;setBusy(true);setError('');
    try{const created=await api<{id:string}>(`/api/jobs/${job.id}/recompose`,{method:'POST'});setJob(await api<Job>('/api/jobs/'+created.id))}catch(e){setError((e as Error).message);setBusy(false)}
  }
  async function download(format:Format){
    if(!job||!presentation)return;setError('');
    const selection=Object.fromEntries(choices.map(choice=>[choice.id,draftSelection[choice.id]||choice.selected])) as Record<string,Variant>;
    const changed=choices.some(choice=>selection[choice.id]!==choice.selected);
    if(!changed){const link=document.createElement('a');link.href=fileURL(job.id,presentation[format]);link.download=`presentation.${format}`;link.click();return}
    try{const created=await api<{id:string}>(`/api/jobs/${job.id}/export`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selection})});setExportTask({id:created.id,format})}catch(e){setError((e as Error).message)}
  }
  const completeSelection=()=>Object.fromEntries(choices.map(choice=>[choice.id,draftSelection[choice.id]||choice.selected])) as Record<string,Variant>;
  const paletteDirty=JSON.stringify(palette)!==JSON.stringify(appliedPalette);
  const previewFilter=paletteDirty?`hue-rotate(${Math.round(hue(palette.accent)-hue(appliedPalette.accent))}deg) saturate(92%)`:undefined;
  const choosePalette=(next:Palette)=>setPalette({...next});
  const applyPalette=()=>beginChild('palette',{palette,selection:completeSelection()});

  const result=job?.result;
  const presentation=result?.presentation;
  const choices=result?.slides||[];
  const currentChoice=choices[slide];
  const legacyReady=job?.state==='ready'&&!presentation;
  const selectedFor=(choice:SlideChoice):Variant=>draftSelection[choice.id]||choice.selected;

  return <div className="app">
    <header><div className="logo">VK</div><div><b>Презентации</b></div></header>
    <main>
      <aside className="setup">
        <div className="eyebrow">НОВАЯ ПРЕЗЕНТАЦИЯ</div><h1>От материалов<br/>к готовым слайдам</h1>
        <label>Шаблон PPTX<select value={template} onChange={e=>setTemplate(e.target.value)}><option value="">Выберите шаблон</option>{templates.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
        <label className="upload">＋ Загрузить шаблон<input type="file" accept=".pptx" disabled={busy} onChange={e=>{if(e.target.files?.[0])upload('templates',e.target.files[0]);e.target.value=''}}/></label>
        <label>Исходные материалы<select value={content} onChange={e=>setContent(e.target.value)}><option value="">Выберите материалы</option>{contents.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label className="upload">＋ Загрузить материалы<input type="file" accept=".json,.txt,.md,.pptx,.pdf,.csv" disabled={busy} onChange={e=>{if(e.target.files?.[0])upload('content',e.target.files[0]);e.target.value=''}}/></label>
        <label>Что должна объяснить презентация?<textarea value={brief} onChange={e=>setBrief(e.target.value)} placeholder="Например: объяснить пользу продукта руководителям и предложить следующий шаг" rows={4}/></label>
        <div className="row"><label>Назначение<select value={purpose} onChange={e=>setPurpose(e.target.value)}><option value="project">Проект</option><option value="product">Продукт</option><option value="feature">Новая функция</option><option value="initiative">Инициатива</option></select></label><label>Слайды<input type="number" min={1} max={50} value={count} onChange={e=>setCount(Number(e.target.value))}/></label></div>
        <label className="checkbox"><input type="checkbox" checked={images} disabled={!health?.image_model_configured} onChange={e=>setImages(e.target.checked)}/>Создать иллюстрации</label>
        <button className="primary" onClick={start} disabled={busy||!template||!content||!brief.trim()||['queued','running'].includes(job?.state||'')}>Создать презентацию</button>
        {health&&!health.model_configured&&<p className="notice">Для генерации нужно запустить текстовую модель.</p>}{health&&!health.image_model_configured&&<p className="notice">Генератор новых иллюстраций сейчас недоступен.</p>}{health&&!health.renderer_available&&<p className="notice">Для PDF и превью нужен LibreOffice.</p>}
        {job?.state==='ready'&&presentation&&<section className="palette-panel">
          <div className="palette-title"><b>Цветовая гамма</b><span className={paletteDirty?'changed':''}>{paletteDirty?'Изменена':'Применена'}</span></div>
          <div className="palette-presets">
            {([[sourcePalette,'Исходная'],[greenPalette,'Тёмно-зелёная'],[graphitePalette,'Графитовая']] as [Palette,string][]).map(([item,label])=><button key={label} title={label} aria-label={label} className={JSON.stringify(palette)===JSON.stringify(item)?'active':''} onClick={()=>choosePalette(item)}><i style={{background:withHash(item.accent)}}/><i style={{background:withHash(item.accent_secondary)}}/><i style={{background:withHash(item.surface)}}/></button>)}
          </div>
          <div className="palette-fields">{paletteFields.map(([field,label])=><label key={field}><span>{label}</span><input type="color" value={withHash(palette[field])} onChange={e=>setPalette(previous=>({...previous,[field]:withoutHash(e.target.value)}))}/></label>)}</div>
          <button className="palette-apply" disabled={!paletteDirty||busy||Boolean(exportTask)} onClick={applyPalette}>{busy?'Применение…':'Применить к презентации'}</button>
        </section>}
      </aside>
      <section className="workspace">
        {error&&<div role="alert" className="error">{error}<button onClick={()=>setError('')}>×</button></div>}
        {!job&&<div className="empty"><div className="empty-card"><div className="mock-title"/><div className="mock-line"/><div className="mock-line short"/><div className="mock-bars"><i/><i/><i/></div></div><h2>Здесь появится презентация</h2></div>}
        {job&&<>
          <div className="status"><span className={'dot '+job.state}/><b>{stages[job.stage]||job.stage}</b>{['queued','running'].includes(job.state)&&<button onClick={()=>api<Job>(`/api/jobs/${job.id}/cancel`,{method:'POST'}).then(setJob).catch(e=>setError(e.message))}>Отменить</button>}</div>
          {job.error&&<div className="error">{job.error}</div>}{['failed','awaiting_input'].includes(job.state)&&<button onClick={()=>api<Job>(`/api/jobs/${job.id}/retry`,{method:'POST'}).then(setJob).catch(e=>setError(e.message))}>Повторить после устранения причины</button>}
          {legacyReady&&<div className="notice-card">Этот результат создан старой версией сервиса. Нажмите «Создать презентацию», чтобы получить выбор вариантов для каждого слайда.</div>}
          {job.state==='ready'&&presentation&&currentChoice&&<>
            <div className="result-head"><div className="exports">{(['pptx','pdf','html'] as const).map(format=><button key={format} disabled={Boolean(exportTask)||paletteDirty} onClick={()=>download(format)}>{exportTask?.format===format?'Сборка…':format.toUpperCase()+' ↓'}</button>)}</div></div>
            <div className="review">
              <div className="viewer">
                <div className="slide-preview"><img style={{filter:previewFilter}} src={fileURL(job.id,currentChoice.options[selectedFor(currentChoice)].preview)} alt={currentChoice.title}/></div>
                <div className="pagination"><button disabled={slide===0} onClick={()=>setSlide(slide-1)}>←</button><span>{slide+1} / {choices.length} · {currentChoice.title}</span><button disabled={slide+1===choices.length} onClick={()=>setSlide(slide+1)}>→</button></div>
                <div className="deck-thumbs">{choices.map((choice,index)=>{const selected=selectedFor(choice);const preview=choice.options[selected].preview;return <button key={choice.id} className={slide===index?'active':''} onClick={()=>setSlide(index)}><img style={{filter:previewFilter}} src={fileURL(job.id,preview)} alt={`Слайд ${index+1}`}/><span>{index+1}</span><i>{selected}</i>{choice.logic_status==='review'&&<em title="Требуется проверка логики">!</em>}</button>})}</div>
              </div>
              <aside className="slide-editor">
                <div className="eyebrow">СЛАЙД {slide+1} · {archetypes[currentChoice.archetype]||currentChoice.archetype}</div><h2>{currentChoice.title}</h2><p className="lead">{currentChoice.lead}</p>
                {currentChoice.support_points.length>0&&<ul>{currentChoice.support_points.map(point=><li key={point}>{point}</li>)}</ul>}{currentChoice.takeaway&&<p className="takeaway">{currentChoice.takeaway}</p>}
                {currentChoice.logic_contract&&<section className={'logic-card '+(currentChoice.logic_status==='review'?'review-needed':'')}><div><b>{currentChoice.logic_status==='review'?'Требуется проверка':'Логика проверена'}</b></div><p>{currentChoice.logic_contract.teaching_goal}</p>{(currentChoice.logic_findings||[]).map(finding=><small key={finding.rule}>{finding.message}</small>)}<div className="logic-actions"><button disabled={busy} onClick={()=>beginChild('regenerate-slide',{slide_id:currentChoice.id,instruction:'Перепиши текст как один точный законченный тезис. Сохрани все факты, числа и связи с соседними слайдами.',selection:completeSelection()})}>Переписать текст</button><button disabled={busy} onClick={()=>beginChild('visual',{slide_id:currentChoice.id,mode:'auto',instruction:'Покажи главный тезис через обязательные сущности и связи без декоративных объектов.',selection:completeSelection()})}>Заменить визуал</button><button disabled={busy} onClick={recompose}>Перестроить раздел</button></div></section>}
                <div className="choice-title"><b>Выберите компоновку</b></div>
                <div className="slide-options">{(['A','B','C'] as const).map(id=>{const option=currentChoice.options[id];return <button key={id} className={selectedFor(currentChoice)===id?'active':''} disabled={busy||Boolean(exportTask)} onClick={()=>setDraftSelection(previous=>({...previous,[currentChoice.id]:id}))}><img src={fileURL(job.id,option.preview)} alt={variantNames[id]}/><span><b>{id} · {variantNames[id]}</b><small>оценка {Math.round(option.score)}/100</small></span></button>})}</div>
                <div className="visual-editor">
                  <div className="choice-title"><b>Визуал</b></div>
                  <div className="visual-modes">
                    <button disabled={busy} onClick={()=>beginChild('visual',{slide_id:currentChoice.id,mode:'auto',instruction:visualInstruction,selection:completeSelection()})}>Авто</button>
                    <button className={currentChoice.visual_strategy==='none'?'active':''} disabled={busy} onClick={()=>beginChild('visual',{slide_id:currentChoice.id,mode:'none',selection:completeSelection()})}>Без визуала</button>
                    <button className={currentChoice.visual_strategy==='diagram'?'active':''} disabled={busy} onClick={()=>beginChild('visual',{slide_id:currentChoice.id,mode:'diagram',selection:completeSelection()})}>Схема</button>
                    <button className={currentChoice.visual_strategy==='generated_image'?'active':''} disabled={busy||!health?.image_model_configured} onClick={()=>beginChild('visual',{slide_id:currentChoice.id,mode:'image',instruction:visualInstruction,selection:completeSelection()})}>Иллюстрация</button>
                  </div>
                  {(currentChoice.image_candidates?.length||0)>0&&<div className="image-candidates">{currentChoice.image_candidates!.map((candidate,index)=><button key={candidate.asset_id} className={candidate.selected?'active':''} title={candidate.reason} disabled={busy} onClick={()=>beginChild('visual',{slide_id:currentChoice.id,mode:'image',candidate_asset_id:candidate.asset_id,selection:completeSelection()})}><img src={fileURL(job.id,candidate.path)} alt={`Вариант иллюстрации ${index+1}`}/><span>{Math.round(candidate.score)}</span></button>)}</div>}
                  <textarea rows={2} value={visualInstruction} onChange={e=>setVisualInstruction(e.target.value)}/>
                </div>
                <div className="regenerate"><label>Что изменить?<textarea rows={3} value={instruction} onChange={e=>setInstruction(e.target.value)}/></label><button className="primary" disabled={busy||Boolean(exportTask)||!instruction.trim()} onClick={()=>beginChild('regenerate-slide',{slide_id:currentChoice.id,instruction,selection:completeSelection()})}>Перегенерировать этот слайд</button></div>
              </aside>
            </div>
          </>}
        </>}
      </section>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
