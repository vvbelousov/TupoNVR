export function fullscreenSupported(element:HTMLElement):boolean {
  const document=element.ownerDocument;
  return document.fullscreenEnabled!==false&&typeof element.requestFullscreen==='function'&&typeof document.exitFullscreen==='function';
}

export async function toggleFullscreen(element:HTMLElement):Promise<void> {
  if(!fullscreenSupported(element))return;
  if(element.ownerDocument.fullscreenElement===element)await element.ownerDocument.exitFullscreen();
  else await element.requestFullscreen();
}
