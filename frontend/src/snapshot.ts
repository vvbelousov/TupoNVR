export async function captureFrame(video:HTMLVideoElement):Promise<Blob>{
  if(video.readyState<2||!video.videoWidth||!video.videoHeight)throw new Error('No decoded frame available. Wait for playback or check browser codec support.');
  const canvas=document.createElement('canvas');
  canvas.width=video.videoWidth;canvas.height=video.videoHeight;
  const context=canvas.getContext('2d');
  if(!context)throw new Error('Frame capture is unsupported by this browser.');
  try{
    context.drawImage(video,0,0);
    return await new Promise<Blob>((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('Frame capture failed.')),'image/png'));
  }catch{throw new Error('Frame capture is blocked by browser security or codec support.');}
}
export function saveFrame(blob:Blob){
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=`snapshot-${new Date().toISOString().replace(/[:.]/g,'-')}.png`;
  link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
