"""Experiment-wide outlines, separate from resumable long-document buffers.

The format owns the outline. The host binds real requests and fixed revisions;
it does not judge whether an explanation is good merely because fields exist.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager

from .storage import canonical, digest, now


PHASES = {'prestudy': '预习与理解', 'procedure': '实验实施',
          'recording': '数据记录与整理', 'analysis': '数据处理与解释'}
KINDS = {'definition': '定义', 'fact': '记忆性知识', 'formula': '公式与条件',
         'procedure': '操作或记录知识'}


def preserves_outline(old, new):
    """Allow additive measurement upgrades while keeping all pre-existing editorial content."""
    if isinstance(old, dict):
        return isinstance(new, dict) and all(key in new and preserves_outline(value, new[key])
                                             for key, value in old.items())
    if isinstance(old, list):
        return (isinstance(new, list) and len(new) >= len(old)
                and all(preserves_outline(before, after) for before, after in zip(old, new)))
    return old == new


def validate_outline(content,bind_setup=False):
    """Identity/order checks only; content quality belongs to the audit."""
    stages = content['stages']
    from .observation_paths import check_design
    check_design(content,bind_setup)
    if content.get("measurement_methods"):
        from .measurement_plan import validate_methods
        validate_methods(content)
    keys = [s['key'] for s in stages]
    if len(keys) != len(set(keys)):
        raise ValueError('草稿阶段标识重复')
    if any(key not in keys for key in PHASES):
        raise ValueError('草稿应顺着预习、实验实施、记录整理、数据处理过完整条主线；非当前重点可简写')
    if [keys.index(key) for key in PHASES] != sorted(keys.index(key) for key in PHASES):
        raise ValueError('草稿的预习、实施、记录、分析应按逻辑顺序排列')
    seen = set()
    for stage in stages:
        for point in stage['points']:
            if point['id'] in seen:
                raise ValueError('草稿要点标识重复')
            if set(point.get('depends_on', [])) - seen:
                raise ValueError('草稿先修要点须指向前面已有的要点')
            seen.add(point['id'])


def outline_markdown(content, include_title=True):
    lines = (['# ' + content['title'], ''] if include_title else []) + [content['purpose'], '',
             '材料选择：' + content['source_strategy'], '']
    request = content.get('request', {})
    if request.get('initial'):
        overall = request.get('overall') or request['initial']
        lines += ['整体任务：' + overall, '']
        if overall != request['initial']:
            lines += ['最近要求：' + request['initial'], '']
    for background in request.get('material_background',[]):
        if background.get('answer'):
            lines += ['背景核对：'+background['question'],'现场答复：'+background['answer'],'']
    if content.get('delivery_requests'):
        lines += ['交付与版式安排：'] + ['- '+item for item in content['delivery_requests']] + ['']
    if content.get('measurement_methods'):
        from .measurement_plan import measurement_markdown
        lines += ['## 测量方法与交付结果', '']
        for method in content['measurement_methods']:
            lines.extend(measurement_markdown(method))
    for obs in content.get('observations',[]):
        lines.append(f"- {obs['quantity']}（{obs['unit']}），{obs['setup']}。读数判据：{obs['criterion']}")
    for branch in content.get('branches',[]):
        lines.append(f"- {branch['condition']}：{branch['action']}")
    for n, stage in enumerate(content['stages'], 1):
        scope = '简要衔接' if stage['scope'] == 'overview' else '本次展开'
        lines += [f"## {n}. {stage['title']}（{scope}）", '']
        for point in stage['points']:
            lines += ['### ' + point['title'], '', '知识点：']
            for item in point['knowledge']:
                lines.append(f"- {KINDS[item['kind']]}：{item['text']}")
            lines += ['', '选材与引用安排：' + point['material_plan'], '',
                      f"诠释要点（{len(point['interpretations'])}项）："]
            if not point['interpretations']:
                lines.append('本点只需定义、事实或操作说明，不另加诠释。')
            for i, item in enumerate(point['interpretations'], 1):
                lines += [f"{i}. {item['question']}：{item['emphasis']}"]
            lines.append('')
    return '\n'.join(lines)


def build_outline(content, out):
    (out / 'draft.md').write_text(outline_markdown(content), encoding='utf-8')
    (out / 'draft.json').write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')


class ExperimentDrafts:
    def __init__(self, agent):
        self.agent = agent
        self.store, self.gateway = agent.store, agent.gateway
        self.store.execute('''CREATE TABLE IF NOT EXISTS experiment_draft_tasks (
            job_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, draft_id TEXT NOT NULL,
            revision INTEGER NOT NULL, request_fingerprint TEXT NOT NULL, created TEXT NOT NULL)''')

    def current(self, experiment_id):
        row = self.store.one("SELECT id FROM objects WHERE experiment_id=? AND kind='ExperimentDraft' ORDER BY created DESC LIMIT 1", (experiment_id,))
        return self.gateway.read(row['id']) if row else None

    def request(self, job_id, experiment_id):
        from .task_scope import job_task
        task = job_task(self.agent, job_id, experiment_id)
        initial = task.get('initial_request') or {}
        if not initial.get('content'):
            raise ValueError('草稿须绑定本任务真实用户要求，不能用模型自写要求代替')
        exp = self.store.experiment(experiment_id)
        from .material_context import background_records
        request = {'initial': initial['content'], 'message_id': initial['id'],
                'answers': [{'question_id': q['id'], 'answer': q['answer']}
                            for q in task['answers'] if q['status'] == 'answered'],
                'selected_sources': exp['source_ids'], 'goal': exp['goal'] + ('\n实验备注：' + exp['notes'] if exp.get('notes') else ''),
                'background': exp['background'],
                'material_background':background_records(self.store,exp['source_ids'],experiment_id)}
        current = self.current(experiment_id)
        if current:
            original = current['content'].get('request', {})
            overall = original.get('overall') or original.get('initial')
            if overall and overall != initial['content']:
                request['overall'] = overall
        return request

    def save(self, experiment_id, job_id, content, identity=None, base_revision=None):
        if not experiment_id:
            raise ValueError('实验草稿必须属于当前实验')
        current = self.current(experiment_id)
        if current and identity is None:
            raise ValueError('本实验已有草稿；先read_experiment_draft读全文，再用原id和base_revision补本次要求')
        if identity:
            old = self.gateway.read(identity)
            if old['kind'] != 'ExperimentDraft' or old['experiment_id'] != experiment_id:
                raise ValueError('草稿不属于当前实验')
            if current and identity != current['id']:
                raise ValueError('请沿当前实验草稿接续，不另造互相矛盾的底稿')
        value = copy.deepcopy(content)
        value['request'] = self.request(job_id, experiment_id)
        from .tool_catalog import requests_lab_book
        requested=value.get('deliverables',[])
        if ('record_sheet' in requested and 'plan' not in requested and not value['request']['answers']
                and requests_lab_book(value['request']['initial'])):
            raise ValueError('用户明确要制作实验册。底稿交付物用 plan，内含记录附录；单独记录纸才用 record_sheet。保留已写的观察与计划。')
        value.setdefault('sources', [])
        for method in value.get('measurement_methods', []):
            value['sources'] = list(dict.fromkeys([*value['sources'], *method.get('source_index', {}).values(),
                                                    *method.get('page', {}).get('sources', [])]))
        if any('sources' in stage or any('sources' in p for p in stage.get('points',[])) for stage in value.get('stages',[])):
            from .observation_paths import source_union
            value['sources']=source_union({k:value[k] for k in ('sources','stages','observations') if k in value})
        target=None
        if identity:
            latest=self.gateway.registry.owner('ExperimentDraft')
            if latest['digest']!=old['pack_digest']:
                from .format_program import Program,check_schema
                pinned=self.gateway.registry.pinned(old['pack_id'],old['pack_version'],old['pack_digest'])
                snapshot=self.gateway.snapshot(value,experiment_id)
                if pinned:
                    try:check_schema(value,Program(pinned).schemas['ExperimentDraft'],snapshot)
                    except ValueError:
                        if old.get('human_baseline'):
                            authored = {k:v for k,v in old['content'].items() if k != 'request'}
                            additive = ('measurement_methods' not in authored and value.get('measurement_methods')
                                        and old['pack_version'] == '1.2.1' and preserves_outline(authored, value))
                            if not additive:
                                raise ValueError('本次升级会改动原草稿的人工内容。读取当前修订，保留原有字段、文字和顺序，再追加测量方法与新要点。')
                        check_schema(value,Program(latest).schemas['ExperimentDraft'],snapshot)
                        target=latest
        result = self.gateway.apply('ExperimentDraft', value, experiment_id=experiment_id,
                                    object_id=identity, base_revision=base_revision or 0,_target_pack=target)
        self.store.execute('INSERT OR REPLACE INTO experiment_draft_tasks VALUES(?,?,?,?,?,?)',
                           (job_id, experiment_id, result['id'], result['revision'],
                            digest(canonical(value['request'])), now()))
        self.agent.runtime.event(job_id, 'experiment_draft_bound', {
            'id': result['id'], 'revision': result['revision'],
            'request_fingerprint': digest(canonical(value['request']))})
        return result

    def revise(self,experiment_id,job_id,old,revision,operations):
        if not experiment_id or old['experiment_id']!=experiment_id or old['revision']!=revision:
            raise ValueError('底稿所属实验或修订已变化，请读取当前固定版本')
        if any(op['path'].split('/')[1:2]==['request'] for op in operations):
            raise ValueError('真实请求与答复由宿主绑定，不能手工编辑')
        content=self.gateway.edited_content(old,operations)
        return self.save(experiment_id,job_id,content,old['id'],revision)

    def require(self, experiment_id, job_id):
        row = self.store.one('SELECT * FROM experiment_draft_tasks WHERE job_id=? AND experiment_id=?', (job_id, experiment_id))
        if not row:
            raise ValueError('先保存本任务实验草稿，再写文稿或做数据分析。已有草稿先读全文，按本次要求接续；只做分析时前面阶段可简写')
        if row['request_fingerprint'] != digest(canonical(self.request(job_id, experiment_id))):
            raise ValueError('真实答复、要求或选用材料已变化；先把变化纳入实验草稿，再继续对应文稿和分析')
        obj = self.gateway.read(row['draft_id'])
        if obj['revision'] != row['revision']:
            raise ValueError('实验草稿已有新版本；先读取并保存本任务对应底稿，再继续')
        return {'id': obj['id'], 'revision': obj['revision']}

    def context(self, experiment_id):
        obj = self.current(experiment_id)
        if not obj:
            return {'status': 'not_started', 'next': '先查实际讲义，建立贯穿预习到数据处理的简要实验草稿'}
        return {'id': obj['id'], 'revision': obj['revision'], 'content': obj['content'],
                'meaning': '本实验工作底稿；按真实新要求先更新。简写部分不是已完成的详细文稿'}

    def attach(self, content, binding, kind):
        value = copy.deepcopy(content)
        obj = self.gateway.read(binding['id'], binding['revision'])
        by_phase = {s['key']: [p['id'] for p in s['points']] for s in obj['content']['stages']}
        phases = ['analysis'] if kind == 'AnalysisProgram' else {
            'prestudy': ['prestudy'], 'short': ['prestudy'], 'deep': ['prestudy'],
            'plan': ['procedure', 'recording'], 'record_sheet': ['recording']}.get(value.get('doc_type'), list(PHASES))
        selected = value.get('draft_points') or [p for phase in phases for p in by_phase.get(phase, [])]
        available = {p['id'] for s in obj['content']['stages'] for p in s['points']}
        if not selected or set(selected) - available:
            raise ValueError('成果的draft_points须指向这份固定草稿中真实存在的要点')
        value['draft_ref'] = binding
        value['draft_points'] = list(dict.fromkeys(selected))
        return value

    @contextmanager
    def work(self, name, args, experiment_id, job_id):
        """Only content-producing operations require an up-to-date outline.

        Reading, importing, locating files and ordinary clarification stay usable.
        A model cannot bypass the workflow through an alternative edit tool.
        """
        produce = name in ('submit_document', 'begin_document_draft', 'append_document_section',
                          'append_document_sections', 'finish_document_draft','correct_document_candidate', 'submit_program', 'run_program')
        if name in ('edit_artifact', 'upgrade_artifact', 'apply_artifact_edits'):
            ids = [e['id'] for e in args.get('edits', [])] if name == 'apply_artifact_edits' else [args['id']]
            objects = [self.gateway.read(i) for i in ids]
            if any(o['kind'] == 'ExperimentDraft' for o in objects):
                raise ValueError('实验草稿用submit_experiment_draft接续，保留真实任务绑定；不能走普通编辑绕过')
            produce = any(o['kind'] in ('LearningDocument', 'AnalysisProgram') for o in objects)
        binding = self.require(experiment_id, job_id) if produce and experiment_id else None
        previous = getattr(self.gateway._workflow, 'draft_binding', None)
        if binding:
            self.gateway._workflow.draft_binding = binding
        try:
            yield binding
        finally:
            self.gateway._workflow.draft_binding = previous
