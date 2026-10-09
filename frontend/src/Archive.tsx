import {useEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import {api,errorMessage,isUnauthorized} from './api';
import {EmptyState,Feedback} from './ui';
import {useLanguage} from './i18n';
import {localClock,localDate,useTimezone,setApplicationTimezone,applicationTimezone,serverNow} from './time';
import {VideoContainer} from './VideoContainer';
import {ArchiveClock,synchronize} from './archiveClock';
import {ArchiveExport} from './ArchiveExport';
import {archiveTarget} from './navigation';
import {recordingChangeEvent} from './cleanup';
import type {Segment} from './archiveClock';

type Camera={id:number;name:string};
type Interval={start:string;end:string};
type Timeline={intervals:Interval[];gaps:Interval[]};
type Day={timezone:string;start:string;end:string;ticks:{time:string;local:string}[]};
type Choice={segment:Segment|null;next_segment:Segment|null;seek_seconds:number;error?:string;retryAt?:number};
type Resolution={time:string;cameras:Record<string,Choice>};
type Instant={time:string;local:string};

export function Archive({cameras,onError}:{cameras:Camera[];onError:(message:unknown)=>void}){
  const {t,when}=useLanguage(),zone=useTimezone();
  const [initialTarget]=useState(()=>archiveTarget(location.search));
  const initialOpened=useRef(false);
  const [ids,setIds]=useState<number[]>(()=>initialTarget?[initialTarget.id]:[]),[date,setDate]=useState(()=>localDate(initialTarget?.stamp)),[time,setTime]=useState(()=>initialTarget?localClock(initialTarget.stamp):'12:00:00');
  const [timelineHost,setTimelineHost]=useState<HTMLDivElement|null>(null);
  const [minutes,setMinutes]=useState(0),[rangeAnchor,setRangeAnchor]=useState(()=>initialTarget?.stamp??serverNow());
  const [day,setDay]=useState<Day|null>(null),[tracks,setTracks]=useState<Record<string,Timeline>>({}),[segments,setSegments]=useState<Segment[]>([]),[more,setMore]=useState(false);
  const [slots,setSlots]=useState<Record<number,Choice>>({}),[stamp,setStamp]=useState<number|null>(null),[preview,setPreview]=useState<number|null>(null);
  const [playing,setPlaying]=useState(false),[preparing,setPreparing]=useState(false),[seeking,setSeeking]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState<unknown>(''),[retry,setRetry]=useState<'load'|'more'|'local'|'next'|'previous'>('load'),[notice,setNotice]=useState(''),[gap,setGap]=useState<{seconds:number;time:string}|null>(null);
  const [speed,setSpeed]=useState(1),[automatic,setAutomatic]=useState(true),[instants,setInstants]=useState<Instant[]>([]),[occurrence,setOccurrence]=useState('');
  const clock=useRef(new ArchiveClock()),slotRef=useRef(slots),players=useRef(new Map<number,{video:HTMLVideoElement;segment:number}>());
  const lookups=useRef(new Set<AbortController>());
  const generation=useRef(0),metadataRequest=useRef(0),resolving=useRef(new Set<number>()),session=useRef(false),preparation=useRef<number|null>(null);
  const idsKey=ids.join(','),cameraKey=cameras.map(c=>c.id).join(',');
  const start=day?Date.parse(day.start):0,end=day?Date.parse(day.end):0;
  const timelineStart=minutes?rangeAnchor-minutes*60000:start,timelineEnd=minutes?rangeAnchor:end;
  function updateSlots(value:Record<number,Choice>){slotRef.current=value;setSlots(value)}
  function stop(){clock.current.pause();setPlaying(false);preparation.current=null;setPreparing(false);for(const {video} of players.current.values())video.pause()}
  function cancelLookups(){for(const controller of lookups.current)controller.abort();lookups.current.clear();resolving.current.clear()}
  function reset(){generation.current++;cancelLookups();stop();session.current=false;updateSlots({});setStamp(null);setPreview(null);setSeeking(false);setNotice('');setGap(null)}
  function select(values:number[]){const current=session.current?clock.current.position():null;reset();setIds(values);if(current!==null){setDate(localDate(current));setTime(localClock(current))}setInstants([]);setOccurrence('')}
  function fail(value:unknown,action:typeof retry='load'){setRetry(action);setError(value);if(isUnauthorized(value))onError(value)}
  function sync(id:number,force=false){
    const slot=slotRef.current[id],player=players.current.get(id),version=generation.current;
    if(slot?.segment&&player&&player.segment===slot.segment.id&&!slot.error)synchronize(player.video,slot.segment,clock.current.position(),clock.current.running,clock.current.speed,force,()=>{
      if(version!==generation.current||!clock.current.running||slotRef.current[id]?.segment?.id!==player.segment||players.current.get(id)?.video!==player.video)return;
      updateSlots({...slotRef.current,[id]:{...slotRef.current[id],error:'Playback blocked. Pause and press Play to retry.'}});
    });
  }
  async function load(append=false){
    if(!date){metadataRequest.current++;setLoading(false);setDay(null);setSegments([]);setTracks({});setMore(false);setError('');return}
    const request=++metadataRequest.current;setLoading(true);setError('');
    try{
      const localDay=await api<Day>(`/api/time/day?date=${date}`);
      const range=minutes?{...localDay,start:new Date(rangeAnchor-minutes*60000).toISOString(),end:new Date(rangeAnchor).toISOString(),ticks:Array.from({length:5},(_,i)=>({time:new Date(rangeAnchor-minutes*60000+i*minutes*15000).toISOString(),local:''}))}:localDay;
      if(request!==metadataRequest.current)return;setDay(range);
      const filter=ids.length?`&camera_ids=${ids.join(',')}`:'';
      const [batch,timeline]=await Promise.all([
        api<Segment[]>(`/api/recordings?start=${encodeURIComponent(range.start)}&end=${encodeURIComponent(range.end)}${filter}&limit=200&offset=${append?segments.length:0}`),
        ids.length&&!append?api<{cameras:Record<string,Timeline>}>('/api/recordings/timelines',{method:'POST',body:JSON.stringify({camera_ids:ids,start:range.start,end:range.end})}):Promise.resolve(null)
      ]);
      if(request!==metadataRequest.current)return;
      setSegments(current=>append?[...current,...batch]:batch);setMore(batch.length===200);if(timeline)setTracks(timeline.cameras);else if(!ids.length)setTracks({});
    }catch(e){if(request===metadataRequest.current)fail(e,append?'more':'load')}finally{if(request===metadataRequest.current)setLoading(false)}
  }
  useEffect(()=>{setSegments([]);setTracks({});setMore(false);setDay(null);load();return()=>{metadataRequest.current++}},[date,idsKey,cameraKey,zone,minutes,rangeAnchor]);
  useEffect(()=>{const changed=()=>{reset();load();setNotice('Recordings changed. Archive refreshed.')};const storageChanged=(event:StorageEvent)=>{if(event.key===recordingChangeEvent)changed()};window.addEventListener(recordingChangeEvent,changed);window.addEventListener('storage',storageChanged);return()=>{window.removeEventListener(recordingChangeEvent,changed);window.removeEventListener('storage',storageChanged)}},[date,idsKey,cameraKey,zone,minutes,rangeAnchor]);
  useEffect(()=>{const available=new Set(cameras.map(c=>c.id));if(cameras.length&&ids.some(id=>!available.has(id)))select(ids.filter(id=>available.has(id)))},[cameraKey]);
  useEffect(()=>{const value=session.current?clock.current.position():serverNow();setDate(localDate(initialTarget&&!session.current?initialTarget.stamp:value,zone));if(session.current||initialTarget)setTime(localClock(initialTarget&&!session.current?initialTarget.stamp:value,zone));setInstants([]);setOccurrence('')},[zone]);
  useEffect(()=>()=>{generation.current++;cancelLookups();clock.current.pause();for(const {video} of players.current.values())video.pause()},[]);
  useEffect(()=>{if(initialTarget&&cameras.some(camera=>camera.id===initialTarget.id)&&!initialOpened.current){initialOpened.current=true;setMinutes(15);setRangeAnchor(initialTarget.stamp+5*60000);jump(initialTarget.stamp)}},[cameraKey]);
  async function resolve(values:number[],target:number,version:number){
    values.forEach(id=>resolving.current.add(id));
    const controller=new AbortController();lookups.current.add(controller);const timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await api<Resolution>('/api/recordings/resolve',{method:'POST',body:JSON.stringify({camera_ids:values,time:new Date(target).toISOString()}),signal:controller.signal});
      if(version!==generation.current)return false;
      const updated={...slotRef.current};for(const id of values)updated[id]=response.cameras[id]||{segment:null,next_segment:null,seek_seconds:0};updateSlots(updated);if(values.some(id=>updated[id]?.segment))setNotice(current=>current==='No recording at the selected time. Gaps are marked on the timeline.'?'':current);return true;
    }catch(e){if(version===generation.current){const updated={...slotRef.current};for(const id of values)updated[id]={...updated[id],segment:null,next_segment:null,seek_seconds:0,error:'Recording lookup failed; retrying…',retryAt:performance.now()+5000};updateSlots(updated);if(isUnauthorized(e))onError(e)}return false}
    finally{clearTimeout(timeout);lookups.current.delete(controller);if(version===generation.current)values.forEach(id=>resolving.current.delete(id))}
  }
  function refresh(){load();if(session.current){const available=ids.filter(id=>!resolving.current.has(id));if(available.length){setNotice('');resolve(available,clock.current.position(),generation.current)}}}
  async function jump(target:number,autoplay=true){
    if(!ids.length)return;const version=++generation.current;cancelLookups();stop();setSeeking(true);setError('');setNotice('');setGap(null);setPreview(null);clock.current.seek(target);setStamp(target);session.current=true;updateSlots({});
    const ok=await resolve(ids,target,version);if(version!==generation.current)return;
    setSeeking(false);if(ok&&autoplay&&ids.some(id=>slotRef.current[id]?.segment||slotRef.current[id]?.next_segment)){preparation.current=performance.now()+5000;setPreparing(true)}
    if(ok&&!ids.some(id=>slotRef.current[id]?.segment))setNotice('No recording at the selected time. Gaps are marked on the timeline.');
  }
  async function localJump(){
    const version=generation.current;setSeeking(true);setError('');try{
      const result=await api<{timezone:string;instants:Instant[]}>('/api/time/resolve',{method:'POST',body:JSON.stringify({date,time}),signal:AbortSignal.timeout(10000)});
      if(version!==generation.current)return;
      if(zone!==applicationTimezone()){setNotice('Timezone changed; choose the local time again.');setSeeking(false);return}
      if(result.timezone!==zone){setApplicationTimezone(result.timezone);setNotice('Timezone changed; choose the local time again.');setSeeking(false);return}
      if(result.instants.length>1&&!result.instants.some(value=>value.time===occurrence)){setInstants(result.instants);setNotice('This local time occurs twice. Choose an offset.');setSeeking(false);return}
      await jump(Date.parse(result.instants.length>1?occurrence:result.instants[0].time));
    }catch(e){if(version===generation.current){fail(e,'local');setSeeking(false)}}
  }
  function play(){
    if(!session.current){localJump();return}
    const updated={...slotRef.current};for(const id of ids)if(updated[id]?.error==='Playback blocked. Pause and press Play to retry.')updated[id]={...updated[id],error:undefined};updateSlots(updated);
    clock.current.play();setPlaying(true);for(const id of ids)sync(id,true);
  }
  async function adjacent(direction:'next'|'previous'){
    const current=slotRef.current[ids[0]]?.segment;if(!current)return;setSeeking(true);setError('');
    const version=generation.current;
    try{const choice=await api<{segment:Segment|null;seek_seconds:number;gap_seconds:number}>(`/api/recordings/${current.id}/adjacent?direction=${direction}`);if(version!==generation.current)return;if(choice.segment){await jump(Date.parse(choice.segment.started_at)+choice.seek_seconds*1000);if(choice.gap_seconds&&generation.current===version+1){setGap({seconds:Math.round(choice.gap_seconds),time:choice.segment.started_at});setNotice('gap')}}else setNotice(direction==='next'?'End of this camera’s archive.':'No earlier recording.')}catch(e){if(version===generation.current)fail(e,direction)}finally{if(version===generation.current)setSeeking(false)}
  }
  useEffect(()=>{
    const timer=setInterval(()=>{
      if(preparation.current!==null){
        const ready=ids.every(id=>{const slot=slotRef.current[id],player=players.current.get(id);return !slot?.segment||!!slot.error||(player?.segment===slot.segment.id&&player.video.readyState>=1)});
        if(ready||performance.now()>=preparation.current){preparation.current=null;setPreparing(false);clock.current.play();setPlaying(true)}
      }
      if(!session.current)return;
      const target=clock.current.position(),needed:number[]=[];
      for(const id of ids){
        const slot=slotRef.current[id];if(!slot||resolving.current.has(id))continue;
        if(slot.retryAt){if(performance.now()>=slot.retryAt)needed.push(id);continue}
        if(slot.segment&&target>=Date.parse(slot.segment.ended_at)){
          if(ids.length===1&&!automatic){clock.current.seek(Date.parse(slot.segment.ended_at));stop();setNotice('Automatic playback paused at the segment boundary.');break}
          needed.push(id);
        }else if(!slot.segment&&slot.next_segment&&target>=Date.parse(slot.next_segment.started_at))needed.push(id);
        else sync(id);
      }
      if(needed.length)resolve(needed,target,generation.current);
      const current=clock.current.position();setStamp(previous=>previous!==null&&Math.floor(previous/1000)===Math.floor(current/1000)?previous:current);
      if(clock.current.running){
        const selected=ids.map(id=>slotRef.current[id]);
        if(selected.length&&resolving.current.size===0&&selected.every(slot=>slot&&!slot.segment&&!slot.next_segment&&!slot.retryAt)){
          stop();setNotice(ids.length===1?'End of this camera’s archive.':'End of the selected cameras’ archive.');
        }else{const currentDate=localDate(current,zone);if(currentDate!==date)setDate(currentDate)}
      }
    },250);
    return()=>clearInterval(timer);
  },[idsKey,automatic,zone,date]);
  function pointer(event:React.PointerEvent<HTMLDivElement>){const box=event.currentTarget.getBoundingClientRect();return timelineStart+Math.min(1,Math.max(0,(event.clientX-box.left)/box.width))*(timelineEnd-timelineStart-1)}
  function pointerDown(event:React.PointerEvent<HTMLDivElement>){if(!day||seeking)return;event.currentTarget.setPointerCapture(event.pointerId);setPreview(pointer(event))}
  function pointerMove(event:React.PointerEvent<HTMLDivElement>){if(event.currentTarget.hasPointerCapture(event.pointerId))setPreview(pointer(event))}
  function pointerUp(event:React.PointerEvent<HTMLDivElement>){if(!event.currentTarget.hasPointerCapture(event.pointerId))return;event.currentTarget.releasePointerCapture(event.pointerId);jump(pointer(event))}
  function keyboard(event:React.KeyboardEvent<HTMLDivElement>){
    if(!day)return;
    const current=preview??stamp??timelineStart,values:Record<string,number>={ArrowLeft:current-(minutes?60000:300000),ArrowRight:current+(minutes?60000:300000),Home:timelineStart,End:timelineEnd-1};
    if(event.key in values){event.preventDefault();setPreview(Math.min(timelineEnd-1,Math.max(timelineStart,values[event.key])))}else if(event.key==='Enter'||event.key===' '){event.preventDefault();if(!seeking)jump(current)}
  }
  const cursor=preview??stamp??(day?timelineStart:serverNow()),percent=day?Math.max(0,Math.min(100,(cursor-timelineStart)/(timelineEnd-timelineStart)*100)):0;
  const pointerEvents={onPointerDown:pointerDown,onPointerMove:pointerMove,onPointerUp:pointerUp,onPointerCancel:()=>setPreview(null)};
  const selectedValue=ids.length===1?String(ids[0]):ids.length===cameras.length&&ids.length?'all':ids.length?'multiple':'';
  return <>
    <div className="toolbar archive-filters"><label>{t('Archive camera')}<select aria-label={t("Archive camera")} value={selectedValue} onChange={event=>select(event.target.value==='all'?cameras.map(c=>c.id):event.target.value?[Number(event.target.value)]:[])}><option value="">{t('Choose cameras…')}</option><option value="all">{t('All cameras')}</option>{selectedValue==='multiple'&&<option value="multiple" disabled>{t('Multiple cameras')}</option>}{cameras.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label>{t('Archive date')}<input type="date" value={date} onChange={event=>{reset();setDate(event.target.value);setMinutes(0);setInstants([]);setOccurrence('')}}/></label><label>{t('Archive time')}<input type="time" step="1" value={time} onChange={event=>{setTime(event.target.value);setInstants([]);setOccurrence('')}}/></label><button className="primary" disabled={!ids.length||!date||!time||seeking||preparing} onClick={localJump}>{t(seeking?'Finding recording…':'Play selected cameras')}</button><button disabled={loading||seeking||preparing} onClick={refresh}>{t('Refresh')}</button></div>
    <div className="toolbar"><button onClick={()=>setMinutes(0)} aria-pressed={!minutes}>{t('Full day')}</button>{[5,15,30,60].map(value=><button key={value} aria-pressed={minutes===value} onClick={()=>{setRangeAnchor((preview??stamp??serverNow())+1000);setMinutes(value)}}>{value} {t('min')}</button>)}<button disabled={!ids.length||seeking} onClick={()=>{const target=serverNow()-5*60000;setRangeAnchor(serverNow());setMinutes(15);setDate(localDate(target));jump(target)}}>{t('Recent recordings')}</button></div>
    <p className="help archive-zone">{minutes&&day?`${when(day.start)} — ${when(day.end)}`:date} · <strong>{zone}</strong></p>
    <details className="archive-selection"><summary>{t('Selected cameras: {count}',{count:ids.length})}</summary><div className="actions"><button onClick={()=>select(cameras.map(c=>c.id))}>{t('Select all')}</button><button onClick={()=>select([])}>{t('Clear')}</button></div><div className="camera-choices">{cameras.map(c=><label className="smallcheck" key={c.id}><input type="checkbox" checked={ids.includes(c.id)} onChange={event=>select(event.target.checked?[...ids,c.id]:ids.filter(id=>id!==c.id))}/>{c.name}</label>)}</div></details>
    {!!instants.length&&<label className="occurrence-choice">{t('Repeated local time')}<select aria-label={t("Repeated local time")} value={occurrence} onChange={event=>setOccurrence(event.target.value)}><option value="">{t('Choose an offset…')}</option>{instants.map(value=><option key={value.time} value={value.time}>{value.local.slice(11,19)} · UTC{value.local.match(/([+-]\d{2}:\d{2}(?::\d{2})?|Z)$/)?.[0]||''}</option>)}</select></label>}
    {!!error&&<Feedback tone="bad">{errorMessage(error)} <button onClick={()=>retry==='local'?localJump():retry==='next'||retry==='previous'?adjacent(retry):load(retry==='more')}>{t('Retry')}</button></Feedback>}{notice&&<Feedback>{notice==='gap'&&gap?t('Recording gap: {seconds} s. Next file starts {time}',{seconds:gap.seconds,time:when(gap.time)}):t(notice)}</Feedback>}{(seeking||preparing)&&<Feedback>{t(preparing?'Preparing synchronized playback…':'Finding recording…')}</Feedback>}
    <section className="archive-timeline"><h3>{t('Daily recordings')}</h3><div className="archive-track"><span/><div className="timeline master-axis" ref={setTimelineHost}><div className="timeline-seek" role="slider" tabIndex={0} aria-label={t('Recording timeline')} aria-valuemin={0} aria-disabled={!day} aria-valuemax={Math.max(0,Math.floor((timelineEnd-timelineStart)/1000)-1)} aria-valuenow={Math.max(0,Math.min(Math.floor((timelineEnd-timelineStart)/1000)-1,Math.floor((cursor-timelineStart)/1000)))} aria-valuetext={when(new Date(cursor).toISOString())} onKeyDown={keyboard} {...pointerEvents}><span className="timeline-cursor" style={{left:`${percent}%`}}/></div></div></div>{ids.map(id=><div className="archive-track" key={id}><strong>{cameras.find(c=>c.id===id)?.name}</strong><div className="timeline" {...pointerEvents} aria-label={t('Recordings for {name}',{name:cameras.find(c=>c.id===id)?.name||id})}>{tracks[id]?.intervals.map((interval,i)=><span className="recorded" key={i} title={`${when(interval.start)} — ${when(interval.end)}`} style={{left:`${(Date.parse(interval.start)-timelineStart)/(timelineEnd-timelineStart)*100}%`,width:`${(Date.parse(interval.end)-Date.parse(interval.start))/(timelineEnd-timelineStart)*100}%`}}/>)}<span className="timeline-cursor" style={{left:`${percent}%`}}/></div></div>)}<div className="archive-track"><span/><div className="timeline-scale">{day?.ticks.filter((_,i)=>minutes||i%6===0||i===day.ticks.length-1).map((tick,i)=><span key={tick.time} title={tick.local} style={{left:`${(Date.parse(tick.time)-timelineStart)/(timelineEnd-timelineStart)*100}%`}}>{i===0?localClock(Date.parse(tick.time)).slice(0,5):!minutes&&tick.time===day?.end?'24:00':localClock(Date.parse(tick.time)).slice(0,5)}</span>)}</div></div><small>{t('Green = recording; dark = gap. Click the timeline to seek.')} {t('Use arrow keys to choose a time; Enter to play.')}</small><ArchiveExport cameras={cameras} selected={ids} cursor={cursor} timeline={timelineHost} start={timelineStart} end={timelineEnd} onLocate={stamp=>{setDate(localDate(stamp));setRangeAnchor(stamp+150000);setMinutes(5)}} onError={onError}/></section>
    <section className="synchronized-playback"><h3>{t('Playback')}</h3><div className="toolbar"><button className="primary" disabled={!ids.length||seeking||preparing||(!session.current&&(!date||!time))} onClick={playing?stop:play}>{t(playing?'Pause':'Play')}</button><label>{t('Speed')}<select aria-label={t("Speed")} value={speed} onChange={event=>{const value=Number(event.target.value);clock.current.setSpeed(value);setSpeed(value);ids.forEach(id=>sync(id,true))}}>{[.5,1,2,4].map(rate=><option key={rate} value={rate}>{rate}×</option>)}</select></label><span className="playback-clock" data-testid="master-time">{stamp!==null?when(new Date(stamp).toISOString()):t('Choose a time to watch')}</span></div>{ids.length===1&&<label className="smallcheck"><input type="checkbox" checked={automatic} onChange={event=>setAutomatic(event.target.checked)}/>{t('Automatically play the next recording from this camera')}</label>}
    {!ids.length?<EmptyState title={t('Choose cameras to watch')}>{t('Select one or more cameras, then choose a date and time.')}</EmptyState>:<div className="archive-video-grid" style={{"--archive-columns":Math.ceil(Math.sqrt(ids.length)),"--archive-gap":`${(Math.ceil(Math.sqrt(ids.length))-1)*12}px`} as CSSProperties}>{ids.map(id=>{const slot=slots[id],segment=slot?.segment,name=cameras.find(c=>c.id===id)?.name||String(id);return <article className="archive-camera" key={id} data-camera-id={id}>{segment?<VideoContainer className="recorded-video" header={<strong>{name}</strong>} headerClassName="archive-camera-head" headerActions={<><a className="button-link quiet" href={`/api/recordings/${segment.id}/download`}>{t('Download MP4')}</a>{ids.length===1&&<><button disabled={seeking||preparing} onClick={()=>adjacent('previous')}>{t('← Previous')}</button><button disabled={seeking||preparing} onClick={()=>adjacent('next')}>{t('Next →')}</button></>}</>}><video key={segment.id} muted playsInline preload="auto" ref={node=>{if(node)players.current.set(id,{video:node,segment:segment.id});else players.current.delete(id)}} src={`/api/recordings/${segment.id}`} onLoadedMetadata={event=>{if(players.current.get(id)?.video===event.currentTarget)sync(id,true)}} onError={event=>{if(slotRef.current[id]?.segment?.id===segment.id&&players.current.get(id)?.video===event.currentTarget)updateSlots({...slotRef.current,[id]:{...slotRef.current[id],error:'File unavailable or codec unsupported by the browser.'}})}}/></VideoContainer>:<><div className="archive-camera-head"><strong>{name}</strong></div><EmptyState title={t(seeking?'Finding recording…':!session.current?'Choose a time to watch':'No recording at this time')}>{slot?.next_segment?t('Footage resumes at {time}',{time:when(slot.next_segment.started_at)}):t('Use the timeline to choose another moment.')}</EmptyState></>}{slot?.error&&<Feedback tone="bad">{t(slot.error)}</Feedback>}</article>})}</div>}</section>
    <section className="archive-list"><h3>{t('Recordings')}</h3>{segments.map(segment=><div className="row" key={segment.id}><div><strong>{cameras.find(c=>c.id===segment.camera_id)?.name||t('Camera {id}',{id:segment.camera_id})}</strong><p>{when(segment.started_at)} — {when(segment.ended_at)}</p><small>{(segment.size_bytes/1024**2).toFixed(1)} MB</small></div><button disabled={!ids.length||seeking} onClick={()=>jump(Math.max(start,Date.parse(segment.started_at)))}>{t('Watch')}</button></div>)}{!segments.length&&<EmptyState title={t(loading?'Loading…':error?'Recordings unavailable':!date?'Choose an archive date':'No recordings for this date')}>{t('Try another date or camera. Footage appears here after a recording file is completed.')}</EmptyState>}{more&&<button disabled={loading} onClick={()=>load(true)}>{t(loading?'Loading…':'Load more')}</button>}</section>
  </>;
}
