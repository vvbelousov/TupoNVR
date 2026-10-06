import {t} from './i18n';
export class ApiError extends Error {
  constructor(public status:number,public detail:unknown){super(`HTTP ${status}`)}
}
export const isUnauthorized=(error:unknown)=>error instanceof ApiError&&error.status===401;
export function errorMessage(error:unknown):string {
  if(error instanceof ApiError){
    if(error.status===401)return t(typeof error.detail==='string'?error.detail:'Authentication required');
    const detail=Array.isArray(error.detail)?error.detail.map(value=>`${(value.loc||[]).filter((key:string)=>key!=='body').join('.')}: ${t(String(value.msg||'Request failed').replace(/^Value error, /,''))}`).join('; '):t(typeof error.detail==='string'?error.detail:'Request failed');
    return `${error.status}: ${detail}`;
  }
  if(error instanceof TypeError)return t('Network request failed');
  return t(error instanceof Error?error.message:String(error));
}
export async function api<T>(path:string, opts:RequestInit = {}):Promise<T> {
  const res = await fetch(path, {...opts, headers:{...(opts.body?{'Content-Type':'application/json'}:{}),...opts.headers}});
  if(!res.ok){let detail:unknown;try{detail=(await res.json()).detail}catch{detail='Request failed'}throw new ApiError(res.status,detail)}
  return res.status===204 ? undefined as T : res.json();
}
