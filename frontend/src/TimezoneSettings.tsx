import {useEffect,useState} from 'react';
import {api,errorMessage,isUnauthorized} from './api';
import {useLanguage} from './i18n';
import {localClock,setApplicationTimezone,useTimezone,serverNow,setServerTime} from './time';
import {Feedback} from './ui';
export function TimezoneSettings({onError}:{onError:(error:unknown)=>void}){
  const {t}=useLanguage(),timezone=useTimezone();
  const [draft,setDraft]=useState(timezone),[zones,setZones]=useState<string[]>([]),[now,setNow]=useState(serverNow()),[error,setError]=useState<unknown>(''),[saving,setSaving]=useState(false),[saved,setSaved]=useState(false);
  useEffect(()=>{setDraft(timezone)},[timezone]);
  useEffect(()=>{const timer=setInterval(()=>setNow(serverNow()),1000);return()=>clearInterval(timer)},[]);
  async function loadZones(){if(zones.length)return;try{const value=await api<{timezones:string[];now:string}>('/api/time');setZones(value.timezones);setServerTime(value.now)}catch(e){setError(e);if(isUnauthorized(e))onError(e)}}
  async function save(event:React.FormEvent){event.preventDefault();if(saving)return;setSaving(true);setError('');setSaved(false);try{const value=await api<{timezone:string}>('/api/time',{method:'PUT',body:JSON.stringify({timezone:draft})});setApplicationTimezone(value.timezone);setSaved(true)}catch(e){setError(e);if(isUnauthorized(e))onError(e)}finally{setSaving(false)}}
  return <section className="time-settings"><h3>{t('Appliance time')}</h3><p>{t('Timezone')}: <strong>{timezone}</strong> · {t('Current local time')}: {localClock(now)}</p><details onToggle={event=>{if(event.currentTarget.open)loadZones()}}><summary>{t('Change timezone')}</summary><p className="help">{t('Applies to displayed times, archive dates and every recording schedule. Existing recording timestamps stay unchanged.')}</p><form className="toolbar" onSubmit={save}><label>{t('Timezone')}<input list="installation-timezones" required value={draft} onChange={event=>{setDraft(event.target.value);setSaved(false)}} placeholder="Europe/Moscow" disabled={saving}/></label><datalist id="installation-timezones">{zones.map(zone=><option key={zone} value={zone}/>)}</datalist><button className="primary" disabled={saving}>{t(saving?'Saving…':'Save timezone')}</button></form></details>{!!error&&<Feedback tone="bad">{errorMessage(error)}</Feedback>}{saved&&<Feedback tone="good">{t('Timezone saved. Recording schedules now use this timezone.')}</Feedback>}</section>;
}
