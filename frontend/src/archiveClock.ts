/** One monotonic absolute clock; buffering or a gap never makes another camera wait. */
export class ArchiveClock {
  private instant:number;
  private tick:number;
  running=false;
  speed=1;
  constructor(private now:()=>number=()=>performance.now(),initial=Date.now()){this.instant=initial;this.tick=now()}
  position(){return this.instant+(this.running?(this.now()-this.tick)*this.speed:0)}
  seek(stamp:number){this.instant=stamp;this.tick=this.now()}
  play(){if(!this.running){this.tick=this.now();this.running=true}}
  pause(){this.instant=this.position();this.tick=this.now();this.running=false}
  setSpeed(value:number){this.seek(this.position());this.speed=value}
}
export type Segment={id:number;camera_id:number;started_at:string;ended_at:string;size_bytes:number};
export const driftTolerance=.75;
export type MediaPlayer={currentTime:number;duration:number;readyState:number;seeking:boolean;paused:boolean;ended:boolean;playbackRate:number;pause:()=>void;play:()=>Promise<void>};
const pending=new WeakSet<MediaPlayer>();
export function synchronize(player:MediaPlayer,segment:Segment,stamp:number,running:boolean,speed:number,force=false,onError:()=>void=()=>{}){
  const offset=(stamp-Date.parse(segment.started_at))/1000;
  if(offset<0||stamp>=Date.parse(segment.ended_at)){player.pause();return}
  player.playbackRate=speed;
  if(!running&&!player.paused)player.pause();
  if(player.readyState<1||player.seeking)return;
  const target=Math.min(offset,Number.isFinite(player.duration)?Math.max(0,player.duration-.01):offset);
  if(force||Math.abs(player.currentTime-target)>driftTolerance)player.currentTime=target;
  if(running&&player.paused&&!player.ended&&!pending.has(player)){
    pending.add(player);
    player.play().catch(onError).finally(()=>pending.delete(player));
  }
}
