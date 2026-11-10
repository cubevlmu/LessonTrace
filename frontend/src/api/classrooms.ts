import { z } from "zod";
import { request } from "./client";
import {videoSchema} from '../models/backend';

const classroom = z.object({ classroom_id: z.string(), owner_actor: z.string().optional(), name: z.string(), status: z.string(), student_count: z.number().optional(),grade:z.string().optional(),term:z.string().optional(),description:z.string().optional() }).passthrough();
export type Classroom = z.infer<typeof classroom>;
const student=z.object({subject_id:z.string(),display_name:z.string(),status:z.string(),expires_at:z.string(),photo_reference_id:z.string().nullable().optional(),photo_count:z.number().optional(),track_registration_count:z.number().optional()});
export type Student=z.infer<typeof student>;
const detail = classroom.extend({ students: z.array(student) });
export const listClassrooms = () => request("classrooms", z.array(classroom));
export const createClassroom = (body: { name: string; grade?: string; term?: string; description?: string }) => request("classrooms", classroom, body);
export const getClassroom = (id: string) => request(`classrooms/${encodeURIComponent(id)}`, detail);
export type StudentInput={display_name:string;consent_record:string;expires_at:string;authorized:boolean;allow_face_matching:boolean;reference_only?:boolean};
export const addStudent = (id: string, body: StudentInput) => request(`classrooms/${encodeURIComponent(id)}/students`, z.object({ subject_id: z.string(), classroom_id: z.string(), display_name: z.string(), status: z.string() }), body);
export const editStudent=(id:string,body:StudentInput)=>request(`students/${encodeURIComponent(id)}`,z.object({updated:z.boolean()}),body,'PUT');
export const editStudentName=(id:string,display_name:string)=>request(`students/${encodeURIComponent(id)}/name`,z.object({updated:z.boolean()}),{display_name},'PUT');
export const editClassroom=(id:string,name:string,extra?:{grade?:string;term?:string;description?:string})=>request(`classrooms/${encodeURIComponent(id)}`,z.object({updated:z.boolean()}),{name,...extra},'PUT');
const reference=z.object({reference_image_id:z.string(),subject_id:z.string(),model_id:z.string().nullable(),status:z.string(),created_at:z.string()});
export const references=(sid:string)=>request(`students/${encodeURIComponent(sid)}/references`,z.array(reference));
export const photoUrl=(sid:string,id:string)=>`/api/context/students/${encodeURIComponent(sid)}/references/${encodeURIComponent(id)}/image`;
export const deletePhoto=(sid:string,id:string)=>request(`students/${encodeURIComponent(sid)}/references/${encodeURIComponent(id)}`,z.object({deleted:z.boolean()}),undefined,'DELETE');
export const linkVideo=(cid:string,vid:string)=>request(`classrooms/${encodeURIComponent(cid)}/videos/${encodeURIComponent(vid)}`,z.object({linked:z.boolean()}),{});
export const classVideos=(cid:string)=>request(`classrooms/${encodeURIComponent(cid)}/videos`,z.array(videoSchema));
export async function uploadStudentPhoto(subjectId: string, file: File) {
  const form = new FormData(); form.append("photo", file);
  return request(`students/${encodeURIComponent(subjectId)}/photo`, z.object({ reference_image_id: z.string(), subject_id: z.string(), feature_status: z.string(), matching_status: z.string() }), form);
}

export const revokeStudent=(sid:string)=>request(`students/${encodeURIComponent(sid)}/revoke`,z.object({revoked:z.boolean()}),{});
