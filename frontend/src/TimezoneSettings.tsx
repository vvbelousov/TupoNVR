import {useEffect,useState} from 'react';
import {api,errorMessage,isUnauthorized} from './api';
import {useLanguage} from './i18n';
import {localClock,setApplicationTimezone,useTimezone,serverNow,setServerTime} from './time';
import {Feedback} from './ui';
import {TimezoneSelector} from './TimezoneSelector';
export function TimezoneSettings({onError}:{onError:(error:unknown)=>void}){
  const {t}=useLanguage(),timezone=useTimezone();
  const [draft,setDraft]=useState(timezone),[zones,setZones]=useState<string[]>([]),[now,setNow]=useState(serverNow()),[error,setError]=useState<unknown>(''),[loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[saved,setSaved]=useState(false);
  useEffect(()=>{setDraft(timezone)},[timezone]);
  useEffect(()=>{const timer=setInterval(()=>setNow(serverNow()),1000);return()=>clearInterval(timer)},[]);
  async function loadZones(){if(zones.length||loading)return;setLoading(true);try{const value=await api<{timezones:string[];now:string}>('/api/time');setZones(value.timezones);setServerTime(value.now)}catch(e){setError(e);if(isUnauthorized(e))onError(e)}finally{setLoading(false)}}
  async function save(event:React.FormEvent){event.preventDefault();if(saving)return;if(!zones.includes(draft)){setError('Unknown IANA timezone');return}setSaving(true);setError('');setSaved(false);try{const value=await api<{timezone:string}>('/api/time',{method:'PUT',body:JSON.stringify({timezone:draft})});setApplicationTimezone(value.timezone);setSaved(true)}catch(e){setError(e);if(isUnauthorized(e))onError(e)}finally{setSaving(false)}}
  return <div className="time-settings"><p>{t('Timezone')}: <strong>{timezone}</strong> · {t('Current local time')}: {localClock(now)}</p><details onToggle={event=>{if(event.currentTarget.open)loadZones()}}><summary>{t('Change timezone')}</summary><p className="help">{t('Applies to displayed times, archive dates and every recording schedule. Existing recording timestamps stay unchanged.')}</p><form className="toolbar" onSubmit={save}><TimezoneSelector value={draft} zones={zones} loading={loading} disabled={saving} onChange={zone=>{setDraft(zone);setSaved(false)}}/><button className="primary" disabled={saving||!zones.includes(draft)}>{t(saving?'Saving…':'Save timezone')}</button></form></details>{!!error&&<Feedback tone="bad">{errorMessage(error)}</Feedback>}{saved&&<Feedback tone="good">{t('Timezone saved. Recording schedules now use this timezone.')}</Feedback>}</div>;
}
