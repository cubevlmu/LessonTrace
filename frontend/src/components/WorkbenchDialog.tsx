import {useEffect,useRef,type ReactNode} from 'react';
export function WorkbenchDialog({title,children,onClose}:{title:string;children:ReactNode;onClose:()=>void}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{ref.current?.showModal();return()=>ref.current?.close();},[]);
 return <dialog ref={ref} className="classroom-dialog workbench-dialog" aria-label={title} onCancel={onClose}><header><h2>{title}</h2><button className="btn btn--ghost" onClick={onClose}>关闭</button></header>{children}</dialog>;
}
