"""Run automatically only for explicitly authorized, class-linked uploaded videos."""
from .store import IdentityStore,future
from .product_matching import ClassroomMatcher

def execute(context,video_id,run_id,actor):
    if not getattr(context,'identity_dir',None):return {'status':'not_configured'}
    store=IdentityStore(context.identity_dir)
    with store.connection() as c:scope=c.execute("SELECT * FROM classroom_video_identity_scopes WHERE video_id=? AND owner_actor=? AND status='active'",(video_id,actor)).fetchone()
    if not scope:return {'status':'not_authorized','reason':'anonymous_tracks_retained'}
    try:
        future(scope['expires_at'])
        result=ClassroomMatcher(store,context).run_classroom(scope['classroom_id'],run_id,actor,store.unseal(scope['consent_record']),scope['expires_at'],True)
        return {'status':'completed_candidates_only',**result}
    except Exception as error:return {'status':'unavailable','error_type':type(error).__name__,'reason':str(error)[:300],'anonymous_tracks_retained':True}
