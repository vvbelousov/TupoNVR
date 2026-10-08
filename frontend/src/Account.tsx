import {useRef,useState} from 'react';
import {api,errorMessage,isUnauthorized} from './api';
import {useLanguage} from './i18n';
import {TimezoneSettings} from './TimezoneSettings';
import {Feedback} from './ui';

export function Account({username,onLogout,onError}:{username:string|null;onLogout:()=>void;onError:(error:unknown)=>void}){
  const {language,setLanguage,t}=useLanguage();
  const pending=useRef(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState<unknown>('');
  async function logout(){
    if(pending.current)return;
    pending.current=true;setBusy(true);setError('');
    try{await api('/api/logout',{method:'POST'});onLogout()}
    catch(value){setError(value);if(isUnauthorized(value))onError(value)}
    finally{pending.current=false;setBusy(false)}
  }
  return <div className="account-page"><section><h3>{t('Preferences')}</h3><label className="language-switch">{t('Language')}<select aria-label={t('Language')} value={language} onChange={event=>setLanguage(event.target.value as 'en'|'ru')}><option value="en">English</option><option value="ru">Русский</option></select></label><p className="help">{t('Language is saved in this browser.')}</p><TimezoneSettings onError={onError}/></section><section><h3>{t('Account')}</h3>{username?<><p>{t('Username')}: <strong>{username}</strong></p><button type="button" disabled={busy} onClick={logout}>{t(busy?'Signing out…':'Log out')}</button></>:<p className="help">{t('Authentication is not configured on this appliance.')}</p>}{!!error&&<Feedback tone="bad">{errorMessage(error)}</Feedback>}</section></div>;
}
