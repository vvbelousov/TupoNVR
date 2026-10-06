import {useEffect,useRef} from 'react';
import type {ReactNode} from 'react';

export function Feedback({children,tone='neutral'}:{children:ReactNode;tone?:'neutral'|'good'|'bad'|'warning'}){
  return <div className={`feedback ${tone}`} role={tone==='bad'?'alert':'status'}>{children}</div>;
}
export function EmptyState({title,children,action}:{title:string;children:ReactNode;action?:ReactNode}){
  return <div className="empty-state"><strong>{title}</strong><p>{children}</p>{action}</div>;
}
export function Modal({title,closeLabel,onClose,children}:{title:string;closeLabel:string;onClose:()=>void;children:ReactNode}){
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    const element=dialog.current,opener=document.activeElement;
    element?.showModal();
    return()=>{element?.close();if(opener instanceof HTMLElement&&opener.isConnected)opener.focus()};
  },[]);
  return <dialog ref={dialog} className="modal" aria-label={title} onCancel={onClose} onClick={event=>{if(event.target===event.currentTarget)onClose()}}><div className="modal-header"><strong>{title}</strong><button type="button" onClick={onClose}>{closeLabel}</button></div>{children}</dialog>;
}
