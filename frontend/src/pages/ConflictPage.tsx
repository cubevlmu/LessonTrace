import {useMemo,useState} from 'react';
import {useSession} from '../state/session';
import {visualItems} from '../adapters/event';
import {CorrectionPanel} from '../components/CorrectionPanel';
import {EvidencePanel} from '../components/EvidencePanel';
import {Link} from 'react-router-dom';
export function ConflictPage(){
 const s=useSession();const items=useMemo(()=>visualItems(s.data.events,s.data.windows),[s.data.events,s.data.windows]);const [id,setId]=useState('');const item=items.find(x=>x.id===id)||items[0];
 return <div className="page"><header className="page__head"><div><h1 className="page__title">教师确认与纠错</h1><p className="page__meta">AI观察、AI建议和教师确认分别保存。修改可撤销，影响报告时需重算。</p></div></header><Link to="/review">返回视频工作台看片</Link><select aria-label="选择纠错事件" value={item?.id||''} onChange={e=>setId(e.target.value)}><option value="">选择事件</option>{items.map(i=><option key={i.id} value={i.id}>Track {i.trackId} · {i.localStart.toFixed(2)}秒 · {i.category}</option>)}</select>{!items.length&&<p className="muted">暂无已保存观察，请先完成视频分析。</p>}<CorrectionPanel item={item}/><EvidencePanel item={item} onSeek={t=>{window.location.href=`/app/review?t=${t}`;}}/></div>;
}
