import type {ReactNode} from 'react';
import {useEffect,useRef,useState} from 'react';
import {archiveURL} from './navigation';
import {serverNow} from './time';
import {VideoContainer} from './VideoContainer';
import {useLanguage} from './i18n';

declare global {interface Window {MediaMTXWebRTCReader: new (config:{url:string;onTrack:(event:RTCTrackEvent)=>void;onError:(err:string)=>void}) => {close:()=>void}}}
export function Stream({id,sub=false,header,headerActions}:{id:number;sub?:boolean;header?:ReactNode;headerActions?:ReactNode}) {
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
      onError:()=>{if(!active)return;setConnecting(false);setIssue('Live stream unavailable. Use Check on Cameras. If the camera is readable, check browser codec support, WebRTC host settings and the UDP firewall. Reconnecting…')}
    });
    return ()=>{active=false;reader.close();if(video.current)video.current.srcObject=null};
  },[id,sub]);
  const controls=<><a className="archive-control button-link" href={archiveURL(id,serverNow()-5*60000)}>{tr('Archive')}</a><button className="fit" onClick={()=>setFit(fit==='contain'?'cover':'contain')}>{tr(fit==='contain'?'Fit':'Fill')}</button></>;
  return <VideoContainer className="stream" header={header} headerActions={headerActions} controls={controls}><video onError={()=>{setConnecting(false);setIssue('Live playback failed. Check browser codec support.')}} ref={video} autoPlay muted playsInline style={{objectFit:fit}}/>{(issue||connecting)&&<span role="status" className="stream-error">{tr(issue||'Connecting to camera…')}</span>}</VideoContainer>;
}
