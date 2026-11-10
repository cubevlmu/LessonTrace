import {useMemo} from 'react';
import type {Track,WindowResult} from '../models/backend';
import type {VisualItem} from '../models/view';
import {fmt} from '../models/view';
import {actionSpans} from '../adapters/timeline';
import {coverageIntervals,coverageNames} from '../adapters/coverage';
import {personTrackLabel,type IdentityLabel} from '../api/workbench';
import {Icon} from './Icon';
interface Props {duration:number;time:number;items:VisualItem[];selectedId?:string;onSelect:(item:VisualItem)=>void;onSeek:(t:number)=>void;tracks?:Track[];windows?:WindowResult[];selectedTrack?:number;identityLabels?:IdentityLabel[];onSelectTrack?:(id:number)=>void;offset?:number;singlePerson?:boolean;coverageJobId?:string}
export function EventTimeline({duration,time,items,onSelect,onSeek,tracks=[],windows=[],selectedTrack=0,identityLabels=[],onSelectTrack,offset=0,singlePerson=false,coverageJobId}:Props){
 const d=Math.max(duration,.01),pct=(t:number)=>`${Math.max(0,Math.min(100,100*t/d))}%`;
 const spans=useMemo(()=>actionSpans(windows,items,tracks),[windows,items,tracks]);
 const coverage=useMemo(()=>coverageIntervals(tracks,coverageJobId?windows.filter(w=>w.job_id===coverageJobId):windows,offset),[tracks,windows,offset,coverageJobId]);
 const placed=useMemo(()=>{const ends=new Map<number,number[]>();return spans.map(s=>{const lanes=ends.get(s.trackId)||[];let row=lanes.findIndex(end=>end<s.start);if(row<0)row=lanes.length;lanes[row]=Math.max(s.end,s.start+d*.1);ends.set(s.trackId,lanes);return {...s,row};});},[spans,d]);
 const rows=tracks.filter(t=>!singlePerson||!selectedTrack||t.track_id===selectedTrack);
 return <section className="tl card"><div className="card__head"><div className="card__title"><Icon name="timeline" size={15}/>人物与动作时间线 <span className="muted">真实录像 {fmt(duration)}</span></div></div>
  <div className="card__body"><div className="timeline-legend"><span className="legend-visible">人物可见区间</span><span className="legend-analyzed">取证窗口</span><span className="legend-estimate">AI状态估计（虚线）</span><span className="legend-human">教师确认区间</span></div>
   <div className="person-timeline__ruler"><span>人物</span><div>{Array.from({length:6},(_,i)=>d*i/5).map(t=><small key={t} style={{left:pct(t),transform:t===d?'translateX(-100%)':undefined}}>{fmt(t)}</small>)}</div></div>
   <div className="person-timeline" aria-label="人物动作区间">
    {rows.map(track=><div className={'person-timeline__row'+(track.track_id===selectedTrack?' is-selected':'')} key={track.track_id}>
     <button className="person-timeline__name" onClick={()=>onSelectTrack?.(track.track_id)} title={personTrackLabel(identityLabels,track.track_id,time)}>{personTrackLabel(identityLabels,track.track_id,time)}</button>
     <div className="person-timeline__lane" style={{height:Math.max(60,55+22*Math.max(0,...placed.filter(s=>s.trackId===track.track_id).map(s=>s.row)))}}>
      {track.segments.map((s,i)=><button key={'visible'+i} className="person-timeline__visible" style={{left:pct(s.local_start),width:pct(s.local_end-s.local_start),minWidth:2}} onClick={()=>{onSelectTrack?.(track.track_id);onSeek(s.local_start);}} title={`人物实际可见：${fmt(s.local_start)}–${fmt(s.local_end)}；空档不补位置`}/>)}
      {coverage.filter(c=>c.trackId===track.track_id).map((c,index)=><button key={'coverage'+index} className={'timeline-coverage is-'+c.state} style={{left:pct(c.start),width:pct(c.end-c.start),minWidth:2}} onClick={()=>{onSelectTrack?.(track.track_id);onSeek(c.start);}} title={`${coverageNames[c.state]} ${fmt(c.start)}–${fmt(c.end)}；取证覆盖不等于逐帧动作准确率`}><span>{coverageNames[c.state]}</span></button>)}
      {placed.filter(s=>s.trackId===track.track_id).map(s=><button className={'person-timeline__action is-'+s.kind+(s.start/d>.8?' is-late':'')} key={s.id} style={{left:pct(s.start),width:pct(s.end-s.start),top:33+s.row*22,minWidth:s.kind==='point'?5:14}} title={`${s.title} · ${fmt(s.start)}–${fmt(s.end)} · ${s.kind==='human'?'教师确认':s.kind==='estimate'?`AI估计，依据${s.samples.length}个取证时刻；非逐帧动作边界`:'单次观察'} `} onClick={()=>{const item=items.find(i=>i.id===s.itemId);if(item)onSelect(item);onSeek(s.start);}}><span>{s.title}</span></button>)}
      <div className="person-timeline__playhead" style={{left:pct(time)}}/>
     </div>
    </div>)}
    {!rows.length&&<p className="muted">人物跟踪完成后显示可见区间。</p>}
   </div>
   <input aria-label="时间线定位" className="product-seek" type="range" min={0} max={d} step=".001" value={Math.min(time,d)} onChange={e=>onSeek(Number(e.target.value))}/>
   <p className="faint">每行包含该人物全部出场区间。灰色为待分析，蓝色为已取证；动作保留实际证据时间，多项动作分行显示。虚线表示AI区间估计，非逐帧确认。</p>
  </div>
 </section>;
}
