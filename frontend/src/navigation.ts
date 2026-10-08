const pages=['Overview','Cameras','Multiview','Archive','Storage'];
export type Route={page:string;cameraId:number|null};
export function readRoute(location:{pathname:string;search:string}):Route{
  const direct=location.pathname.replace(/\/$/,'').slice(1);
  const aliases:Record<string,string>={overview:'Overview',multiview:'Multiview',archive:'Archive',settings:'Storage'};
  if(aliases[direct])return {page:aliases[direct],cameraId:null};
  if(/^\/account\/?$/.test(location.pathname))return {page:'Account',cameraId:null};
  const match=/^\/cameras\/([^/]+)\/live\/?$/.exec(location.pathname);
  if(match){
    const id=/^[1-9]\d*$/.test(match[1])?Number(match[1]):NaN;
    return {page:'Live',cameraId:Number.isSafeInteger(id)?id:null};
  }
  const page=new URLSearchParams(location.search).get('page')||'Overview';
  return {page:pages.includes(page)?page:'Overview',cameraId:null};
}
export const liveURL=(id:number)=>`/cameras/${id}/live`;
export const pageURL=(page:string)=>page==='Account'?'/account':page==='Overview'?'/':`/?page=${encodeURIComponent(page)}`;

export const archiveURL=(id:number,time:number)=>`/archive?camera=${id}&at=${encodeURIComponent(new Date(time).toISOString())}`;
export function archiveTarget(search:string){
  const params=new URLSearchParams(search),id=Number(params.get('camera')),instant=params.get('at')||'',stamp=Date.parse(instant);
  if(!/(Z|[+-]\d{2}:\d{2})$/.test(instant))return null;
  return Number.isSafeInteger(id)&&id>0&&Number.isFinite(stamp)?{id,stamp}:null;
}
