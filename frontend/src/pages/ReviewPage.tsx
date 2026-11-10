import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useSession } from "../state/session";
import { visualItems } from "../adapters/event";
import {startJob,controlJob} from '../api/jobs';
import {getRunResults,getTrackGroups,saveTrackGroup,revokeTrackGroup,getFullPlan,type TrackGroup} from '../api/workbench';
import {canonicalTrack,memberTracks,groupedTracks,groupedObservations,presentationLabels} from '../adapters/trackGroups';
import {WorkbenchDialog} from '../components/WorkbenchDialog';
import type {DiscoveredEvent,WindowResult} from '../models/backend';
import {getPlaybackObservations,getPlaybackIdentities,personLabel,personTrackLabel,type WorkbenchIdentity} from '../api/workbench';
import { videoUrl } from "../api/videos";
import type { TrackObservation } from "../models/backend";
import { fmt,videoTitle,observationView,observationBasis,showObservation } from "../models/view";
import { SessionBar } from "../components/SessionBar";
import { VideoStage } from "../components/VideoStage";
import { EventTimeline } from "../components/EventTimeline";
import { EvidencePanel } from "../components/EvidencePanel";
import {IdentityPanel} from '../components/IdentityPanel';
import {CorrectionPanel} from '../components/CorrectionPanel';
import {PersonProfiles} from '../components/PersonProfiles';
import {useTrackQuality} from '../components/TrackQuality';
export function ReviewPage() {
  const s = useSession(),
    [params] = useSearchParams();
  const [saved,setSaved]=useState<{run:string;events:DiscoveredEvent[];windows:WindowResult[];truncated:boolean}|null>(null);
  const [groups,setGroups]=useState<TrackGroup[]>([]),[groupInput,setGroupInput]=useState(''),[groupReason,setGroupReason]=useState(''),[groupBusy,setGroupBusy]=useState(false);
  const [dialog,setDialog]=useState<'run'|'identity'|'groups'|'full'|null>(null),[sideTab,setSideTab]=useState<'evidence'|'correction'|'help'>('evidence');
  const [fullPlan,setFullPlan]=useState<Awaited<ReturnType<typeof getFullPlan>>|null>(null);
  const runId=s.data.job?.run_id;
  const fullJob=s.data.jobs.find(j=>j.run_id===runId&&j.config_and_runtime.config.analysis_mode==='full_timeline');
  useEffect(()=>{setGroups([]);setSaved(null);},[runId]);
  const allWindows=saved&&saved.run===runId?saved.windows:s.data.windows;
  const allEvents=saved&&saved.run===runId?saved.events:s.data.events;
  useEffect(()=>{
    let active=true;const run=s.data.job?.run_id;if(!run){setSaved(null);setGroups([]);return;}
    const load=()=>Promise.all([getRunResults(run),getTrackGroups(run)]).then(([r,g])=>{if(active){setSaved({...r,run});setGroups(g);}}).catch(e=>{if(active)s.setError(String(e));});
    void load();window.addEventListener('focus',load);const timer=setInterval(load,15000);
    return()=>{active=false;clearInterval(timer);window.removeEventListener('focus',load);};
  },[runId,s.data.job?.processed_windows,s.data.events]);
  const items = useMemo(
    () => visualItems(allEvents, allWindows,s.data.scope?.start),
    [allEvents, allWindows,s.data.scope?.start],
  );
  const [time, setTime] = useState(0),
    [selected, setSelected] = useState(""),
    [track, setTrack] = useState(0),
    [obs, setObs] = useState<TrackObservation[]>([]);
  const [boxMode,setBoxMode]=useState<'all'|'single'|'hidden'>('all'),[identity,setIdentity]=useState<WorkbenchIdentity|null>(null),[expanding,setExpanding]=useState(false),[expandError,setExpandError]=useState('');
  // An anonymous display group must not spread a binding to a different raw track.
  const labels=useMemo(()=>identity?presentationLabels([...identity.bindings,...identity.track_labels,...identity.candidates],groups):[],[identity,groups]);
  const [filter,setFilter]=useState('all');
  const quality=useTrackQuality(s.data.job?.run_id),[includePartial,setIncludePartial]=useState(true);
  const excluded=new Set(quality?.tracks.filter(t=>t.possible_nested_with!==null||t.quality_flags.includes('short_lived_region')).map(t=>t.track_id)||[]);
  const visibleObs=includePartial||boxMode==='single'?obs:obs.filter(o=>!excluded.has(o.track_id));
  const members=memberTracks(track,groups);
  useEffect(()=>{setTrack(id=>canonicalTrack(id,groups));},[groups]);
  const displayTracks=useMemo(()=>groupedTracks(s.data.tracks,groups),[s.data.tracks,groups]);
  const shown=items.filter(i=>showObservation(i,filter)&&(boxMode!=='single'||!track||members.includes(observationView(i).trackId)));
  const timelineWindows=allWindows.map(w=>({...w,track_id:canonicalTrack(w.track_id,groups),segment_id:w.track_id*100000+Number(w.segment_id||0)}));
  const timelineItems=items.map(i=>({...i,trackId:canonicalTrack(i.trackId,groups),humanCorrection:i.humanCorrection?{...i.humanCorrection,track_id:canonicalTrack(i.humanCorrection.track_id,groups)}:i.humanCorrection}));
  const video = s.data.videos.find((v) => v.video_id === s.data.videoId);
  const duration = video?.metadata.duration || 0;
  const newerContinuous=s.data.jobs.find(j=>j.job_id!==s.data.job?.job_id&&j.run_id===s.data.job?.run_id&&j.config_and_runtime.reused_tracking_run_id&&Date.parse(String(j.created_at))>Date.parse(String(s.data.job?.created_at||''))&&!['failed','cancelled'].includes(j.status));
  const item = shown.find((i) => i.id === selected) || (track&&boxMode==='single'?[...shown].sort((a,b)=>Math.abs(a.localStart-time)-Math.abs(b.localStart-time))[0]:shown[0]);
  useEffect(() => {
    const requested = Number(params.get("t") || 0);
    setTime(
      Number.isFinite(requested) && requested >= 0 && requested <= duration
        ? requested
        : 0,
    );
    const tid = Number(params.get("track") || 0);
    setTrack(tid > 0 ? tid : 0);
    if (tid > 0) {
      const sourceItem =
        items.find(
          (i) =>
            i.trackId === tid &&
            i.frames.some((f) => Math.abs(f.local_time - requested) < 0.002),
        ) || items.find((i) => i.trackId === tid);
      if (sourceItem) setSelected(sourceItem.id);
    }
  }, [params, duration]);
  useEffect(() => {
    let active = true;
    if (s.data.job?.run_id&&duration>0)
      void getPlaybackObservations(s.data.job.run_id,Math.max(0,Math.floor(time/5)*5-.2),Math.min(duration,(Math.floor(time/5)+1)*5+.2))
        .then((v) => {
          if (active) setObs(v);
        })
        .catch((e) => {
          if (active) {setObs([]);s.setError(String(e));}
        });
    return () => {
      active = false;
    };
  }, [s.data.job?.run_id,Math.floor(time/5),duration]);
  useEffect(()=>{setObs([]);setSelected('');setBoxMode('all');setTrack(Number(params.get('track'))||0);},[s.data.job?.run_id]);
  useEffect(()=>{
    let active=true;setIdentity(null);const run=s.data.job?.run_id;
    const load=()=>{if(run)void getPlaybackIdentities(run).then(result=>{if(active)setIdentity(result);}).catch(e=>{if(active){setIdentity(null);s.setError(String(e));}});};
    load();window.addEventListener('focus',load);window.addEventListener('lessontrace:identity-changed',load);const timer=setInterval(load,15000);
    return()=>{active=false;clearInterval(timer);window.removeEventListener('focus',load);window.removeEventListener('lessontrace:identity-changed',load);};
  },[s.data.job?.run_id]);
  function selectTrack(id:number){
    const canonical=canonicalTrack(id,groups);setTrack(canonical);setBoxMode(canonical?'single':'all');setSelected('');
    const person=displayTracks.find(t=>t.track_id===canonical);
    if(person&&!person.segments.some(seg=>seg.local_start<=time&&time<=seg.local_end))setTime(person.segments[0]?.local_start||0);
  }
  async function editGroup(revoke?:string){
    if(!runId)return;setGroupBusy(true);setExpandError('');
    try{if(revoke)await revokeTrackGroup(revoke);else{const ids=groupInput.split(/[,，、\s]+/).filter(Boolean).map(Number);if(ids.some(id=>!Number.isInteger(id)||id<1))throw Error('请输入正确的轨迹编号');await saveTrackGroup(runId,ids,groupReason);setGroupInput('');setGroupReason('');}setGroups(await getTrackGroups(runId));setTrack(0);setBoxMode('all');}
    catch(e){setExpandError(String(e));}finally{setGroupBusy(false);}
  }
  async function expandTrack(){
    if(!s.data.job?.run_id||!track||!video)return;setExpanding(true);setExpandError('');
    try{
      const selectedTrack=displayTracks.find(t=>t.track_id===track);if(!selectedTrack)throw Error('请先选择人物');
      const planned=selectedTrack.segments.reduce((sum,seg)=>sum+Math.max(1,Math.ceil((seg.local_end-seg.local_start)/8)),0),count=Math.min(24,planned);
      if(!count)throw Error('该人物尚无可分析的实际可见区间');
      const previous=s.data.job.config_and_runtime.config as Record<string,unknown>|undefined;
      const result=await startJob(video.video_id,{...previous,track_ids:members,max_tracks:members.length,max_windows:count,max_calls:count*2,budget_seconds:3600,window_seconds:8,overlap:0,frames:2},s.data.job.run_id);
      await s.refresh();await s.loadJob(result.job.job_id);
    }catch(e){setExpandError(String(e));}finally{setExpanding(false);}
  }
  function choose(id: string) {
    const i = items.find((v) => v.id === id);
    if (!i) return;
    setSelected(id);
    setTime(observationView(i).localStart);
    setTrack(canonicalTrack(observationView(i).trackId,groups));
  }
  async function observeClearPeople(){
    if(!video||!s.data.job?.run_id)return;setExpanding(true);setExpandError('');
    try{const result=await startJob(video.video_id,{device:'cpu',prefer_clear_targets:true,max_tracks:3,max_windows:6,max_calls:12,frames:2,input_long_edge:384,timeout:240,budget_seconds:900},s.data.job.run_id);await s.refresh();await s.loadJob(result.job.job_id);}catch(e){setExpandError(String(e));}finally{setExpanding(false);}
  }
  async function openFull(){setDialog('full');setFullPlan(null);setExpandError('');if(runId)try{setFullPlan(await getFullPlan(runId));}catch(e){setExpandError(String(e));}}
  async function startFull(){
    if(!video||!runId)return;setExpanding(true);setExpandError('');
    try{const result=await startJob(video.video_id,{analysis_mode:'full_timeline',timeline_window_seconds:2,frames:4,input_long_edge:512,device:'cpu',timeout:240,budget_seconds:900,prefer_clear_targets:false},runId);
      if(result.job.status==='paused')await controlJob(result.job.job_id,'resume');
      await s.refresh();await s.loadJob(result.job.job_id);setTrack(0);setBoxMode('all');setDialog(null);
    }catch(e){setExpandError(String(e));}finally{setExpanding(false);}
  }
  return (
    <div className="page page--review">
      <header className="page__head">
        <div className="page__head-main">
          <h1 className="page__title">
            {videoTitle(video)}
          </h1>
          <div className="page__meta">
            <span>单段录像 · {duration.toFixed(2)}秒</span>
            <span>AI近似识别 · 点击时间线定位回放</span>
          </div>
        </div>
        <Link className="btn btn--primary" to="/report">
          生成报告
        </Link>
      </header>
      <div className="workbench-toolbar">
        <span className="badge badge--observation">{({completed:'分析已保存',completed_with_errors:'分析已保存（含异常）',running:'分析中',queued:'等待分析',paused:'已暂停',pausing:'正在暂停',failed:'分析需重试',cancelled:'已取消'} as Record<string,string>)[s.data.job?.status||'']||'请选择课堂'}</span>
        <span className="muted">{identity?.classroom?.name||'匿名课堂'} · {displayTracks.length}组轨迹 · {items.length}条画面记录</span>
        <button className="btn btn--ghost btn--sm" onClick={()=>window.dispatchEvent(new Event('lessontrace:choose-lesson'))}>切换课堂</button>
        <button className="btn btn--secondary btn--sm" onClick={()=>setDialog('identity')}>人物与班级</button>
        <button className="btn btn--secondary btn--sm" onClick={()=>setDialog('groups')}>整理重复人物</button>
        <button className="btn btn--ghost btn--sm" onClick={()=>setDialog('run')}>分析详情</button>
        {s.canWrite&&runId&&<button className="btn btn--primary btn--sm" onClick={()=>void openFull()}>全轨迹全时段分析</button>}
        {fullJob&&!['completed','completed_with_errors','cancelled'].includes(fullJob.status)&&<div className="full-analysis-progress"><strong>全量进度 {fullJob.processed_windows}/{fullJob.total_windows||'待规划'}个窗口</strong><progress max={fullJob.total_windows||1} value={fullJob.processed_windows}/><button className="btn btn--ghost btn--sm" onClick={()=>void s.loadJob(fullJob.job_id)}>查看全量任务</button></div>}
      </div>
      {dialog==='full'&&<WorkbenchDialog title="全轨迹全时段分析" onClose={()=>setDialog(null)}>
        <p>逐段分析所有已检测人物的每个可见区间，不再只选少量人物。默认每2秒规划一个窗口，最多4张真实帧，保持单并发。</p>
        {!fullPlan&&!expandError&&<p role="status">正在统计全部人物与可见时段…</p>}
        {fullPlan&&<><p><strong>{fullPlan.selected_tracks}条原始轨迹 · {fullPlan.selected_windows}个窗口 · 最多{fullPlan.maximum_model_requests}次模型请求</strong></p><p>可见轨迹累计{fullPlan.visible_track_seconds.toFixed(1)}秒；同一时刻的不同人物分别计算，轨迹数不是学生人数。</p>{fullPlan.rough_processing_seconds!==null&&<p className="src-note">按之前本机任务速度粗略估算约{(fullPlan.rough_processing_seconds/3600).toFixed(1)}小时。这是历史任务参考，设备和帧数不同，不代表本次完成时间。任务可后台运行、暂停和恢复。</p>}<p>全量覆盖不等于逐帧准确识别。遮挡、漏检和动作歧义会单独标明；不会用已有动作填满未分析时段。</p></>}
        {expandError&&<p role="alert">{expandError}</p>}
        <button className="btn btn--primary" disabled={!fullPlan||expanding||!s.canWrite} onClick={()=>void startFull()}>{expanding?'正在创建全量任务…':'开始全量分析'}</button>
      </WorkbenchDialog>}
      {dialog==='run'&&<WorkbenchDialog title="分析详情" onClose={()=>setDialog(null)}><p className="muted">以下进度与预算仅属于当前任务；工作台时间线已汇总同次跟踪下各任务保存的观察。</p><SessionBar />
      {saved?.truncated&&<p role="status">本次最多显示2000个保存窗口；当前列表不是全部分析范围。</p>}
      {quality&&<div className="workbench-identity-note"><span>{quality.raw_track_count}条原始人物区域轨迹，不是学生人数；{quality.preferred_region_count}条适合优先观察动作。</span><label><input type="checkbox" checked={includePartial} onChange={e=>setIncludePartial(e.target.checked)}/>显示可能重复及短暂区域</label><details><summary>为什么后脑勺也会有框？</summary><p>检测器会记录局部或被遮挡的人物区域，轨迹编号不代表独立学生。嵌套区域只标为可能重复，不自动合并身份；默认优先显示稳定区域，原始记录仍可展开。</p></details><button className="btn btn--secondary btn--sm" disabled={expanding||!s.canWrite} onClick={()=>void observeClearPeople()}>补充清晰人物观察</button></div>}
      {newerContinuous&&<div className="workbench-identity-note"><span>该录像有新的连续人物分析：{newerContinuous.processed_windows}/{newerContinuous.total_windows}个窗口已保存。</span><button className="btn btn--secondary btn--sm" disabled={s.loading} onClick={()=>void s.loadJob(newerContinuous.job_id)}>{newerContinuous.status==='completed'?'查看最新连续分析':'查看连续分析进度'}</button></div>}
      {identity?.classroom&&<div className="workbench-identity-note"><span>{identity.classroom.name}</span><span>{identity.track_labels.length?`${identity.track_labels.length}个班级轨迹登记区间`:identity.matching_status==='references_only_no_identity_enrollment'?'可从人物截图登记到班级，或提前上传获授权的照片匹配':identity.bindings.length?`${identity.bindings.length}个已确认身份区间`:'尚无当前有效的姓名关联'}</span><Link to={'/classrooms?classroom='+identity.classroom.classroom_id}>查看学生资料</Link></div>}
</WorkbenchDialog>}
      {dialog==='identity'&&<WorkbenchDialog title="人物与班级" onClose={()=>setDialog(null)}><IdentityPanel classroomId={identity?.classroom?.classroom_id} currentTime={time} selectedTrack={track}/></WorkbenchDialog>}
      {dialog==='groups'&&<WorkbenchDialog title="整理重复人物" onClose={()=>setDialog(null)}>
        <p>把同一人物的重复区域或断裂轨迹归在一起查看。保留原编号、可见空档和原始观察；归组不自动确认真实身份。</p>
        {groups.map(g=><div className="card card__body" key={g.group_id}><strong>人物 {String(g.track_ids[0]).padStart(3,'0')}</strong><p>原始轨迹 {g.track_ids.join('、')} · {g.status==='demo_spatial_association'?'演示空间归组':'操作者归组'}</p><p>{g.reason}</p>{s.canWrite&&<button className="btn btn--ghost" disabled={groupBusy} onClick={()=>void editGroup(g.group_id)}>撤销归组</button>}</div>)}
        {!groups.length&&<p className="muted">尚未归组。请先回放核对，再填写需要合并显示的编号。</p>}
        {s.canWrite&&<form className="classroom-form" onSubmit={e=>{e.preventDefault();void editGroup();}}><label>原始轨迹编号<input aria-label="归组轨迹编号" value={groupInput} onChange={e=>setGroupInput(e.target.value)} placeholder="例如：55, 70, 79" required/></label><label>归组依据<textarea aria-label="归组依据" value={groupReason} onChange={e=>setGroupReason(e.target.value)} minLength={5} required/></label><button className="btn btn--primary" disabled={groupBusy}>保存人物归组</button></form>}
        {expandError&&<p role="alert">{expandError}</p>}
      </WorkbenchDialog>}
      <div className="review-grid">
        <div className="review-grid__main">
          <VideoStage
            src={video ? videoUrl(video.video_id) : undefined}
            time={time}
            duration={duration}
            onTime={setTime}
            onSeek={setTime}
            observations={groupedObservations(visibleObs,groups)}
            selectedTrack={track} boxMode={boxMode} identityLabels={labels} onSelectTrack={selectTrack}
          />
          <section className="card">
            <div className="card__head">
              <h2 className="card__title">人物轨迹</h2>
              <select
                aria-label="人物轨迹"
                value={track}
                onChange={e=>selectTrack(Number(e.target.value))}
              >
                <option value="0">全部人物</option>
                {displayTracks.map((t) => (
                  <option key={t.track_id} value={t.track_id}>
                    {personTrackLabel(labels,t.track_id,time)}{memberTracks(t.track_id,groups).length>1?'（含'+memberTracks(t.track_id,groups).join('、')+'）':''}
                  </option>
                ))}
              </select>
            </div>
            <div className="workbench-box-controls" role="group" aria-label="人物框显示"><button className="chip" aria-pressed={boxMode==='all'} onClick={()=>setBoxMode('all')}>显示全部框</button><button className="chip" aria-pressed={boxMode==='single'} disabled={!track} onClick={()=>setBoxMode('single')}>只看选中人物</button><button className="chip" aria-pressed={boxMode==='hidden'} onClick={()=>setBoxMode('hidden')}>隐藏框</button>{track>0&&s.canWrite&&<button className="btn btn--primary btn--sm" disabled={expanding||['running','queued','pausing','cancelling'].includes(s.data.job?.status||'')} onClick={()=>void expandTrack()}>{expanding?'正在创建任务…':'扩展此人物的连续动作分析'}</button>}</div>
            {expandError&&<p role="alert" className="upload__error">{expandError}</p>}
            <div className="card__body">
              {s.data.tracks.length ? (
                <>
                  <p className="muted">选择人物，查看其出现区间与已保存动作。{members.length>1?`当前人物包含原始轨迹 ${members.join('、')}。`:''}</p>
                  <details><summary>人物可见区间与轨迹详情</summary>{displayTracks
                    .filter((t) => t.track_id === track)
                    .map((t) => (
                      <div key={t.track_id}>
                        <p>
                          {personTrackLabel(labels,t.track_id,time)} · {t.observed_frame_count}
                          次实际观测 · 未观测间隔不补位置
                        </p>
                        {t.segments.map((seg, i) => (
                          <button
                            className="chip"
                            key={i}
                            onClick={() => setTime(seg.local_start)}
                          >
                            {fmt(seg.local_start)}—{fmt(seg.local_end)}
                          </button>
                        ))}
                      </div>
                    ))}</details>
                </>
              ) : (
                <p className="muted">
                  尚无已保存人物轨迹。无人场景、尚未完成跟踪或处理异常需分别查看任务状态。
                </p>
              )}
            </div>
          </section>
          <EventTimeline
            duration={duration}
            time={time}
            items={timelineItems}
            selectedId={item?.id}
            onSelect={(i) => choose(i.id)}
            onSeek={setTime}
            tracks={displayTracks} windows={timelineWindows} selectedTrack={track} identityLabels={labels} onSelectTrack={selectTrack} singlePerson={boxMode==='single'} offset={s.data.scope?.start||0} coverageJobId={fullJob?.job_id}
          />
          {s.data.job?.run_id&&<PersonProfiles excludedTrackIds={includePartial?[]:[...excluded]} trackGroups={groups} runId={s.data.job.run_id} currentTime={time} selectedTrack={track} labels={labels} classroomId={identity?.classroom?.classroom_id} version={allWindows.filter(w=>w.status==='completed').length} onSelect={(id,t)=>{selectTrack(id);if(t!==undefined)setTime(t);}}/>}
          <section className="card event-table">
            <div className="card__head">
              <h2 className="card__title">画面观察记录</h2>
              <span className="muted">
                {shown.length}/{items.length}条记录 · 仅已分析范围
              </span>
            </div>
            <div className="card__body observation-filter"><label>显示内容 <select aria-label="观察显示范围" value={filter} onChange={e=>{setFilter(e.target.value);setSelected('');}}><option value="observed">近似识别与动作描述</option><option value="classified">模型支持或教师确认</option><option value="all">全部片段（含待查看）</option></select></label><span className="muted">近似结果直接展示，供课堂复盘参考；点击记录可回放证据。</span></div>
            <div className="event-table__scroll">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>实际时刻</th>
                    <th>识别状态与画面描述</th>
                    <th>观察对象</th>
                    <th>观察依据</th>
                    <th>结论来源</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((i) => (
                    <tr
                      key={i.id}
                      onClick={() => choose(i.id)}
                      className={item?.id === i.id ? "is-active" : undefined}
                    >
                      <td>
                        <button className="btn btn--ghost btn--sm">
                          {fmt(observationView(i).localStart)}
                        </button>
                      </td>
                      <td><strong>{observationView(i).title}</strong><div>{observationView(i).description||'请查看对应片段'}</div></td>
                      <td>{personLabel(labels,canonicalTrack(observationView(i).trackId,groups),observationView(i).localStart)}{canonicalTrack(i.trackId,groups)!==i.trackId&&<small className="muted"> · 原轨迹{i.trackId}</small>}</td>
                      <td title={observationView(i).reliabilityReason}>{observationBasis(i)}</td>
                      <td><span className="badge badge--observation">{observationView(i).source}</span></td>
                    </tr>
                  ))}
                  {!shown.length && (
                    <tr>
                      <td colSpan={5}>
                        当前筛选下没有记录。可切换“全部片段”查看视频与待核对内容；这不表示没有课堂行为。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
        <div className="review-grid__side">
          <div className="classroom-tabs" role="tablist" aria-label="观察操作">{([['evidence','证据面板'],['correction','修正观察'],['help','使用说明']] as const).map(([key,label])=><button key={key} role="tab" aria-selected={sideTab===key} onClick={()=>setSideTab(key)}>{label}</button>)}</div>
          <div role="tabpanel" aria-label={sideTab==='evidence'?'证据面板':sideTab==='correction'?'修正观察':'使用说明'}>
          {item&&memberTracks(item.trackId,groups).length>1&&<p className="src-note">归组人物 {String(canonicalTrack(item.trackId,groups)).padStart(3,'0')} · 本条证据的原始轨迹 {item.trackId}。修改时保留实际执行轨迹。</p>}
          {sideTab==='evidence'&&<EvidencePanel
            item={item}
            identityLabels={labels}
            onSeek={(t) => {
              setTime(t);
              if (item) setTrack(canonicalTrack(item.trackId,groups));
            }}
          />}
          {sideTab==='correction'&&<CorrectionPanel item={item}/>}
          {sideTab==='help'&&<section className="card"><h3 className="card__head">识别范围与使用说明</h3>
            <div className="card__body">
              <p>
                这些记录只覆盖实际取证时刻。人物编号是匿名轨迹；画面描述与教师确认分别保存，不据此评价学生的心理状态或学习效果。
              </p>
              <Link to="/qa" className="btn btn--secondary">
                结合证据提问
              </Link>
            </div>
          </section>}
          </div>
        </div>
      </div>
    </div>
  );
}
