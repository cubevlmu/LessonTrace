import {z} from 'zod';
import {request,responseError} from './client';
import {evidenceSchema} from '../models/backend';
export const sectionSchema=z.object({section_id:z.string(),heading:z.string(),content:z.string(),evidence_refs:z.array(z.string())});
export const lessonReportSchema=z.object({report_id:z.string(),title:z.string(),angle:z.enum(['lesson','students','student']),version:z.number(),origin:z.string(),status:z.enum(['current','stale']),created_at:z.string(),sections:z.array(sectionSchema),evidence:z.array(evidenceSchema),classroom:z.object({run_id:z.string()}).passthrough(),product_scope:z.object({classroom_name:z.string(),subject_name:z.string().nullable()}).nullable().optional(),scope_warning:z.string(),generation:z.object({status:z.string()}).passthrough().optional(),options:z.object({roster_subject_id:z.string().nullable().optional(),track_id:z.number().nullable().optional(),angle:z.string()}).passthrough(),reason:z.string().optional()}).passthrough();
export type LessonReport=z.infer<typeof lessonReportSchema>;
export const listLessonReports=(run:string)=>request('lesson-reports?run_id='+encodeURIComponent(run),z.array(z.object({report_id:z.string(),angle:z.string(),created_at:z.string()})));
export const getLessonReport=(id:string)=>request('lesson-reports/'+encodeURIComponent(id),lessonReportSchema);
export const createLessonReport=(body:Record<string,unknown>)=>request('lesson-reports',lessonReportSchema,body);
export const editLessonReport=(report:LessonReport,sections:LessonReport['sections'])=>request('lesson-reports/'+report.report_id,lessonReportSchema,{version:report.version,sections},'PUT');
export const reviseLessonReport=(report:LessonReport,instruction:string,keepTeacherEdits=true)=>request('lesson-reports/'+report.report_id+'/revise',lessonReportSchema,{version:report.version,instruction,keep_teacher_edits:keepTeacherEdits});
export async function downloadLessonReport(id:string,format:'markdown'|'json'){
 const r=await fetch('/api/context/lesson-reports/'+encodeURIComponent(id)+'/export/'+format,{credentials:'same-origin'});if(!r.ok)throw responseError(r.status,await r.json());const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='课迹复盘报告.'+(format==='markdown'?'md':'json');a.click();URL.revokeObjectURL(url);
}
