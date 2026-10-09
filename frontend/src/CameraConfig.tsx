import {useRef,useState} from 'react';
import {api,ApiError,errorMessage} from './api';
import {useLanguage} from './i18n';
import {Feedback} from './ui';

type Entry={key:string;name:string;changes:Record<string,{changed?:boolean;before?:unknown;after?:unknown}>};
type Preview={create:Entry[];update:Entry[];unchanged:Entry[];errors:{field:string;message:string}[];conflicts:{key:string;message:string}[];warnings:{key:string;message:string}[];fingerprint?:string};
type Result={created:number;updated:number;unchanged:number;runtime_pending:boolean};

export default function CameraConfig({onImported,onError}:{onImported:()=>Promise<void>;onError:(error:unknown)=>void}){
  const {t:tr}=useLanguage();
  const [include,setInclude]=useState(false),[content,setContent]=useState(''),[preview,setPreview]=useState<Preview|null>(null),[result,setResult]=useState<Result|null>(null),[error,setError]=useState<unknown>(''),[busy,setBusy]=useState(false);
  const selection=useRef(0);
  async function fileSelected(file?:File){
    const epoch=++selection.current;setPreview(null);setResult(null);setError('');setContent('');
    if(!file)return;
    if(file.size>1024*1024){setError(new Error('YAML exceeds 1 MiB'));return}
    try{const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(epoch===selection.current){if(!text.trim())setError(new Error('Empty YAML file'));else setContent(text)}}catch{if(epoch===selection.current)setError(new Error('Invalid UTF-8 YAML file'))}
  }
  async function run(apply=false){
    setBusy(true);setError('');setResult(null);
    try{
      const options={method:'POST',body:content,headers:{'Content-Type':'application/yaml',...(apply?{'X-Confirm-Import':'true','X-Import-Fingerprint':preview?.fingerprint||''}:{})}};
      if(apply){const value=await api<Result>('/api/camera-config/apply',options);setResult(value);setPreview(null);await onImported()}
      else setPreview(await api<Preview>('/api/camera-config/preview',options));
    }catch(e){setError(e);if(apply)setPreview(null);onError(e)}finally{setBusy(false)}
  }
  async function download(){
    setBusy(true);setError('');
    try{
      const response=await fetch(`/api/camera-config/export?include_credentials=${include}`);
      if(!response.ok){let detail='Request failed';try{detail=(await response.json()).detail}catch{}throw new ApiError(response.status,detail)}
      const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download='cameras.yaml';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){setError(e);onError(e)}finally{setBusy(false)}
  }
  const valid=preview?.fingerprint&&!preview.errors.length&&!preview.conflicts.length;
  return <section className="camera-config"><h3>{tr('Import / Export')}</h3><p className="help">{tr('Back up or migrate camera settings with YAML. Imports never delete cameras.')}</p>
    <div className="actions"><button disabled={busy} onClick={download}>{tr('Export YAML')}</button><label className="smallcheck"><input type="checkbox" disabled={busy} checked={include} onChange={e=>setInclude(e.target.checked)}/>{tr('Include credentials')}</label></div>
    {include&&<Feedback tone="bad">{tr('This file includes passwords and URL tokens. Store it securely and share it only with trusted people.')}</Feedback>}
    <label>{tr('YAML file')}<input type="file" accept=".yaml,.yml,application/yaml,text/yaml" disabled={busy} onChange={e=>fileSelected(e.target.files?.[0])}/></label>
    <div className="actions"><button disabled={busy||!content} onClick={()=>run()}>{tr('Preview changes')}</button>{valid&&<button className="primary" disabled={busy} onClick={()=>run(true)}>{tr('Confirm import')}</button>}</div>
    {!!error&&<Feedback tone="bad">{errorMessage(error)}</Feedback>}
    {busy&&<Feedback>{tr('Working…')}</Feedback>}
    {preview&&<div aria-live="polite"><p>{tr('Create: {create} · Update: {update} · Unchanged: {unchanged}',{create:preview.create.length,update:preview.update.length,unchanged:preview.unchanged.length})}</p>
      {(['create','update','unchanged'] as const).map(action=>preview[action].map(entry=><details key={entry.key}><summary>{tr(action)}: {entry.name} ({entry.key})</summary>{Object.entries(entry.changes).map(([field,value])=><p key={field}><strong>{tr(field)}</strong>: {value.changed?tr('Changed (hidden)'):`${JSON.stringify(value.before)} → ${JSON.stringify(value.after)}`}</p>)}</details>))}
      {preview.errors.map((e,i)=><Feedback tone="bad" key={`e${i}`}>{e.field}: {tr(e.message)}</Feedback>)}
      {preview.conflicts.map((e,i)=><Feedback tone="bad" key={`c${i}`}>{e.key}: {tr(e.message)}</Feedback>)}
      {preview.warnings.map((e,i)=><Feedback key={`w${i}`}>{e.key}: {tr(e.message)}</Feedback>)}
    </div>}
    {result&&<Feedback tone="good">{tr('Imported: {created} created · {updated} updated · {unchanged} unchanged',{created:result.created,updated:result.updated,unchanged:result.unchanged})}</Feedback>}
    {result?.runtime_pending&&<Feedback tone="bad">{tr('Settings saved. Camera activation is pending; automatic reconciliation will retry.')}</Feedback>}
  </section>
}
