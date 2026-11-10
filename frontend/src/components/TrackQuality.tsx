import {useEffect,useState} from 'react';
import {z} from 'zod';
import {request} from '../api/client';
export const qualitySchema=z.object({tracks:z.array(z.object({track_id:z.number(),quality_flags:z.array(z.string()),possible_nested_with:z.number().nullable(),preferred_for_action_review:z.boolean()}).passthrough()),raw_track_count:z.number(),preferred_region_count:z.number(),possible_nested_region_count:z.number(),not_a_student_count:z.boolean()});
export type TrackQualityData=z.infer<typeof qualitySchema>;
export function useTrackQuality(run:string|null|undefined){
 const [data,setData]=useState<TrackQualityData|null>(null);useEffect(()=>{setData(null);if(!run)return;let active=true;void request('workbench/track-quality?run_id='+encodeURIComponent(run),qualitySchema).then(d=>{if(active)setData(d);}).catch(()=>{if(active)setData(null);});return()=>{active=false;};},[run]);return data;
}
