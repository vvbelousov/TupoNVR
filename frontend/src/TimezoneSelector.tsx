import {useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {useLanguage} from './i18n';

// Search text is local to the popup; only selecting a server-provided zone changes value.
export function TimezoneSelector({value,zones,onChange,disabled=false,loading=false}:{value:string;zones:string[];onChange:(zone:string)=>void;disabled?:boolean;loading?:boolean}){
  const {t}=useLanguage(),id=useId();
  const input=useRef<HTMLInputElement>(null),popup=useRef<HTMLDivElement>(null);
  const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[active,setActive]=useState(-1);
  const [position,setPosition]=useState({left:0,top:0,width:0,maxHeight:240});
  const options=zones.filter(zone=>zone.toLowerCase().includes(query.trim().toLowerCase()));
  function show(){if(disabled)return;setQuery('');setActive(zones.indexOf(value));setOpen(true)}
  function choose(zone:string){if(!zones.includes(zone))return;onChange(zone);setOpen(false);setQuery('')}
  useEffect(()=>{if(disabled)setOpen(false)},[disabled]);
  useEffect(()=>{if(open)setActive(query?0:zones.indexOf(value))},[query,zones,value,open]);
  useLayoutEffect(()=>{
    if(!open)return;
    function place(){
      const rect=input.current!.getBoundingClientRect(),viewport=window.visualViewport;
      const bottom=(viewport?.height??window.innerHeight)+(viewport?.offsetTop??0),top=viewport?.offsetTop??0;
      const below=bottom-rect.bottom-8,above=rect.top-top-8;
      const height=Math.max(0,Math.min(240,below>=120||below>=above?below:above));
      setPosition({left:rect.left,top:below>=120||below>=above?rect.bottom+4:rect.top-height-4,width:rect.width,maxHeight:height});
    }
    place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
    window.visualViewport?.addEventListener('resize',place);window.visualViewport?.addEventListener('scroll',place);
    return()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);window.visualViewport?.removeEventListener('resize',place);window.visualViewport?.removeEventListener('scroll',place)};
  },[open]);
  useEffect(()=>{
    if(!open)return;
    const outside=(event:PointerEvent)=>{if(!input.current?.contains(event.target as Node)&&!popup.current?.contains(event.target as Node))setOpen(false)};
    document.addEventListener('pointerdown',outside);
    return()=>document.removeEventListener('pointerdown',outside);
  },[open]);
  useEffect(()=>{if(open)popup.current?.querySelector(`[id="${id}-option-${active}"]`)?.scrollIntoView({block:'nearest'})},[active,open,id]);
  return <div className="timezone-selector"><label htmlFor={id}>{t('Timezone')}</label><input ref={input} id={id} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={open?`${id}-list`:undefined} aria-activedescendant={open&&options[active]?`${id}-option-${active}`:undefined} autoComplete="off" autoCapitalize="none" spellCheck={false} disabled={disabled} value={open?query:value} placeholder={value} onFocus={show} onClick={()=>{if(!open)show()}} onBlur={event=>{if(!popup.current?.contains(event.relatedTarget as Node))setOpen(false)}} onChange={event=>{setQuery(event.target.value);setActive(0)}} onKeyDown={event=>{
    if(event.key==='Escape'){if(open){event.preventDefault();event.stopPropagation();setOpen(false)}return}
    if(event.key==='Tab'){setOpen(false);return}
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      event.preventDefault();if(!open){show();return}
      setActive(index=>options.length?(index<0?(event.key==='ArrowDown'?0:options.length-1):(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length):-1);return;
    }
    if(event.key==='Enter'&&open){event.preventDefault();if(options[active])choose(options[active])}
  }}/>{open&&createPortal(<div ref={popup} id={`${id}-list`} role="listbox" aria-label={t('Timezone')} aria-busy={loading} className="timezone-options" style={position}>{options.map((zone,index)=><div key={zone} id={`${id}-option-${index}`} role="option" aria-selected={zone===value} className={index===active?'highlighted':''} onMouseDown={event=>event.preventDefault()} onClick={()=>choose(zone)}>{zone}{zone===value&&<span aria-hidden="true"> ✓</span>}</div>)}{!options.length&&<div role="status" className="timezone-empty">{t(loading?'Loading timezones…':'No matching timezones')}</div>}</div>,document.body)}</div>;
}
