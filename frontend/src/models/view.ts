import type { Frame,DiscoveredEvent } from "./backend";
export function videoTitle(video?:{metadata:{filename?:string;duration:number}}){
 const name=video?.metadata.filename;
 return name&&name.length<=38?name.replace(/\.[^.]+$/,''):video?`课堂录像 · ${video.metadata.duration.toFixed(0)} 秒`:'真实课堂复盘';
}
export interface VisualItem {
  id: string;
  kind: "event" | "window";
  trackId: number;
  localStart: number;
  localEnd: number;
  category: string;
  state: string;
  description: string[];
  frames: (Frame & { url: string; actions?: string[] })[];
  source: "model_discovered_event" | "blind_visual_observation";
  limitations: string[];
  raw: unknown;
  eventId?: string;
  reviewStatus: string;
  humanCorrection?:DiscoveredEvent['human_correction'];
  observationCount?:number;
}
const observationLabels = new Map<string, string>([
  ["低头写作业", "低头书写"],
  ["raising hand", "举手"],
  ["raising a hand", "举手"],
  ["raising left hand", "举左手"],
  ["raising right hand", "举右手"],
  ["raising right hand to head", "抬右手至头部"],
  ["raising hand to the forehead", "抬手至额头"],
  ["raising hand to face", "抬手至脸部"],
  ["touching forehead", "触摸额头"],
  ["hand on head", "手放在头上"],
  ["hand resting on head", "手放在头上"],
  ["resting head on hand", "用手托着头"],
  ["resting head on desk", "头部伏在桌面"],
  ["resting their head on the desk", "头部伏在桌面"],
  ["head down", "低头"],
  ["head tilted", "头部倾斜"],
  ["head tilted slightly", "头部略微倾斜"],
  ["leaning forward", "身体前倾"],
  ["leaning forward slightly with their head down", "身体略微前倾，低着头"],
  ["leaning forward slightly with his hands on the desk", "身体略微前倾，双手放在桌上"],
  ["leaning forward with his arms crossed on the desk", "身体前倾，双臂交叉放在桌上"],
  ["leaning forward with his head down on the desk", "身体前倾，头伏在桌面上"],
  ["leaning forward slightly, with their head tilted down towards the desk, and their right hand is resting on the desk", "身体略微前倾，头朝桌面低垂，右手放在桌上"],
  ["leaning forward slightly with his head down on the desk, appearing to be focused on writing or reading", "身体略微前倾，头伏在桌面上；原描述推测其可能在专心书写或阅读"],
  ["standing", "站姿"],
  ["standing near the table", "站在桌边"],
  ["standing near a desk", "站在课桌旁"],
  ["moving", "移动"],
  ["moving slightly while standing", "站立时略微移动"],
  ["sitting", "坐姿"],
  ["sitting at a desk", "坐在课桌前"],
  ["sitting at his desk", "坐在课桌前"],
  ["sitting at her desk", "坐在课桌前"],
  ["sitting at his desk with his arms crossed", "坐在课桌前，双臂交叉"],
  ["sitting at a desk with their head slightly tilted forward", "坐在课桌前，头部略微前倾"],
  ["looking down at his book", "低头看向书本"],
  ["looking down at her book", "低头看向书本"],
  ["looking down at a book", "低头看向书本"],
  ["looking at the book on his desk", "看着桌上的书"],
  ["looking at the paper in front of him", "看着面前的纸"],
  ["looking forward", "看向前方"],
  ["looking at the front", "看向前方"],
  ["looking at the teacher", "看向教师"],
  ["looking up and to his right", "抬眼看向右上方"],
  ["looking up and to his right, possibly at the teacher or the board", "抬眼看向右上方，可能在看教师或黑板"],
  ["holding a phone", "手持手机"],
  ["looking at a phone", "看向手机"],
  ["holding a bag", "手持一个包"],
  ["gesturing with their right hand", "用右手做手势"],
  ["writing", "书写"],
  ["writing on paper", "在纸面书写"],
  ["writing on a paper with his right hand", "用右手在纸上书写"],
  ["writing on a paper with a pencil", "用铅笔在纸上书写"],
  ["the boy raises his right hand", "该人物举起右手。"],
  ["seated at a desk, head tilted back, left hand resting on head, right hand on desk", "坐在课桌前，头部后仰，左手放在头上，右手放在桌上"],
  ["target maintains similar posture, slight head movement, hands unchanged", "目标人物保持相似姿势，头部略微移动，双手姿势未变。"],
  ["target maintains seated posture with head tilted back, hands unchanged, no significant movement observed", "目标人物保持坐姿，头部后仰，双手姿势未变，未观察到明显移动。"],
  ["target remains seated with similar posture, slight head movement observed", "目标人物保持相似坐姿，可见头部略微移动。"],
]);

const observationSubjects: Record<string, string> = {
  "the boy": "该人物", "the girl": "该人物", "the student": "该人物", "the person": "该人物",
  "target": "目标人物", "the target": "目标人物", "the target person": "目标人物",
  "the person in the right crop": "右侧裁剪画面中的人物",
  "the person in the left crop": "左侧裁剪画面中的人物",
};

/** Translate only a whole known observation or a fully recognized same-subject sentence. */
export function observationText(text: string): string {
  const normalized = text.trim().toLowerCase().replace(/\s+/g, " ").replace(/\.$/, "");
  const label = observationLabels.get(normalized);
  if (label) return label;

  const sentence = normalized.match(/^(.+?) is (.+)$/);
  if (!sentence || !Object.prototype.hasOwnProperty.call(observationSubjects, sentence[1])) return text;
  const subject = observationSubjects[sentence[1]];
  const action = observationLabels.get(sentence[2]);
  if (action) return subject + action + "。";

  const parts = sentence[2].split(/,\s*| and /);
  if (parts.length === 2) {
    const first = observationLabels.get(parts[0]);
    const second = observationLabels.get(parts[1]);
    if (first && second) return subject + first + "，" + second + "。";
  }
  return text;
}
/** Display provenance separately from the unchanged model decision. */
export function observationView(item:VisualItem){
 const human=item.humanCorrection;
 const offset=item.frames[0]?item.frames[0].global_time-item.frames[0].local_time:null;
 const time=human?.status==='confirmed'&&offset!==null?{localStart:human.start-offset,localEnd:human.end-offset}:{localStart:item.localStart,localEnd:item.localEnd};
 if(human?.status==='confirmed')return {...time,title:actionLabel[human.corrected_category]||human.corrected_category,description:human.observable_action,source:'教师确认',classified:true,trackId:human.track_id,reliability:'高',reliabilityReason:'已由教师复核，仅适用于所填时间与人物范围。',suggestion:actionLabel[human.corrected_category]||human.corrected_category};
 const described=[...new Set(item.description.filter(t=>t.trim()).map(observationText))].join('；');
 const concrete=item.category!=='unknown'&&item.category!=='other_observable_action';
 const suggestion=concrete?(actionLabel[item.category]||item.category):described||'取证帧回放';
 if(item.state==='confirmed'&&concrete&&human?.status!=='rejected')return {...time,title:suggestion,description:described,source:'模型支持',classified:true,trackId:item.trackId,reliability:'中',reliabilityReason:'通过当前模型与程序检查，尚未经教师复核；非实测准确率。',suggestion};
 const excluded=human?.status==='rejected'||item.state==='rejected';
 const first=item.description.map(observationText).find(t=>t.trim());
 const processing:Record<string,string>={pending:'等待分析',running:'正在分析',error:'分析异常'};
 const title=excluded?'已排除该候选':processing[item.state]|| (first?first.replace(/[。；]$/,''):concrete?suggestion:'取证帧回放');
 const approximate=!!described||(item.state==='uncertain'&&item.category!=='unknown'&&item.category!=='other_observable_action');
 return {...time,title,description:described,source:human?.status==='rejected'?'教师已驳回':item.state==='rejected'?'模型反证':approximate?'推断':'待查看',classified:false,trackId:item.trackId,reliability:excluded?'已排除':'低',reliabilityReason:excluded?'该候选已有反证或教师驳回记录。':'推断供课堂复盘参考，尚未经教师确认；原始模型与程序校验结果保留在详情中。',suggestion};
}
export function observationBasis(item:VisualItem){
 if(item.humanCorrection?.status==='confirmed')return '教师已核对';
 if(item.humanCorrection?.status==='rejected'||item.state==='rejected')return '已有排除记录';
 if(item.state==='confirmed'&&item.category!=='unknown')return '模型类别支持';
 if((item.observationCount||0)>1)return `多时刻观察（${item.observationCount}）`;
 return item.description.some(t=>t.trim())?'单次画面观察':'待查看画面';
}
export function showObservation(item:VisualItem,filter:string){
 if(filter==='all'||item.state==='rejected'||item.humanCorrection?.status==='rejected')return true;
 const view=observationView(item);
 return filter==='classified'?view.classified:!!view.description||view.classified||view.source==='推断';
}
export const humanReviewLabel: Record<string, string> = {
  unreviewed: "未独立人工复核",
  confirmed: "已有独立人工确认（仅引用区间）",
  rejected: "人工已驳回归属声明",
  ambiguous: "人工判为执行者歧义",
  uncertain: "人工仍无法判断",
  revoked: "人工复核已撤销",
};
export const stateLabel: Record<string, string> = {
  unknown: "画面动作记录",
  uncertain: "推断",
  confirmed: "模型证据支持（未独立人工确认）",
  rejected: "模型报告存在反证",
  error: "处理异常",
  supported: "有证据支持（限所述范围）",
  contradicted: "证据存在矛盾",
  unreviewed: "未独立人工复核",
  revoked: "复核已撤销",
};
export const actionLabel: Record<string, string> = {
  stand: "站立",
  walk: "行走",
  playphone: "手机操作",
  sleep: "睡眠（不能仅凭姿态确定真实睡眠）",
  readbook: "阅读",
  homework: "作业行为（需任务语境）",
  chat: "交谈",
  eatordrink: "进食或饮水",
  usecomputer: "电脑操作",
  discuss: "讨论",
  hubbub: "群体嘈杂（需声音证据）",
  call: "通话（需语境）",
  chase: "追逐",
  unknown: "画面动作记录",
  other_observable_action: "具体动作描述",
};
export function fmt(t: number) {
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(2).padStart(5, "0")}`;
}
export function toLocal(global: number, offset: number, duration: number) {
  const t = global - offset;
  if (!Number.isFinite(t) || t < -0.002 || t > duration + 0.002)
    throw Error("视频不覆盖该证据时间");
  return Math.max(0, Math.min(duration, t));
}
export function trackLabel(id: number) {
  return `匿名人物轨迹 ${String(id).padStart(3, "0")}`;
}
