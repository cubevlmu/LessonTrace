"""One current block per identity; report versions and adoptions are audit snapshots."""
from .report_adoptions import adoption_blocks, unique
from src.agent.fusion.evidence import version as digest


def section_block(section, version, actor, *, origin='model', statement_id=None, block_id=None):
    sid=section['section_id']
    return {'block_id':block_id or 'block_'+digest([sid,statement_id])[:32],
            'section_id':sid,'origin':origin,'text':section['content'],
            'evidence_refs':section['evidence_refs'],'source_statement_id':statement_id,
            'version':version,'actor':actor,
            'reference_status':'teacher_revised' if origin=='teacher_edited' else 'verified'}


def ensure_blocks(doc, version, actor):
    if 'blocks' not in doc:
        edited=set(doc.get('teacher_edited_sections',[]))
        doc['blocks']=[
            {**section_block(s,version,actor,origin='teacher_edited' if s['section_id'] in edited else 'model'),
             **({'legacy_source_only':True} if not s['content'].strip() else {})}
            for s in doc['sections'] if s['content'].strip() or s['evidence_refs']
        ]
        doc['blocks'].extend(b for a in doc.get('adoptions',[]) for b in adoption_blocks(a))


def project(doc):
    ordered = {}
    for block in doc['blocks']:
        old=ordered.get(block['block_id'])
        if not old or block['version']>=old['version']:
            if block['origin']=='teacher_edited':
                block={**block,'revision_label':f"教师修订（第{block['version']}版）",
                       'reference_label':'原参考来源（正文已由教师修订）'}
            ordered[block['block_id']]=block
    doc['blocks']=list(ordered.values())
    empty_or_visible=[]
    for section in doc['sections']:
        blocks=[b for b in doc['blocks'] if b['section_id']==section['section_id'] and (b['text'].strip() or b.get('legacy_source_only'))]
        native=[b for b in blocks if not b.get('fusion_report_id')]
        section['content']='\n\n'.join(b['text'] for b in native)
        # Retain legacy indices and section shape. Current reading/print uses these
        # same blocks as Markdown, while adoption prose stays in its audit snapshot.
        section['evidence_refs']=unique(r for b in blocks for r in b.get('evidence_refs',[]))
        if blocks:
            empty_or_visible.append({'section_id':section['section_id'],'heading':section['heading'],'blocks':blocks})
    doc['visible_sections']=empty_or_visible


def edit_sections(doc, sections, version, actor):
    before={s['section_id']:s for s in doc['sections']}
    edited=doc.setdefault('teacher_edited_sections',[])
    for section in sections:
        key=section['section_id']
        if section['content']==before[key]['content'] and section['evidence_refs']==before[key]['evidence_refs']:
            continue
        candidates=[b for b in doc['blocks'] if b['section_id']==key and not b.get('fusion_report_id')]
        originals=unique(r for b in candidates for r in b['evidence_refs'])
        adoption_refs={r for b in doc['blocks'] if b['section_id']==key and b.get('fusion_report_id')
                       for r in b['evidence_refs']}
        section['evidence_refs']=unique(originals+[r for r in section['evidence_refs'] if r not in adoption_refs])
        doc['blocks']=[b for b in doc['blocks'] if b not in candidates]
        doc['blocks'].append(section_block(section,version,actor,origin='teacher_edited',
                                          block_id=candidates[0]['block_id'] if candidates else None))
        if key not in edited:
            edited.append(key)
    doc['sections']=sections


def edit_blocks(doc, edits, version, actor):
    from fastapi import HTTPException
    positions={b['block_id']:i for i,b in enumerate(doc['blocks'])}
    sections={s['section_id'] for s in doc['sections']}
    known={e['evidence_id'] for e in doc['evidence']}
    seen=set()
    for edit in edits:
        if edit.block_id is None:
            if edit.section_id not in sections:
                raise HTTPException(422,'invalid_report_block_section')
            from src.identity.store import uid
            refs=edit.evidence_refs or []
            if not set(refs)<=known:
                raise HTTPException(422,'edited_report_unknown_evidence_reference')
            doc['blocks'].append(section_block({'section_id':edit.section_id,'content':edit.text,'evidence_refs':refs},
                                               version,actor,origin='teacher_edited',block_id='block_'+uid()))
            continue
        if edit.block_id not in positions or edit.block_id in seen:
            raise HTTPException(422,'unknown_or_duplicate_report_block')
        seen.add(edit.block_id)
        pos=positions[edit.block_id]
        block=doc['blocks'][pos]
        if edit.section_id not in {None,block['section_id']}:
            raise HTTPException(422,'report_block_section_immutable')
        if edit.evidence_refs is not None and set(edit.evidence_refs)!=set(block['evidence_refs']):
            raise HTTPException(422,'report_block_original_references_immutable')
        if block['text']!=edit.text:
            doc['blocks'][pos]={**block,'text':edit.text,'origin':'teacher_edited','actor':actor,'version':version,
                                'reference_status':'teacher_revised'}
