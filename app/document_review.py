"""Read the actual crop/output and calculate examples without executable code.

These tools provide observations, not a claim that the underlying science is
correct. Results and images use the ordinary durable tool-result journal.
"""
from __future__ import annotations

import ast
import base64
import io
import math
import operator


def vision_enabled(agent, job_id):
    import json
    job = agent.store.one('SELECT payload FROM jobs WHERE id=?', (job_id,))
    options = json.loads(job['payload']).get('request_options', {}) if job else {}
    return options.get('native_vision', agent.settings.read()['native_vision'])


def anchor_preview(store, anchor, include_context=True):
    from PIL import Image,ImageDraw
    page = store.one('SELECT image FROM pages WHERE source_id=? AND number=?',
                     (anchor['source_id'], anchor['page']))
    if not page or not page['image']:
        raise ValueError('该锚点没有原页图')
    with Image.open(store.asset_path(page['image'])) as original:
        image = original.convert('RGB');context=image.copy();box=None
        if anchor.get('region'):
            box=tuple(round(v * (image.width if i % 2 == 0 else image.height)) for i,v in enumerate(anchor['region']))
            image = image.crop(box)
        output = io.BytesIO(); image.save(output, format='PNG')
        gray=image.convert('L');w,h=image.size
        edges={'left':[(0,y) for y in range(h)],'right':[(w-1,y) for y in range(h)],
               'top':[(x,0) for x in range(w)],'bottom':[(x,h-1) for x in range(w)]}
        result={'anchor_id': anchor['id'], 'page': anchor['page'], 'region': anchor.get('region'),
                'width': image.width, 'height': image.height,
                'dark_pixels_at_crop_edges':{name:sum(gray.getpixel(p)<100 for p in points) for name,points in edges.items()},
                'edge_check_scope':'边缘墨迹是截断提示，不自动证明有错；对照红框原页检查完整回线、箭头、标签及无关残文。红框不进入文稿。',
                '_image': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()}
        if box and include_context:
            ImageDraw.Draw(context).rectangle(box,outline=(210,35,35),width=3)
            full=io.BytesIO();context.save(full,format='PNG')
            result['source_context']={'description':'同一原页上的实际裁框，仅作核对，不是最终图片',
                '_image':'data:image/png;base64,'+base64.b64encode(full.getvalue()).decode()}
        return result


def document_pdf(agent, identity, revision, experiment_id):
    from .storage import digest
    obj=agent.gateway.read(identity,revision)
    if obj['kind']!='LearningDocument' or obj['experiment_id']!=experiment_id:
        raise ValueError('只能检查当前实验的文稿')
    asset=next((a for a in obj['assets'] if a['name']=='document.pdf'),None)
    if not asset:raise ValueError('此修订尚无PDF')
    path=agent.store.asset_path(asset['path'])
    if digest(path.read_bytes())!=asset['sha']:raise ValueError('实际文件与固定修订摘要不符')
    return obj,asset,path


def page_fingerprints(agent, asset, pdf):
    import fitz
    from .storage import digest
    if not hasattr(agent,'_review_page_hashes'):agent._review_page_hashes={}
    cached=agent._review_page_hashes.get(asset['sha'])
    if cached is None:
        cached=[digest(p.get_pixmap(matrix=fitz.Matrix(1.6,1.6)).tobytes('png')) for p in pdf]
        agent._review_page_hashes[asset['sha']]=cached
    return cached


def inspection_receipts(result):
    found=[]
    if isinstance(result,dict):
        if all(k in result for k in ('id','revision','page','page_fingerprint','pdf_sha256')):
            found.append({k:result[k] for k in ('id','revision','page','page_fingerprint','pdf_sha256')})
        for key in ('results','result'):
            if key in result:found.extend(inspection_receipts(result[key]))
    elif isinstance(result,list):
        for item in result:found.extend(inspection_receipts(item))
    return found


def pending_page_reviews(agent,documents,completed,experiment_id):
    import fitz
    observed={(r['id'],r['page_fingerprint']) for c in completed for r in c.get('document_inspections',[])}
    pending=[]
    for identity,revision in documents.items():
        obj,asset,path=document_pdf(agent,identity,revision,experiment_id)
        with fitz.open(path) as pdf:hashes=page_fingerprints(agent,asset,pdf)
        missing=[i+1 for i,h in enumerate(hashes) if (identity,h) not in observed]
        if missing:pending.append({'id':identity,'revision':revision,'pages_not_yet_viewed':missing})
    return pending


def inspect_document(agent, args, experiment_id, job_id):
    import fitz
    obj,asset,path=document_pdf(agent,args['id'],args.get('revision'),experiment_id)
    with fitz.open(path) as pdf:
        number = args.get('page', 1)
        if not 1 <= number <= len(pdf):
            raise ValueError('页码不在实际PDF范围内：1–' + str(len(pdf)))
        page = pdf[number - 1];hashes=page_fingerprints(agent,asset,pdf)
        result = {'id': obj['id'], 'revision': obj['revision'], 'title': obj['title'],
                  'pdf_pages': len(pdf), 'page': number, 'text': page.get_text(),
                  'pdf_sha256': asset['sha'],
                  'page_fingerprint':hashes[number-1],
                  'inspection_scope': '仅该页真实PDF；不证明Word分页、用户已打开或整稿科学正确'}
        if vision_enabled(agent, job_id):
            result['_image'] = 'data:image/png;base64,' + base64.b64encode(
                page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6)).tobytes('png')).decode()
        else:
            result['image_status'] = '用户关闭了原生图像，本次仅返回实际文字，不能声称视觉核对'
        if obj['revision']>1:
            _,previous,old_path=document_pdf(agent,obj['id'],obj['revision']-1,experiment_id)
            with fitz.open(old_path) as old_pdf:old_hashes=page_fingerprints(agent,previous,old_pdf)
            result['pages_changed_since_previous_revision']=[i+1 for i,h in enumerate(hashes)
                if i>=len(old_hashes) or h!=old_hashes[i]]
        return result


def calculate_values(items):
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
    functions = {name: getattr(math, name) for name in
                 ('sqrt', 'exp', 'log', 'log10', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan')}
    functions.update(abs=abs, min=min, max=max)
    results = []
    for item in items:
        variables = item.get('variables', {})
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
               for v in variables.values()):
            raise ValueError('变量须为有限实数，单位先显式换算')
        def value(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float): return node.value
            if isinstance(node, ast.Name):
                if node.id in variables: return variables[node.id]
                if node.id in ('pi', 'e'): return getattr(math, node.id)
                raise ValueError('未定义变量：' + node.id)
            if isinstance(node, ast.BinOp) and type(node.op) in binary:
                return binary[type(node.op)](value(node.left), value(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return value(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and not node.keywords:
                return functions[node.func.id](*(value(n) for n in node.args))
            raise ValueError('只支持数值、变量、四则/幂与公开数学函数；不执行代码')
        number = value(ast.parse(item['expression'], mode='eval').body)
        if not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError('表达式没有有限实数结果')
        results.append({**item, 'value': number})
    return {'results': results, 'scope': '按所给表达式复算；物理模型、符号方向及单位仍需核对。'}
