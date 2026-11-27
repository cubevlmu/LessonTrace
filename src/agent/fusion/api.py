import json
import math
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Depends,HTTPException,Query
from fastapi.responses import FileResponse,Response
from .coverage import window_coverage
from .planning import ReviewRequest,make_scope_key
from .orchestrator import FusionWorkflow
from . import reporting
from pydantic import BaseModel

class ConversationSelection(BaseModel):
    run_id:str|None=None
    lesson_id:str|None=None
    last_report_id:str|None=None


def _track_for_reference(evidence, run_id):
    provenance = evidence['provenance']
    subject = evidence.get('subject_ref') or ''
    refs = {ref for ref in (subject if subject.startswith('track:') else None,
                            provenance.get('track_ref')) if ref is not None}
    if not refs:
        return None  # Blind descriptions and region-level observations have no executor.
    if len(refs) != 1:
        raise ValueError('evidence_track_source_mismatch')
    source, _, number = refs.pop().rpartition(':')
    if source != f'track:{run_id}' or not number.isascii() or not number.isdecimal():
        raise ValueError('evidence_track_source_mismatch')
    track = int(number)
    if provenance.get('track_id') not in (None, track):
        raise ValueError('evidence_track_source_mismatch')
    if (provenance.get('scope') == 'spatiotemporal_candidate_not_actor_proof'
            or ((provenance.get('alignment_checks') or {}).get('subject_checks') or {}).get('passed') is False):
        return None
    return track


def _playback(service, report, actor, role, evidence_id):
    from src.behavior_discovery.ingest import authorize_run
    from src.context.report_sources import VIDEO_EVIDENCE, locate_media_range, resolve_report_media

    def unavailable(reason):
        return {'status': 'unavailable', 'reason': reason}

    if report['status'] != 'current':
        return unavailable('report_stale')
    evidence = None
    if evidence_id is not None:
        evidence = next((e for e in report.get('evidence', []) if e['evidence_id'] == evidence_id), None)
        if evidence is None:
            return unavailable('reference_missing')
        if evidence['evidence_type'] not in VIDEO_EVIDENCE:
            return unavailable('not_video')
    plan = report.get('query_plan', {})
    classroom = report.get('classroom', {})
    provenance = evidence['provenance'] if evidence else {}
    try:
        media = resolve_report_media(service, classroom, actor)
        authorize_run(service, media.run_id, actor, role)
        run, clip = service.source.run(media.run_id)
        metadata = run['metadata']
        if (not media.matches(plan) or not media.matches(provenance)
                or not media.matches(metadata)
                or metadata.get('registered_end', media.end) != media.end
                or any(source.get('processing_sha256') not in (None, metadata['source_sha256'])
                       for source in (clip, provenance))
                or provenance.get('source_sha256') not in (None, metadata['source_sha256'])):
            return unavailable('source_mismatch')
        track = _track_for_reference(evidence, media.run_id) if evidence else plan.get('track_id')
        if track is not None and (type(track) is not int or not any(
            t['track_id'] == track for t in service.source.tracks(media.run_id)
        )):
            return unavailable('source_mismatch')
        ranges = locate_media_range(media, evidence['time_scope'] if evidence else plan.get('effective_time_range'))
        if ranges is None:
            return unavailable('time_out_of_range')
        local, registered = ranges
        if media.uploaded:
            from src.behavior_discovery.ingest import MEDIA_ROOT
            from src.behavior_discovery.repository import Repository
            video = Repository(service.repo).video(media.video_id, actor)
            info = video['metadata']
            if video['status'] != 'active' or clip.get('owner') != actor:
                return unavailable('media_unavailable')
            if (video['clip_id'] != media.clip_id or info.get('processing_sha256') != metadata['source_sha256']
                    or Path(info['media_path']) != media.path or MEDIA_ROOT.resolve() not in media.path.parents):
                return unavailable('source_mismatch')
            duration = info.get('duration')
            url = '/api/context/analysis/videos/' + quote(media.video_id, safe='') + '/media'
        else:
            duration = json.loads(run.get('report') or '{}').get('source_timebase', {}).get('duration')
            if duration is None:
                duration = clip.get('duration')
            if duration is None:
                from src.tracking.video import probe
                duration = probe(media.path, clip)['duration']
            url = '/api/context/media?run_id=' + quote(media.run_id, safe='')
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            return unavailable('media_unavailable')
        if local[1] > duration + .002:
            return unavailable('time_out_of_range')
        return {'status': 'available', 'run_id': media.run_id, 'clip_id': media.clip_id,
                'media_url': url, 'duration': duration, 'local_range': local,
                'global_range': registered, 'track_id': track}
    except (PermissionError, OSError):
        return unavailable('media_unavailable')
    except ValueError as error:
        if str(error) in {'registered_video_missing', 'registered_video_deleted', 'analysis_video_missing',
                          'insufficient_timebase_samples', 'no_video_stream'}:
            return unavailable('media_unavailable')
        return unavailable('source_mismatch')
    except (KeyError, TypeError, AttributeError):
        return unavailable('source_mismatch')


def mount_fusion(app,service,agent,account,writer):
    workflow=FusionWorkflow(agent,service);app.state.fusion=workflow
    from src.context.lesson_reports import mount as mount_lesson_reports
    mount_lesson_reports(app,service,workflow,account,writer)
    def privileged(a):return a['role'] in {'admin','teacher','reviewer'}
    @app.get('/review')
    def page():return FileResponse(Path(__file__).resolve().parents[3]/'webui/review.html',media_type='text/html')
    @app.post('/api/context/fusion/ask',status_code=201)
    def ask(body:ReviewRequest,a=Depends(account)):
        return agent.ask_fusion(body,workflow,a['actor'],privileged(a))
    def conversation_scope(run_id,lesson_id,a):
        if lesson_id and lesson_id.strip():
            from src.context.lesson_graph import lesson_index
            from .lesson_evidence import index_for
            if run_id:
                index_for(service,ReviewRequest(question='会话范围',run_id=run_id,lesson_id=lesson_id),a['actor'])
            else:
                lesson_index(service,lesson_id,a['actor'])
            return make_scope_key(lesson_id,run_id)
        if run_id and run_id.strip():
            from src.behavior_discovery.ingest import authorize_run
            authorize_run(service,run_id,a['actor'],a['role'])
            return None  # Legacy endpoints also list video turns anchored to a lesson.
        raise HTTPException(422,'lesson_id_or_run_id_required')

    @app.get('/api/context/fusion/conversation')
    def conversation(run_id:str|None=None,lesson_id:str|None=None,last_report_id:str|None=None,a=Depends(account)):
        from .conversation import turns
        scope_key=conversation_scope(run_id,lesson_id,a)
        return turns(service,a['actor'],run_id,last_report_id,scope_key=scope_key,
                     allow_identity=privileged(a),archive=scope_key is None)
    @app.get('/api/context/fusion/conversations')
    def conversations(run_id:str|None=None,lesson_id:str|None=None,a=Depends(account)):
        from .conversation import threads
        scope_key=conversation_scope(run_id,lesson_id,a)
        return threads(service,a['actor'],run_id,scope_key=scope_key)
    @app.post('/api/context/fusion/conversation/select')
    def select_conversation(body:ConversationSelection,a=Depends(account)):
        from .conversation import select_head
        scope_key=conversation_scope(body.run_id,body.lesson_id,a)
        return select_head(service,a['actor'],body.run_id,body.last_report_id,scope_key=scope_key)
    @app.get('/api/context/fusion/reports/{rid}')
    def report(rid:str,a=Depends(account)):return reporting.load(service,rid,a['actor'],privileged(a))
    @app.get('/api/context/fusion/reports/{fusion_report_id}/playback')
    def playback(fusion_report_id:str,evidence_id:str|None=Query(None,min_length=1),
                 target:Literal['selected_range']|None=None,a=Depends(account)):
        report=reporting.load(service,fusion_report_id,a['actor'],privileged(a))
        if (evidence_id is None)==(target is None):
            raise HTTPException(422,'select_evidence_id_or_selected_range')
        return _playback(service,report,a['actor'],a['role'],evidence_id)
    @app.get('/api/context/fusion/reports/{rid}/historical')
    def historical(rid:str,a=Depends(account)):return reporting.historical(service,rid,a['actor'],privileged(a))
    @app.post('/api/context/fusion/reports/{rid}/refresh',status_code=201)
    def refresh(rid:str,a=Depends(account)):
        with service.repo.connection() as c:r=c.execute('SELECT owner,request FROM fusion_reports WHERE report_id=?',(rid,)).fetchone()
        if not r:raise ValueError('fusion_report_not_found')
        if r['owner']!=a['actor']:raise PermissionError('fusion_report_access_denied')
        return agent.ask_fusion(json.loads(r['request']),workflow,a['actor'],privileged(a),rid)
    @app.get('/api/context/fusion/reports/{rid}/export/{format}')
    def export(rid:str,format:str,a=Depends(account)):
        r=reporting.load(service,rid,a['actor'],privileged(a))
        if r['status']!='current':raise HTTPException(409,'stale_report_recompute_required')
        if format=='json':data=json.dumps(r,ensure_ascii=False,indent=2);mime='application/json';suffix='.json'
        elif format=='markdown':data=reporting.markdown(r);mime='text/markdown';suffix='.md'
        else:raise ValueError('export_format_json_or_markdown')
        return Response(data,media_type=mime,headers={'Content-Disposition':'attachment; filename="inticlass_review_'+rid+suffix+'"'})
    @app.get('/api/context/fusion/reports/{rid}/history')
    def history(rid:str,a=Depends(account)):
        with service.repo.connection() as c:
            r=c.execute('SELECT owner FROM fusion_reports WHERE report_id=?',(rid,)).fetchone()
            if not r or r['owner']!=a['actor']:raise PermissionError('fusion_report_access_denied')
            return [dict(r) for r in c.execute('SELECT operation,created_at,payload FROM fusion_report_audit WHERE report_id=? ORDER BY created_at',(rid,))]
    @app.get('/api/context/fusion/scope')
    def scope(run_id:str,include_coverage:bool=False,track_id:int|None=Query(None,ge=1),
              roster_subject_id:str|None=None,start:float|None=None,end:float|None=None,a=Depends(account)):
        def fields(clip):
            return {k:clip[k] for k in ['clip_id','video_id','start','end','width','height']}
        if not include_coverage:
            _,clip=service.source.verified_run(run_id)
            return fields(clip)
        if roster_subject_id and not privileged(a):
            raise PermissionError('review_role_required_for_identity_or_visual_tool')
        from .planning import plan as build_anonymous_plan
        from .roster_scope import build_plan
        request=ReviewRequest(run_id=run_id,question='可观察行为',track_id=track_id,
                              roster_subject_id=roster_subject_id,start=start,end=end)
        query=build_plan(service,request,a['actor'])[1] if roster_subject_id else build_anonymous_plan(request,service.source)
        _,clip=service.source.run(run_id)
        return {**fields(clip),'coverage':window_coverage(service,query,a['actor'])}
    @app.get('/api/context/fusion/observations')
    def observations(run_id:str,track_id:int,a=Depends(account)):
        service.source.verified_run(run_id)
        return service.source.observations(run_id,track_id)
    @app.get('/api/context/fusion/subjects')
    def subjects(run_id:str,a=Depends(account)):
        if not privileged(a):return []
        from src.attribution.source import IdentityReader
        run,clip=service.source.verified_run(run_id);result={}
        for t in service.source.tracks(run_id):
            for label in IdentityReader(service.identity_dir).intersect(run_id,t['track_id'],clip['start'],clip['end'],run['metadata']['source_sha256']):
                result[label['subject_id']]={'subject_id':label['subject_id'],'display_name':label['display_name'],'scope':'current_authorized_identity_interval_only'}
        return list(result.values())
