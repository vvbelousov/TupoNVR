import {useLanguage} from './i18n';
import {EmptyState,Feedback} from './ui';
import {Stream} from './Stream';

export function SingleCameraLive({camera,loading,failed,status,onOverview,onEdit}:{camera?:{id:number;name:string;enabled:boolean};loading:boolean;failed:boolean;status?:{connectivity_state?:string;state?:string};onOverview:()=>void;onEdit:(id:number)=>void}){
  const {t:tr,statusText,connectivityClass}=useLanguage();
  const connectivity=status?.connectivity_state||status?.state||'UNKNOWN';
  return <div className="single-camera-live">
    <div className="toolbar"><button onClick={onOverview}>{tr('Back to Overview')}</button>{camera&&<><span aria-label={tr('Connectivity')} className={'state '+connectivityClass(connectivity)}>{statusText(connectivity)}</span><button onClick={()=>onEdit(camera.id)}>{tr('Edit camera')}</button></>}</div>
    {loading?<Feedback>{tr('Loading camera…')}</Feedback>:!camera?<EmptyState title={tr(failed?'Camera could not be loaded':'Camera not found')}>{tr(failed?'Retry loading appliance status above.':'This camera does not exist or has been deleted.')}</EmptyState>:<>
      {(!camera.enabled||connectivity==='OFFLINE')&&<Feedback tone="warning">{tr(!camera.enabled?'Camera is disabled. Enable it on the Cameras page to watch live.':'Camera is offline. Live playback will reconnect when available.')}</Feedback>}
      <div className="single-camera-video" aria-label={tr('Live stream for {name}',{name:camera.name})}><Stream key={camera.id} id={camera.id}/></div>
    </>}
  </div>;
}
