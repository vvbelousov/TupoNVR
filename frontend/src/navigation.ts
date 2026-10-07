const pages=['Overview','Cameras','Multiview','Archive','Storage'];
export type Route={page:string;cameraId:number|null};
export function readRoute(location:{pathname:string;search:string}):Route{
  const match=/^\/cameras\/([^/]+)\/live\/?$/.exec(location.pathname);
  if(match){
    const id=/^[1-9]\d*$/.test(match[1])?Number(match[1]):NaN;
    return {page:'Live',cameraId:Number.isSafeInteger(id)?id:null};
  }
  const page=new URLSearchParams(location.search).get('page')||'Overview';
  return {page:pages.includes(page)?page:'Overview',cameraId:null};
}
export const liveURL=(id:number)=>`/cameras/${id}/live`;
export const pageURL=(page:string)=>page==='Overview'?'/':`/?page=${encodeURIComponent(page)}`;
