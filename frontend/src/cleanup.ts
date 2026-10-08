export type Criteria={camera_ids?:number[];recording_ids?:number[];start?:string;end?:string};
export type Recording={id:number;camera_id:number;started_at:string;ended_at:string;size_bytes:number};
export type Preview={token:string;count:number;size_bytes:number;cameras:number[];criteria:Criteria;active_excluded:number;not_found?:number;clear_all:boolean};
export type Progress={state:string;processed:number;total:number;deleted:number;reclaimed_bytes:number;active:number;missing:number;failed:number};
export function pageSelection(records:Recording[],selected:number[],checked:boolean){
  const page=new Set(records.map(row=>row.id));
  return checked?[...new Set([...selected,...page])]:selected.filter(id=>!page.has(id));
}
export function confirmationReady(preview:Preview,text:string){return preview.count>0&&(!preview.clear_all||text==='DELETE')}
export function instantChoice(instants:{time:string}[],occurrence:string){
  if(instants.length===1)return instants[0].time;
  if(instants.length===2&&(occurrence==='earlier'||occurrence==='later'))return instants[occurrence==='earlier'?0:1].time;
  throw new Error('Repeated local time: choose the earlier or later occurrence.');
}
export function bytes(size:number){
  if(size<1024)return `${size} B`;
  const unit=Math.min(4,Math.floor(Math.log(size)/Math.log(1024)));
  return `${(size/1024**unit).toFixed(2)} ${['B','KB','MB','GB','TB'][unit]}`;
}
export const recordingChangeEvent='nvr-recordings-changed';
export function announceRecordingCleanup(){
  try{localStorage.setItem(recordingChangeEvent,String(Date.now()))}catch{/* Storage can be disabled by the browser. */}
  window.dispatchEvent(new Event(recordingChangeEvent));
}
