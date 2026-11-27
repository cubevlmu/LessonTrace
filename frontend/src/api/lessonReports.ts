import {z} from 'zod';
import {request,responseError} from './client';
import {evidenceSchema} from '../models/backend';
export const sectionSchema=z.object({section_id:z.string(),heading:z.string(),content:z.string(),evidence_refs:z.array(z.string())});
export const adoptedParagraphSchema=z.object({
 section_id:z.string(),
 text:z.string(),
 evidence_refs:z.array(z.string()).default([]),
 content_kind:z.string().optional(),
 check_evidence_refs:z.array(z.string()).optional(),
 check_scope:z.object({
  run_id:z.string().nullable().optional(),
  clip_id:z.string().nullable().optional(),
  subject_label:z.string().nullable().optional(),
  effective_time_range:z.tuple([z.number(),z.number()]).nullable().optional(),
 }).passthrough().optional(),
}).passthrough();
export const adoptionSchema=z.object({
 fusion_report_id:z.string(),
 adopted_version:z.number(),
 actor:z.string().optional(),
 created_at:z.string().optional(),
 section_ids:z.array(z.string()).default([]),
 scope:z.object({subject_label:z.string().nullable().optional()}).passthrough().optional(),
 paragraphs:z.array(adoptedParagraphSchema).default([]),
}).passthrough();
export const reportBlockSchema=z.object({
 block_id:z.string(),
 section_id:z.string(),
 origin:z.enum(['model','adopted','teacher_edited']),
 text:z.string(),
 evidence_refs:z.array(z.string()),
 source_statement_id:z.string().nullish(),
 version:z.number().int().positive(),
 actor:z.string().nullish(),
 reference_status:z.string().nullish(),
}).passthrough();
export const lessonReportSchema=z.object({
 report_id:z.string(),
 title:z.string(),
 angle:z.enum(['lesson','students','student']),
 version:z.number(),
 origin:z.string(),
 status:z.enum(['current','stale']),
 created_at:z.string(),
 sections:z.array(sectionSchema),
 blocks:z.array(reportBlockSchema).optional(),
 teacher_edited_sections:z.array(z.string()).optional(),
 adoptions:z.array(adoptionSchema).default([]),
 evidence:z.array(evidenceSchema),
 source_labels:z.record(z.string()).default({}),
 adoption_check_labels:z.record(z.string()).default({}),
 classroom:z.object({run_id:z.string().nullish(),lesson_id:z.string().nullish()}).passthrough(),
 product_scope:z.object({classroom_name:z.string(),subject_name:z.string().nullable()}).nullable().optional(),
 scope_key:z.string().optional(),
 scope_warning:z.string(),
 generation:z.object({status:z.string()}).passthrough().optional(),
 options:z.object({
  roster_subject_id:z.string().nullable().optional(),
  subject_id:z.string().nullable().optional(),
  lesson_id:z.string().nullable().optional(),
  track_id:z.number().nullable().optional(),
  start:z.number().nullable().optional(),
  end:z.number().nullable().optional(),
  angle:z.string(),
 }).passthrough(),
 reason:z.string().optional(),
}).passthrough();
export const adoptionResultSchema=z.object({
 outcome:z.enum(['adopted','already_adopted']),
 report_id:z.string(),
 version:z.number(),
 adopted_version:z.number(),
 fusion_report_id:z.string(),
 section_ids:z.array(z.string()),
 created:z.boolean().optional(),
});
export const scopeMismatchReasonSchema=z.object({
 kind:z.enum(['run','clip','subject','time','source_missing','source_version']),
 message:z.string(),
 evidence_id:z.string().optional(),
});
export const scopeMismatchDetailSchema=z.object({
 code:z.literal('scope_mismatch'),
 report_id:z.string(),
 current_version:z.number(),
 fusion_report_id:z.string(),
 reasons:z.array(scopeMismatchReasonSchema),
 can_create_from_answer:z.boolean(),
});
export type LessonReport=z.infer<typeof lessonReportSchema>;
export type ReportBlock=z.infer<typeof reportBlockSchema>;
export type AdoptionResult=z.infer<typeof adoptionResultSchema>;
export type ScopeMismatchDetail=z.infer<typeof scopeMismatchDetailSchema>;
export const listLessonReports=(run:string)=>request('lesson-reports?run_id='+encodeURIComponent(run),z.array(z.object({report_id:z.string(),angle:z.string(),created_at:z.string()})));
// request's schema type also infers input fields as optional when they have defaults.
const requestReport=(path:string,body?:unknown,method?:string):Promise<LessonReport>=>
 request(path,lessonReportSchema,body,method).then(doc=>({...doc,adoptions:doc.adoptions??[],source_labels:doc.source_labels??{},adoption_check_labels:doc.adoption_check_labels??{}}));
export const getLessonReport=(id:string)=>requestReport('lesson-reports/'+encodeURIComponent(id));
export const createLessonReport=(body:Record<string,unknown>)=>requestReport('lesson-reports',body);
export const createLessonReportFromAnswer=(body:{fusion_report_id:string;create_reason:'first_draft'|'scope_mismatch';target_report_id?:string})=>
 request('lesson-reports',adoptionResultSchema,{mode:'from_answer',...body});
export const adoptAnswerToReport=(reportId:string,fusionReportId:string,expectedVersion:number)=>
 request('lesson-reports/'+encodeURIComponent(reportId)+'/adoptions',adoptionResultSchema,{fusion_report_id:fusionReportId,expected_version:expectedVersion});
export const editLessonReport=(report:LessonReport,sections:LessonReport['sections'],blocks?:ReportBlock[])=>
 requestReport('lesson-reports/'+encodeURIComponent(report.report_id),{version:report.version,sections,...(blocks===undefined?{}:{blocks})},'PUT');
export const reviseLessonReport=(report:LessonReport,instruction:string,keepTeacherEdits=true)=>requestReport('lesson-reports/'+report.report_id+'/revise',{version:report.version,instruction,keep_teacher_edits:keepTeacherEdits});
export async function downloadLessonReport(id:string,format:'markdown'|'json',version?:number){
 const r=await fetch('/api/context/lesson-reports/'+encodeURIComponent(id)+'/export/'+format+(version === undefined ? '' : '?version='+version),{credentials:'same-origin'});if(!r.ok)throw responseError(r.status,await r.json());const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='课迹复盘报告.'+(format==='markdown'?'md':'json');a.click();URL.revokeObjectURL(url);
}
