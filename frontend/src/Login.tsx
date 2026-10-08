import {useRef,useState} from 'react';
import {api,isUnauthorized} from './api';
import {useLanguage} from './i18n';
import {Feedback} from './ui';

export function Login({onSuccess}:{onSuccess:()=>Promise<void>}){
  const {t}=useLanguage();
  const pending=useRef(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(event:React.FormEvent<HTMLFormElement>){
    event.preventDefault();
    if(pending.current)return;
    const form=new FormData(event.currentTarget);
    pending.current=true;setBusy(true);setError('');
    try{
      await api('/api/login',{method:'POST',body:JSON.stringify({username:form.get('user'),password:form.get('pass')})});
      await onSuccess();
    }catch(value){setError(isUnauthorized(value)?'Invalid credentials':'Unable to sign in. Please try again.')}
    finally{pending.current=false;setBusy(false)}
  }
  return <main className="login-page"><div className="login-content"><div className="login-brand"><h1><span aria-hidden="true">▣ </span>TupoNVR</h1><p>{t('A quiet video recorder')}</p></div><section className="login-panel" aria-labelledby="login-title"><h2 id="login-title">{t('Sign in to your NVR')}</h2><p className="help">{t('Use the credentials configured on this appliance.')}</p><form onSubmit={submit} className="form" aria-busy={busy}><label>{t('Username')}<input required autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} name="user" disabled={busy}/></label><label>{t('Password')}<input required autoComplete="current-password" name="pass" type="password" disabled={busy}/></label>{error&&<Feedback tone="bad">{t(error)}</Feedback>}<button type="submit" className="primary" disabled={busy}>{t(busy?'Signing in…':'Sign in')}</button></form></section></div></main>;
}
