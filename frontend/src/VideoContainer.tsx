import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {useLanguage} from './i18n';
import {fullscreenSupported,toggleFullscreen} from './fullscreen';

// Fullscreen changes presentation only; children retain their DOM and playback state.
export function VideoContainer({children,className=''}:{children:ReactNode;className?:string}) {
  const {t}=useLanguage();
  const container=useRef<HTMLDivElement>(null),mounted=useRef(false);
  const [supported,setSupported]=useState(false),[fullscreen,setFullscreen]=useState(false);
  const [pending,setPending]=useState(false),[error,setError]=useState(false);
  useEffect(()=>{
    const element=container.current!;
    const document=element.ownerDocument;
    mounted.current=true;
    function change(){setSupported(fullscreenSupported(element));setFullscreen(document.fullscreenElement===element);setError(false)}
    change();
    document.addEventListener('fullscreenchange',change);
    return()=>{mounted.current=false;document.removeEventListener('fullscreenchange',change)};
  },[]);
  async function toggle(){
    if(!container.current||pending)return;
    setPending(true);setError(false);
    try{await toggleFullscreen(container.current)}catch{if(mounted.current)setError(true)}finally{if(mounted.current)setPending(false)}
  }
  const label=t(fullscreen?'Exit fullscreen':'Enter fullscreen');
  return <div ref={container} className={`video-container ${className}`}>
    {children}
    {supported&&<button type="button" className="fullscreen-control" disabled={pending} aria-label={label} title={label} aria-pressed={fullscreen} onClick={toggle}>
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d={fullscreen?'M3 8h5V3M16 3v5h5M21 16h-5v5M8 21v-5H3':'M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5'}/></svg>
    </button>}
    {error&&<span className="fullscreen-error" role="status">{t('Fullscreen unavailable. Try again or use browser controls.')}</span>}
  </div>;
}
