import {useRef,useState} from 'react';
import type {PointerEvent} from 'react';
import {useLanguage} from './i18n';
export type ExportInterval={start:number;end:number};
export function moveBoundary(value:ExportInterval,boundary:'start'|'end',stamp:number):ExportInterval{
  stamp=Math.floor(stamp/1000)*1000;
  return boundary==='start'?{...value,start:Math.min(stamp,value.end-1000)}:{...value,end:Math.max(stamp,value.start+1000)};
}
export function ExportSelection({value,start,end,disabled,onChange}:{value:ExportInterval;start:number;end:number;disabled:boolean;onChange:(value:ExportInterval)=>void}){
  const {t,when}=useLanguage(),drag=useRef<{boundary:'start'|'end'|'range';anchor:number}|null>(null);
  const [dragging,setDragging]=useState(false);
  if(end<=start)return null;
  const position=(stamp:number)=>(stamp-start)/(end-start)*100;
  function instant(event:PointerEvent<HTMLElement>){const box=event.currentTarget.closest('.export-selection')!.getBoundingClientRect();return Math.floor((start+Math.max(0,Math.min(1,(event.clientX-box.left)/box.width))*(end-start))/1000)*1000}
  function down(event:PointerEvent<HTMLElement>,boundary:'start'|'end'|'range'){
    event.stopPropagation();event.preventDefault();if(disabled)return;
    event.currentTarget.setPointerCapture(event.pointerId);drag.current={boundary,anchor:instant(event)};setDragging(true);
  }
  function move(event:PointerEvent<HTMLElement>){
    event.stopPropagation();if(!drag.current)return;
    const stamp=instant(event),{boundary,anchor}=drag.current;
    onChange(boundary==='range'?{start:Math.min(anchor,stamp),end:Math.max(anchor,stamp,Math.min(anchor,stamp)+1000)}:moveBoundary(value,boundary,stamp));
  }
  function up(event:PointerEvent<HTMLElement>){event.stopPropagation();if(drag.current)move(event);drag.current=null;setDragging(false);if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId)}
  const events={onPointerMove:move,onPointerUp:up,onPointerCancel:(event:PointerEvent<HTMLElement>)=>{event.stopPropagation();drag.current=null;setDragging(false)}};
  return <div className={`export-selection${dragging?' dragging':''}`} aria-label={t('Drag to select export interval')} onPointerDown={event=>down(event,'range')} {...events}>
    <span className="export-selection-fill" style={{left:`${Math.max(0,position(value.start))}%`,right:`${Math.max(0,100-position(value.end))}%`}}/>
    {(['start','end'] as const).map(boundary=>value[boundary]>=start&&value[boundary]<=end&&<button key={boundary} className={`export-handle ${boundary}`} role="slider" aria-label={t(boundary==='start'?'Export start boundary':'Export end boundary')} aria-valuemin={Math.floor((boundary==='start'?Math.min(start,value.start):value.start+1000)/1000)} aria-valuemax={Math.floor((boundary==='end'?Math.max(end,value.end):value.end-1000)/1000)} aria-valuenow={Math.floor(value[boundary]/1000)} aria-valuetext={when(new Date(value[boundary]).toISOString())} disabled={disabled} style={{left:`${position(value[boundary])}%`}} onPointerDown={event=>down(event,boundary)} {...events} onKeyDown={event=>{
      event.stopPropagation();const delta=(event.shiftKey?60:1)*1000;
      const target=event.key==='ArrowLeft'?value[boundary]-delta:event.key==='ArrowRight'?value[boundary]+delta:event.key==='Home'?start:event.key==='End'?end:null;
      if(target!==null){event.preventDefault();onChange(moveBoundary(value,boundary,target))}
    }}>{t(boundary==='start'?'Start':'End')}</button>)}
  </div>;
}
