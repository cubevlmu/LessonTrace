"""Reversible, operator-reported anonymous grouping; never an identity binding."""
import json
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from src.identity.store import uid, now


class GroupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str
    track_ids: list[int] = Field(min_length=2, max_length=30)
    reason: str = Field(min_length=5, max_length=1000)
    source_kind: Literal['operator_reported','demo_spatial_association']='operator_reported'


def mount(app, service, account, source):
    with service.repo.connection() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS workbench_track_groups(
            group_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,owner TEXT NOT NULL,
            created_at TEXT NOT NULL,track_ids TEXT NOT NULL,reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workbench_track_group_revocations(
            group_id TEXT PRIMARY KEY REFERENCES workbench_track_groups(group_id),
            actor TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')
        if 'source_kind' not in {r[1] for r in c.execute('PRAGMA table_info(workbench_track_groups)')}:
            c.execute("ALTER TABLE workbench_track_groups ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'operator_reported'")

    def rows(run, actor):
        with service.repo.connection() as c:
            return [{**dict(r), 'track_ids': json.loads(r['track_ids']),
                     'status': r['source_kind'], 'identity_verified': False}
                    for r in c.execute('SELECT g.* FROM workbench_track_groups g WHERE run_id=? AND owner=? AND NOT EXISTS(SELECT 1 FROM workbench_track_group_revocations r WHERE r.group_id=g.group_id) ORDER BY created_at,group_id', (run, actor))]

    @app.get('/api/context/workbench/track-groups')
    def groups(run_id: str, a=Depends(account)):
        source(run_id, a['actor'])
        return rows(run_id, a['actor'])

    @app.post('/api/context/workbench/track-groups', status_code=201)
    def create(body: GroupInput, a=Depends(account)):
        if a['role'] not in {'admin', 'teacher'}:
            raise HTTPException(403, 'teacher_write_required')
        source(body.run_id, a['actor'])
        ids = sorted(set(body.track_ids))
        valid = {t['track_id'] for t in service.source.tracks(body.run_id)}
        if len(ids) < 2 or not set(ids) <= valid:
            raise ValueError('at_least_two_existing_tracks_required')
        for group in rows(body.run_id, a['actor']):
            if group['track_ids'] == ids:
                return group
            if set(group['track_ids']) & set(ids):
                raise ValueError('track_already_grouped; revoke_previous_group_first')
        gid = uid()
        with service.repo.connection() as c:
            c.execute('INSERT INTO workbench_track_groups(group_id,run_id,owner,created_at,track_ids,reason,source_kind) VALUES (?,?,?,?,?,?,?)',
                      (gid, body.run_id, a['actor'], now(), json.dumps(ids), body.reason,body.source_kind))
        return next(g for g in rows(body.run_id, a['actor']) if g['group_id'] == gid)

    @app.post('/api/context/workbench/track-groups/{group_id}/revoke')
    def revoke(group_id: str, a=Depends(account)):
        if a['role'] not in {'admin', 'teacher'}:
            raise HTTPException(403, 'teacher_write_required')
        with service.repo.connection() as c:
            group = c.execute('SELECT * FROM workbench_track_groups WHERE group_id=? AND owner=?', (group_id, a['actor'])).fetchone()
        if not group:
            raise HTTPException(404, 'group_not_found')
        source(group['run_id'], a['actor'])
        with service.repo.connection() as c:
            c.execute('INSERT OR IGNORE INTO workbench_track_group_revocations VALUES (?,?,?)', (group_id, a['actor'], now()))
        return {'revoked': True}
