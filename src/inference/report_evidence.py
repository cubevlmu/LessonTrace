"""Order already authorized evidence for a bounded report-writing context."""
from collections import defaultdict, deque


def demo_notice(text):
    """Recognize a standalone sample-data notice, preserving substantive mixed passages."""
    text = text.strip()
    if text.startswith('【比赛演示样例】') and '不用于认定视频行为或真实成绩' in text:
        return not text.partition('不用于认定视频行为或真实成绩')[2].strip('。 .\n')
    return False


def report_order(evidence):
    # Cycle through documents and observed actions before spending the budget on
    # more examples of one action. Keep original rows and their exact locators.
    groups = defaultdict(lambda: defaultdict(list))
    for row in evidence:
        p = row['provenance']
        kind = row['evidence_type']
        if p.get('document_id'):
            bucket, key = 'materials', p['document_id']
        elif kind in {'human_confirmed_event', 'human_visual_review'}:
            bucket, key = 'reviews', p.get('track_id')
        elif kind == 'vlm_observation':
            bucket, key = 'observations', p.get('action') or row['content']
        elif kind == 'model_discovered_event':
            bucket, key = 'decisions', p.get('action')
        else:
            bucket, key = 'context', kind
        groups[bucket][key].append(row)

    queues = {}
    for bucket, values in groups.items():
        if bucket == 'materials':
            for rows in values.values():
                rows.sort(key=lambda e: demo_notice(e['content']))
        if bucket == 'observations':
            for rows in values.values():
                rows.sort(key=lambda e: not e['provenance'].get('sampled_frames_valid'))
        queues[bucket] = deque(deque(rows) for rows in values.values())
    while any(queues.values()):
        for bucket in ('reviews', 'observations', 'materials', 'decisions', 'context'):
            queue = queues.get(bucket)
            if queue:
                rows = queue.popleft()
                yield rows.popleft()
                if rows:
                    queue.append(rows)
