"""Read-only local report labels. Never forwarded to the text inference provider."""
import json,sqlite3
from pathlib import Path
from cryptography.fernet import Fernet

def metadata(service,plan,actor):
    root=getattr(service,'identity_dir',None)
    if not root:return None
    root=Path(root);path=root/'identity.db';key=root/'encryption.key'
    if not path.is_file() or not key.is_file():return None
    _,clip=service.source.run(plan.run_id)
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
            c.row_factory=sqlite3.Row
            row=c.execute('SELECT cl.classroom_id,cl.name FROM classrooms cl JOIN classroom_videos v ON v.classroom_id=cl.classroom_id WHERE v.video_id=? AND cl.owner_actor=?',(clip['video_id'],actor)).fetchone()
            if not row:return None
            result={'classroom_id':row['classroom_id'],'classroom_name':row['name'],'subject_id':None,'subject_name':None}
            target=plan.subject_id or plan.roster_subject_id
            if target:
                student=c.execute("SELECT s.display_name FROM registered_subjects s JOIN classroom_students cs ON cs.subject_id=s.subject_id WHERE cs.classroom_id=? AND s.subject_id=? AND s.status IN ('active','reference_only') AND cs.status='active'",(row['classroom_id'],target)).fetchone()
                if student and student[0]:result.update(subject_id=target,subject_name=json.loads(Fernet(key.read_bytes()).decrypt(student[0])),subject_scope_kind='registered_identity' if plan.subject_id else 'teacher_selected_roster_profile_not_independent_identity')
            return result
    except sqlite3.OperationalError:return None
