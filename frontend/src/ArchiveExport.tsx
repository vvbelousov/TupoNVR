import {useEffect,useState} from 'react';
import {api,errorMessage,isUnauthorized} from './api';
import {instantChoice} from './cleanup';
import {localDate,localClock,useTimezone,applicationTimezone} from './time';
import {useLanguage} from './i18n';
import {Feedback} from './ui';
type Progress={token:string;state:string;completed:number;total:number;error:string;gaps:{start:string;end:string}[];mode:string};
export function ArchiveExport({cameras,selected,cursor,onError}:{cameras:{id:number;name:string}[];selected:number[];cursor:number;onError:(error:unknown)=>void}){
  const {t,when}=useLanguage(),zone=useTimezone();
  const [camera,setCamera]=useState(selected[0]||cameras[0]?.id||0),[start,setStart]=useState(''),[end,setEnd]=useState('');
  const [startFold,setStartFold]=useState(''),[endFold,setEndFold]=useState(''),[mode,setMode]=useState('exact');
  const [progress,setProgress]=useState<Progress|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(''),[polling,setPolling]=useState(true);
  useEffect(()=>{if(selected.length===1)setCamera(selected[0])},[selected.join(',')]);
  useEffect(()=>{setStart('');setEnd('');setStartFold('');setEndFold('')},[zone]);
  function fail(value:unknown){setError(value);if(isUnauthorized(value))onError(value)}
  useEffect(()=>{
    if(!progress||progress.state!=='processing'||!polling)return;
    let active=true;const controller=new AbortController();
    const timer=setTimeout(async()=>{try{const value=await api<Progress>(`/api/recordings/exports/${progress.token}`,{signal:controller.signal});if(active)setProgress(value)}catch(e){if(active){fail(e);setPolling(false)}}},750);
    return()=>{active=false;clearTimeout(timer);controller.abort()};
  },[progress,polling]);
  function mark(boundary:'start'|'end'){
    const local=`${localDate(cursor)}T${localClock(cursor)}`;
    if(boundary==='start'){setStart(local);setStartFold('')}else{setEnd(local);setEndFold('')}
    // Keep the absolute timeline instant: repeated wall times remain unambiguous.
    setMarked(current=>({...current,[boundary]:{local,instant:new Date(Math.floor(cursor/1000)*1000).toISOString()}}));
  }
  const [marked,setMarked]=useState<Record<string,{local:string;instant:string}>>({});
  useEffect(()=>setMarked({}),[zone]);
  async function resolve(local:string,fold:string,boundary:string){
    if(marked[boundary]?.local===local)return marked[boundary].instant;
    if(!local)throw new Error('Choose both date and time boundaries.');
    const [date,time]=local.split('T');
    const response=await api<{timezone:string;instants:{time:string}[]}>('/api/time/resolve',{method:'POST',body:JSON.stringify({date,time})});
    if(response.timezone!==zone||applicationTimezone()!==zone)throw new Error('Timezone changed; choose the local time again.');
    return instantChoice(response.instants,fold);
  }
  async function begin(){
    setBusy(true);setError('');setProgress(null);setPolling(true);
    try{
      const a=await resolve(start,startFold,'start'),b=await resolve(end,endFold,'end');
      if(Date.parse(a)>=Date.parse(b))throw new Error('Start must precede end.');
      if(applicationTimezone()!==zone)throw new Error('Timezone changed; choose the local time again.');
      setProgress(await api<Progress>('/api/recordings/exports',{method:'POST',body:JSON.stringify({camera_id:camera,start:a,end:b,mode})}));
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  const running=progress?.state==='processing';
  return <section className="archive-export"><h3>{t('Export interval')}</h3>
    <fieldset disabled={busy||running}><legend>{t('Time range')} · {zone}</legend><div className="toolbar">
      <label>{t('Camera')}<select aria-label={t('Export camera')} value={camera} onChange={e=>setCamera(Number(e.target.value))}>{cameras.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      {(['start','end'] as const).map(boundary=>{const value=boundary==='start'?start:end,fold=boundary==='start'?startFold:endFold;return <label key={boundary}>{t(boundary==='start'?'Start':'End')}<input aria-label={t(boundary==='start'?'Export start':'Export end')} type="datetime-local" step="1" value={value} onChange={e=>{(boundary==='start'?setStart:setEnd)(e.target.value);(boundary==='start'?setStartFold:setEndFold)('');setMarked(current=>({...current,[boundary]:{local:'',instant:''}}))}}/><select aria-label={t(boundary==='start'?'Start occurrence':'End occurrence')} value={fold} onChange={e=>{(boundary==='start'?setStartFold:setEndFold)(e.target.value);setMarked(current=>({...current,[boundary]:{local:'',instant:''}}))}}><option value="">{t('If time repeats, choose occurrence')}</option><option value="earlier">{t('Earlier occurrence')}</option><option value="later">{t('Later occurrence')}</option></select><button onClick={()=>mark(boundary)}>{t(boundary==='start'?'Set start at cursor':'Set end at cursor')}</button></label>})}
      <label>{t('Export mode')}<select value={mode} onChange={e=>setMode(e.target.value)}><option value="exact">{t('Exact boundaries (encode)')}</option><option value="copy">{t('Original quality (keyframe cuts)')}</option></select></label>
      <button className="primary" disabled={!camera||!start||!end} onClick={begin}>{t('Create export')}</button>
    </div></fieldset>
    <p className="help">{t('Gaps are omitted; available footage is joined. Exports contain video only. Exact mode encodes H.264; original quality mode may include footage outside the selected boundaries at keyframes.')}</p>
    {!!error&&<Feedback tone="bad">{errorMessage(error)} {!polling&&<button onClick={()=>{setError('');setPolling(true)}}>{t('Retry')}</button>}</Feedback>}
    {progress&&<Feedback tone={progress.state==='failed'?'bad':'neutral'}>{t(progress.state==='processing'?'Exporting…':progress.state==='ready'?'Export ready':progress.state==='failed'?'Export failed':'Download started')} {progress.completed} / {progress.total}{progress.error&&` · ${t(progress.error)}`}{progress.state==='ready'&&<a className="button-link" href={`/api/recordings/exports/${progress.token}/download`} onClick={()=>setProgress({...progress,state:'downloading'})}>{t('Download MP4')}</a>}{!!progress.gaps.length&&<p>{t('Recording gaps omitted')}: {progress.gaps.map(gap=>`${when(gap.start)} — ${when(gap.end)}`).join('; ')}</p>}</Feedback>}
  </section>;
}
