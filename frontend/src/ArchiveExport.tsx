import {useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {api,errorMessage,isUnauthorized} from './api';
import {instantChoice} from './cleanup';
import {localDate,localClock,useTimezone,applicationTimezone} from './time';
import {useLanguage} from './i18n';
import {Feedback} from './ui';
import {ExportSelection} from './ExportSelection';
import type {ExportInterval} from './ExportSelection';
type CameraProgress={camera_id:number;state:string;error:string;gaps:{start:string;end:string}[];completed:number;total:number;mode:string};
type Progress={token:string;state:string;phase:string;completed:number;total:number;error:string;gaps:{start:string;end:string}[];mode:string;format:string;partial:boolean;cameras:CameraProgress[]};
export function ArchiveExport({cameras,selected,cursor,timeline,start:domainStart,end:domainEnd,onLocate,onError}:{cameras:{id:number;name:string}[];selected:number[];cursor:number;timeline:HTMLElement|null;start:number;end:number;onLocate:(stamp:number)=>void;onError:(error:unknown)=>void}){
  const {t,when}=useLanguage(),zone=useTimezone();
  const [expanded,setExpanded]=useState(false),[ids,setIds]=useState(selected.length?selected:cameras.slice(0,1).map(c=>c.id)),dirty=useRef(false);
  const [start,setStart]=useState(''),[end,setEnd]=useState(''),[startFold,setStartFold]=useState(''),[endFold,setEndFold]=useState(''),[mode,setMode]=useState('exact');
  const [interval,setInterval]=useState<ExportInterval|null>(null),[ambiguous,setAmbiguous]=useState({start:false,end:false});
  const marked=useRef<Record<string,{local:string;instant:string}>>({}),revision=useRef(0);
  const [progress,setProgress]=useState<Progress|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(''),[polling,setPolling]=useState(true);
  useEffect(()=>{if(!dirty.current)setIds(selected.length?selected:cameras.slice(0,1).map(c=>c.id))},[selected.join(','),cameras.map(c=>c.id).join(',')]);
  function fail(value:unknown){setError(value);if(isUnauthorized(value))onError(value)}
  const running=progress?.state==='processing';
  useEffect(()=>{
    if(!running||!progress||!polling)return;
    let active=true;const controller=new AbortController();
    const timer=setTimeout(async()=>{try{const value=await api<Progress>(`/api/recordings/exports/${progress.token}`,{signal:controller.signal});if(active)setProgress(value)}catch(e){if(active){fail(e);setPolling(false)}}},750);
    return()=>{active=false;clearTimeout(timer);controller.abort()};
  },[progress,polling]);
  function choose(value:ExportInterval){
    revision.current++;setInterval(value);setError('');
    for(const boundary of ['start','end'] as const){const stamp=value[boundary],local=`${localDate(stamp)}T${localClock(stamp)}`;marked.current[boundary]={local,instant:new Date(stamp).toISOString()};(boundary==='start'?setStart:setEnd)(local)}
    setStartFold('');setEndFold('');setAmbiguous({start:false,end:false});
  }
  useEffect(()=>{if(interval)choose(interval);else{setStart('');setEnd('');marked.current={}}},[zone]);
  async function resolve(local:string,fold:string,boundary:'start'|'end',version:number){
    if(marked.current[boundary]?.local===local)return marked.current[boundary].instant;
    if(!local)throw new Error('Choose both date and time boundaries.');
    const [date,time]=local.split('T');
    const response=await api<{timezone:string;instants:{time:string}[]}>('/api/time/resolve',{method:'POST',body:JSON.stringify({date,time})});
    if(response.timezone!==zone||applicationTimezone()!==zone)throw new Error('Timezone changed; choose the local time again.');
    if(version===revision.current)setAmbiguous(current=>({...current,[boundary]:response.instants.length>1}));
    return instantChoice(response.instants,fold);
  }
  useEffect(()=>{
    const version=++revision.current;
    if(!start||!end){setInterval(null);return}
    let active=true;
    const timer=setTimeout(async()=>{
      try{const [a,b]=await Promise.all([resolve(start,startFold,'start',version),resolve(end,endFold,'end',version)]);
        if(!active||version!==revision.current)return;
        if(Date.parse(a)>=Date.parse(b))throw new Error('Start must precede end.');
        setInterval({start:Date.parse(a),end:Date.parse(b)});setError('');
      }catch(e){if(active&&version===revision.current){setInterval(null);fail(e)}}
    },150);
    return()=>{active=false;clearTimeout(timer)};
  },[start,end,startFold,endFold,zone]);
  function toggle(){if(!expanded&&!start&&!end){const stamp=Math.floor(cursor/1000)*1000;choose({start:stamp,end:stamp+60000})}setExpanded(!expanded)}
  async function begin(){
    if(busy||running)return;
    setBusy(true);setError('');setProgress(null);setPolling(true);
    const version=revision.current;
    try{
      const [a,b]=await Promise.all([resolve(start,startFold,'start',version),resolve(end,endFold,'end',version)]);
      if(Date.parse(a)>=Date.parse(b))throw new Error('Start must precede end.');
      if(applicationTimezone()!==zone||version!==revision.current)throw new Error('Timezone changed; choose the local time again.');
      const selection=ids.length===1?{camera_id:ids[0]}:{camera_ids:ids};
      setProgress(await api<Progress>('/api/recordings/exports',{method:'POST',body:JSON.stringify({...selection,start:a,end:b,mode})}));
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  const outside=interval&&(interval.start<domainStart||interval.end>domainEnd);
  const phase=progress?.state==='processing'?({preparing:'Preparing export…',processing:'Processing cameras…',packaging:'Packaging ZIP…'}[progress.phase]||'Exporting…'):progress?.state==='ready'?(progress.partial?'Export partially completed':'Export ready'):progress?.state==='failed'?'Export failed':'Download started';
  return <section className="archive-export">
    <button className="export-toggle" aria-expanded={expanded} aria-controls="export-clips-controls" onClick={toggle}>{expanded?'▾':'▸'} {t('Export clips')} {start&&end&&<small>{start.replace('T',' ')} — {end.replace('T',' ')} · {t('Export cameras: {count}',{count:ids.length})}</small>}</button>
    {expanded&&timeline&&interval&&createPortal(<ExportSelection value={interval} start={domainStart} end={domainEnd} disabled={busy||running} onChange={choose}/>,timeline)}
    <div id="export-clips-controls" hidden={!expanded}>{expanded&&<>
      {outside&&<Feedback>{t('Selection extends outside the visible timeline.')} <button onClick={()=>onLocate(interval.start)}>{t('Locate start')}</button> <button onClick={()=>onLocate(interval.end)}>{t('Locate end')}</button></Feedback>}
      <fieldset disabled={busy||running}><legend>{t('Time range')} · {zone}</legend><div className="toolbar">
        {(['start','end'] as const).map(boundary=>{const value=boundary==='start'?start:end,fold=boundary==='start'?startFold:endFold;return <label key={boundary}>{t(boundary==='start'?'Start':'End')}<input aria-label={t(boundary==='start'?'Export start':'Export end')} type="datetime-local" step="1" value={value} onChange={e=>{revision.current++;(boundary==='start'?setStart:setEnd)(e.target.value);(boundary==='start'?setStartFold:setEndFold)('');delete marked.current[boundary];setInterval(null)}}/>{ambiguous[boundary]&&<select aria-label={t(boundary==='start'?'Start occurrence':'End occurrence')} value={fold} onChange={e=>{(boundary==='start'?setStartFold:setEndFold)(e.target.value);delete marked.current[boundary]}}><option value="">{t('If time repeats, choose occurrence')}</option><option value="earlier">{t('Earlier occurrence')}</option><option value="later">{t('Later occurrence')}</option></select>}</label>})}
        <label>{t('Export mode')}<select value={mode} onChange={e=>setMode(e.target.value)}><option value="exact">{t('Exact boundaries (encode)')}</option><option value="copy">{t('Original quality (keyframe cuts)')}</option></select></label>
      </div><div className="export-cameras"><div className="actions"><strong>{t('Export cameras: {count}',{count:ids.length})}</strong><button onClick={()=>{dirty.current=true;setIds(cameras.map(c=>c.id))}}>{t('Select all')}</button><button onClick={()=>{dirty.current=true;setIds([])}}>{t('Clear')}</button></div><div className="camera-choices">{cameras.map(c=><label className="smallcheck" key={c.id}><input type="checkbox" checked={ids.includes(c.id)} onChange={e=>{dirty.current=true;setIds(e.target.checked?[...ids,c.id]:ids.filter(id=>id!==c.id))}}/>{c.name}</label>)}</div></div>
      <button className="primary" disabled={!ids.length||!start||!end} onClick={begin}>{t('Create export')}</button></fieldset>
      <small className="help">{t('Drag the export band or its handles; click recording tracks to seek.')} {t('Gaps are omitted; available footage is joined. Exports contain video only. Exact mode encodes H.264; original quality mode may include footage outside the selected boundaries at keyframes.')}</small>
    </>}</div>
    {!!error&&<Feedback tone="bad">{errorMessage(error)} {!polling&&<button onClick={()=>{setError('');setPolling(true)}}>{t('Retry')}</button>}</Feedback>}
    {progress&&<Feedback tone={progress.state==='failed'?'bad':progress.partial?'warning':'neutral'}>{t(phase)} {progress.completed} / {progress.total}{progress.error&&` · ${t(progress.error)}`}{progress.state==='ready'&&<a className="button-link" href={`/api/recordings/exports/${progress.token}/download`} onClick={()=>setProgress({...progress,state:'downloading'})}>{t(progress.format==='zip'?'Download ZIP':'Download MP4')}</a>}{progress.cameras.map(c=><p key={c.camera_id}>{cameras.find(camera=>camera.id===c.camera_id)?.name||c.camera_id}: {t(c.state==='ready'?'Export ready':c.state==='failed'?'Export failed':c.state==='processing'?'Exporting…':'Preparing export…')} {c.completed} / {c.total} · {t(c.mode==='copy'?'Original quality (keyframe cuts)':'Exact boundaries (encode)')}{c.error&&` · ${t(c.error)}`}{!!c.gaps.length&&<span> · {t('Recording gaps omitted')}: {c.gaps.map(g=>`${when(g.start)} — ${when(g.end)}`).join('; ')}</span>}</p>)}</Feedback>}
  </section>;
}
