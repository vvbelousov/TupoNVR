import {useSyncExternalStore} from 'react';
let timezone='UTC';
let revision=0;
let serverTime=Date.now(),serverTick=performance.now();
const listeners=new Set<()=>void>();
export const applicationTimezone=()=>timezone;
export const timezoneRevision=()=>revision;
export const serverNow=()=>serverTime+performance.now()-serverTick;
export function setServerTime(value?:string){if(value){serverTime=Date.parse(value);serverTick=performance.now()}}
export function setApplicationTimezone(value:string){
  if(value===timezone)return;
  // Values come from the server's validated IANA database.
  new Intl.DateTimeFormat('en',{timeZone:value});
  timezone=value;revision++;listeners.forEach(listener=>listener());
}
export function useTimezone(){
  return useSyncExternalStore(listener=>{listeners.add(listener);return()=>{listeners.delete(listener)}},applicationTimezone);
}
export function localDate(stamp:number=serverNow(),zone=timezone){
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(stamp);
  const get=(name:string)=>parts.find(part=>part.type===name)!.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}
export const localClock=(stamp:number,zone=timezone)=>new Intl.DateTimeFormat('en-GB',{timeZone:zone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(stamp);
