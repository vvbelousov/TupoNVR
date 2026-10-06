import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=ts.transpileModule(readFileSync(new URL('../src/archiveClock.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {ArchiveClock,synchronize,driftTolerance}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const segment=(id,start,seconds)=>({id,camera_id:id,started_at:new Date(start).toISOString(),ended_at:new Date(start+seconds*1000).toISOString(),size_bytes:4096});
function media(){return {currentTime:0,duration:120,readyState:4,seeking:false,paused:true,ended:false,playbackRate:1,seeks:0,plays:0,pause(){this.paused=true},async play(){this.plays++;this.paused=false}}}

test('one monotonic absolute clock preserves pause/resume and shared speed',()=>{
 let tick=0;const clock=new ArchiveClock(()=>tick,Date.parse('2026-10-06T11:32:17Z'));
 clock.play();tick=1000;assert.equal(clock.position(),Date.parse('2026-10-06T11:32:18Z'));
 clock.pause();tick=6000;assert.equal(clock.position(),Date.parse('2026-10-06T11:32:18Z'));
 clock.setSpeed(4);clock.play();tick=6500;assert.equal(clock.position(),Date.parse('2026-10-06T11:32:20Z'));
 clock.setSpeed(.5);tick=8500;assert.equal(clock.position(),Date.parse('2026-10-06T11:32:21Z'));
 clock.seek(Date.parse('2026-10-06T12:00:00Z'));assert.equal(clock.position(),Date.parse('2026-10-06T12:00:00Z'));
});

test('absolute target produces independent offsets and common controls',async()=>{
 const start=Date.parse('2026-10-06T11:32:00Z'),a=media(),b=media();
 synchronize(a,segment(1,start,90),start+17000,true,2,true);
 synchronize(b,segment(2,start+10000,90),start+17000,true,2,true);
 assert.equal(a.currentTime,17);assert.equal(b.currentTime,7);
 assert.equal(a.playbackRate,2);assert.equal(b.playbackRate,2);
 assert.equal(a.paused,false);assert.equal(b.paused,false);
 synchronize(a,segment(1,start,90),start+17000,false,2);
 synchronize(b,segment(2,start+10000,90),start+17000,false,2);
 assert.equal(a.paused,true);assert.equal(b.paused,true);
});

test('drift within tolerance is stable; larger drift is corrected without repeated tiny seeks',()=>{
 const stamp=Date.parse('2026-10-06T11:32:00Z'),player=media(),record=segment(1,stamp,90);
 player.currentTime=10+driftTolerance/2;synchronize(player,record,stamp+10000,false,1);assert.equal(player.currentTime,10+driftTolerance/2);
 player.currentTime=8;synchronize(player,record,stamp+10000,false,1);assert.equal(player.currentTime,10);
 player.seeking=true;player.currentTime=5;synchronize(player,record,stamp+10000,false,1);assert.equal(player.currentTime,5);
 player.seeking=false;player.readyState=0;synchronize(player,record,stamp+10000,false,1);assert.equal(player.currentTime,5);
});

test('gaps and segment boundaries pause only the affected media; later footage can join the same clock',async()=>{
 const stamp=Date.parse('2026-10-06T11:32:00Z'),player=media(),other=media();
 synchronize(player,segment(1,stamp+20000,40),stamp+17000,true,1);assert.equal(player.paused,true);
 synchronize(other,segment(2,stamp,60),stamp+17000,true,1,true);assert.equal(other.paused,false);
 synchronize(player,segment(1,stamp+20000,40),stamp+22000,true,1,true);assert.equal(player.paused,false);assert.equal(player.currentTime,2);
 await new Promise(resolve=>setImmediate(resolve));
 synchronize(other,segment(2,stamp,60),stamp+60000,true,1);assert.equal(other.paused,true);
 synchronize(other,segment(3,stamp+60000,60),stamp+62500,true,1,true);assert.equal(other.currentTime,2.5);assert.equal(other.paused,false);
});

test('pending play promises are not repeated and media rejection is contained',async()=>{
 let reject,errors=0;const player=media(),stamp=Date.parse('2026-10-06T11:32:00Z');
 player.play=function(){this.plays++;return new Promise((_,no)=>{reject=no})};
 const record=segment(1,stamp,90);
 synchronize(player,record,stamp+10000,true,1,true,()=>errors++);
 synchronize(player,record,stamp+10000,true,1,false,()=>errors++);
 assert.equal(player.plays,1);reject(new Error('blocked'));await new Promise(resolve=>setImmediate(resolve));assert.equal(errors,1);
});
