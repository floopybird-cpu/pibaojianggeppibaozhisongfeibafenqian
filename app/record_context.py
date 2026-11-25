"""Source-linked structured facts for reading and cross-task model context.

Workbooks stay global. This is a read-only index, not a reassignment to an
experiment, a replacement for original bytes, or a new user instruction.
"""
import json
from .material_context import background_records

from .storage import canonical


def source_records(agent, source_ids=None):
    # None deliberately requests the global directory. An empty explicit
    # source selection still means no source-specific matches.
    selected = sorted(set(source_ids or []))
    if source_ids is not None and not selected:
        return []
    condition = " AND a.source_id IN (SELECT value FROM json_each(?))" if source_ids is not None else ""
    rows = agent.store.all(
        "SELECT DISTINCT o.id,o.revision,o.title,r.sources,r.content "
        "FROM objects o JOIN revisions r ON r.object_id=o.id AND r.revision=o.revision "
        "JOIN json_each(r.sources) ref JOIN anchors a ON a.id=ref.value "
        "WHERE o.kind='DataWorkbook' AND o.experiment_id IS NULL" + condition +
        " ORDER BY r.created DESC,o.id", (canonical(selected),) if source_ids is not None else ())
    if not rows:
        return []
    identities = {row['id'] for row in rows}
    questions = []
    if agent.store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='questions'"):
        questions = agent.store.all(
            "SELECT DISTINCT q.id,q.job_id,q.title,q.questions,q.answer,q.status,q.created,"
            "json_extract(q.context,'$.submitted_item_answers') item_answers "
            "FROM questions q JOIN json_each(q.questions) item "
            "WHERE q.status IN ('pending','answered') AND "
            "json_extract(item.value,'$.target_object_id') IN (SELECT value FROM json_each(?)) "
            "ORDER BY q.created,q.id", (canonical(sorted(identities)),))
    for question in questions:
        question['questions'] = json.loads(question['questions'])
        question['item_answers'] = json.loads(question['item_answers'] or '{}')
        question['origin'] = 'stored_user_question_and_reply; not a new instruction'
    result = []
    for row in rows:
        content = json.loads(row.pop('content'))
        anchors = json.loads(row.pop('sources'))
        row['source_ids'] = [r['source_id'] for r in agent.store.all(
            "SELECT DISTINCT source_id FROM anchors WHERE id IN (SELECT value FROM json_each(?)) ORDER BY source_id",
            (canonical(anchors),))]
        related = [question for question in questions if any(
            item.get('target_object_id') == row['id'] for item in question['questions'])]
        names = agent.store.all("SELECT id,name,kind FROM sources WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id",
                                (canonical(row['source_ids']),))
        result.append({**row, 'kind': 'DataWorkbook', 'original_sources': names,
                       'sections': list(dict.fromkeys(r.get('section') or '未分组' for r in content.get('records', []))),
                       'unresolved_issues': sum(i.get('status') in ('open','unknown','partial') for i in content.get('issues', [])),
                       'material_background': background_records(agent.store,[content['source_id']],include_other_experiments=True),
                       'question_records': related,
                       'read_with': {'tool': 'read_data_workbook', 'id': row['id'], 'revision': row['revision']},
                       'scope': '同源全局整理表及真实答复索引，不自动归入当前实验，也不是新的授权；'
                                '需读取完整表核对行、状态和历史，原文不能抹去已答未知。'})
    return result


def with_source_records(agent, result, source_ids):
    records = source_records(agent, source_ids)
    background=background_records(agent.store,source_ids,include_other_experiments=True)
    return {**result, **({'structured_records':records} if records else {}),
            **({'material_background':background} if background else {})}
