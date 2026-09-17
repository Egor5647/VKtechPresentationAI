import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Export={version:number;scene:string;audit:string;pptx:string;html:string;pdf:string;previews:string[];issue_count:number};
type Option={label:string;preview:string;score:number;reasons:Record<string,unknown>};
type Variant='A'|'B'|'C';
type Format='pptx'|'pdf'|'html';
type SlideChoice={id:string;title:string;archetype:string;lead:string;support_points:string[];takeaway:string;selected:Variant;options:Record<Variant,Option>};
type Result={presentation?:Export;variants?:Record<string,Export>;slides?:SlideChoice[];selection?:Record<string,string>;elapsed_seconds?:number;deadline_met?:boolean};
type Job={id:string;state:string;stage:string;error:string;result:Result};
type Template={id:string;name:string;design:{prototypes:unknown[];fonts:string[];warnings:string[]}};
type Content={id:string;name:string;content:{claims:unknown[];datasets:unknown[]}};
type Health={model_configured:boolean;image_model_configured:boolean;renderer_available:boolean};

const stages:Record<string,string>={queued:'В очереди',starting:'Запуск',loading:'Чтение материалов',planning:'Планирование содержания',layout_export_render:'Сборка и экспорт',contextual_audit:'Проверка содержания',finalizing:'Сохранение результата',ready:'Готово',awaiting_input:'Нужны сведения',failed:'Ошибка',cancelled:'Отменено'};
const archetypes:Record<string,string>={cover:'Обложка',divider:'Раздел',explanation:'Объяснение',comparison:'Сравнение',process:'Процесс',example:'Пример',formula:'Формула',exercise:'Задание',summary:'Итог',illustration:'Иллюстрация'};

async function api<T>(url:string,init?:RequestInit):Promise<T>{
  const response=await fetch(url,init);
  if(!response.ok){const body=await response.json().catch(()=>({detail:'Ошибка сервера'}));throw new Error(String(body.detail))}
  return response.json();
}
const fileURL=(job:string,path:string)=>`/api/jobs/${job}/file?path=${encodeURIComponent(path)}`;

function App(){
  const [templates,setTemplates]=useState<Template[]>([]),[contents,setContents]=useState<Content[]>([]);
  const [template,setTemplate]=useState(''),[content,setContent]=useState(''),[brief,setBrief]=useState(''),[count,setCount]=useState(12),[purpose,setPurpose]=useState('project'),[images,setImages]=useState(true);
  const [job,setJob]=useState<Job|null>(null),[slide,setSlide]=useState(0),[instruction,setInstruction]=useState('Сделай слайд выразительнее и плотнее, сохрани все факты.');
  const [draftSelection,setDraftSelection]=useState<Record<string,Variant>>({}),[exportTask,setExportTask]=useState<{id:string;format:Format}|null>(null);
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
  async function download(format:Format){
    if(!job||!presentation)return;setError('');
    const selection=Object.fromEntries(choices.map(choice=>[choice.id,draftSelection[choice.id]||choice.selected])) as Record<string,Variant>;
    const changed=choices.some(choice=>selection[choice.id]!==choice.selected);
    if(!changed){const link=document.createElement('a');link.href=fileURL(job.id,presentation[format]);link.download=`presentation.${format}`;link.click();return}
    try{const created=await api<{id:string}>(`/api/jobs/${job.id}/export`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selection})});setExportTask({id:created.id,format})}catch(e){setError((e as Error).message)}
  }
  const completeSelection=()=>Object.fromEntries(choices.map(choice=>[choice.id,draftSelection[choice.id]||choice.selected])) as Record<string,Variant>;

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
      </aside>
      <section className="workspace">
        {error&&<div role="alert" className="error">{error}<button onClick={()=>setError('')}>×</button></div>}
        {!job&&<div className="empty"><div className="empty-card"><div className="mock-title"/><div className="mock-line"/><div className="mock-line short"/><div className="mock-bars"><i/><i/><i/></div></div><h2>Здесь появится презентация</h2></div>}
        {job&&<>
          <div className="status"><span className={'dot '+job.state}/><b>{stages[job.stage]||job.stage}</b>{['queued','running'].includes(job.state)&&<button onClick={()=>api<Job>(`/api/jobs/${job.id}/cancel`,{method:'POST'}).then(setJob).catch(e=>setError(e.message))}>Отменить</button>}</div>
          {job.error&&<div className="error">{job.error}</div>}{['failed','awaiting_input'].includes(job.state)&&<button onClick={()=>api<Job>(`/api/jobs/${job.id}/retry`,{method:'POST'}).then(setJob).catch(e=>setError(e.message))}>Повторить после устранения причины</button>}
          {legacyReady&&<div className="notice-card">Этот результат создан старой версией сервиса. Нажмите «Создать презентацию», чтобы получить выбор вариантов для каждого слайда.</div>}
          {job.state==='ready'&&presentation&&currentChoice&&<>
            <div className="result-head"><div className="exports">{(['pptx','pdf','html'] as const).map(format=><button key={format} disabled={Boolean(exportTask)} onClick={()=>download(format)}>{exportTask?.format===format?'Сборка…':format.toUpperCase()+' ↓'}</button>)}</div></div>
            <div className="review">
              <div className="viewer">
                <div className="slide-preview"><img src={fileURL(job.id,currentChoice.options[selectedFor(currentChoice)].preview)} alt={currentChoice.title}/></div>
                <div className="pagination"><button disabled={slide===0} onClick={()=>setSlide(slide-1)}>←</button><span>{slide+1} / {choices.length} · {currentChoice.title}</span><button disabled={slide+1===choices.length} onClick={()=>setSlide(slide+1)}>→</button></div>
                <div className="deck-thumbs">{choices.map((choice,index)=>{const selected=selectedFor(choice);const preview=choice.options[selected].preview;return <button key={choice.id} className={slide===index?'active':''} onClick={()=>setSlide(index)}><img src={fileURL(job.id,preview)} alt={`Слайд ${index+1}`}/><span>{index+1}</span><i>{selected}</i></button>})}</div>
              </div>
              <aside className="slide-editor">
                <div className="eyebrow">СЛАЙД {slide+1} · {archetypes[currentChoice.archetype]||currentChoice.archetype}</div><h2>{currentChoice.title}</h2><p className="lead">{currentChoice.lead}</p>
                {currentChoice.support_points.length>0&&<ul>{currentChoice.support_points.map(point=><li key={point}>{point}</li>)}</ul>}{currentChoice.takeaway&&<p className="takeaway">{currentChoice.takeaway}</p>}
                <div className="choice-title"><b>Выберите компоновку</b></div>
                <div className="slide-options">{(['A','B','C'] as const).map(id=>{const option=currentChoice.options[id];return <button key={id} className={selectedFor(currentChoice)===id?'active':''} disabled={busy||Boolean(exportTask)} onClick={()=>setDraftSelection(previous=>({...previous,[currentChoice.id]:id}))}><img src={fileURL(job.id,option.preview)} alt={option.label}/><span><b>{id} · {option.label}</b><small>оценка {Math.round(option.score)}/100</small></span></button>})}</div>
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
