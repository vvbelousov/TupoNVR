import {useEffect,useRef,useState} from 'react';
import {VideoContainer} from './VideoContainer';
import {useLanguage} from './i18n';

declare global {interface Window {MediaMTXWebRTCReader: new (config:{url:string;onTrack:(event:RTCTrackEvent)=>void;onError:(err:string)=>void}) => {close:()=>void}}}
export function Stream({id,sub=false}:{id:number;sub?:boolean}) {
  const {t:tr}=useLanguage();
  const video=useRef<HTMLVideoElement>(null);
  const [fit,setFit]=useState<'contain'|'cover'>('contain');
  const [issue,setIssue]=useState(''),[connecting,setConnecting]=useState(true);
  useEffect(()=>{
    setConnecting(true);setIssue('');
    let active=true;
    const reader=new window.MediaMTXWebRTCReader({
      url:`${location.origin}/api/media/cam_${id}${sub?'_sub':''}/whep`,
      onTrack:e=>{if(!active)return;if(video.current){video.current.srcObject=e.streams[0];video.current.play().catch(()=>{if(!active)return;setIssue('Live playback failed. Check browser codec support.');setConnecting(false)})}setIssue('');setConnecting(false)},
      onError:()=>{if(!active)return;setConnecting(false);setIssue('Stream unavailable. Reconnecting…')}
    });
    return ()=>{active=false;reader.close();if(video.current)video.current.srcObject=null};
  },[id,sub]);
  return <VideoContainer className="stream"><video onError={()=>{setConnecting(false);setIssue('Live playback failed. Check browser codec support.')}} ref={video} autoPlay muted playsInline style={{objectFit:fit}}/><button className="fit" onClick={()=>setFit(fit==='contain'?'cover':'contain')}>{tr(fit==='contain'?'Fit':'Fill')}</button>{(issue||connecting)&&<span role="status" className="stream-error">{tr(issue||'Connecting to camera…')}</span>}</VideoContainer>;
}
