"""Resolve explanatory measurements from one fixed experimental design."""
import re
from decimal import Decimal
from .document_review import calculate_values


def source_union(content):
    found = {}
    def visit(item, path):
        if isinstance(item,list):
            for i,child in enumerate(item):visit(child,f"{path}[{i}]")
        elif isinstance(item,dict):
            for key,child in item.items():
                if key=="sources":
                    if not isinstance(child,list) or any(not isinstance(s,str) for s in child):
                        raise ValueError(f"{path}.sources 应为来源标识数组")
                    found.update(dict.fromkeys(child))
                elif key=="source_anchor":
                    if not isinstance(child,str):raise ValueError(f"{path}.source_anchor 应为来源标识")
                    found[child]=None
                else:visit(child,f"{path}.{key}")
    visit(content,"content")
    return list(found)


def index(items, name):
    mapping={item["id"]:item for item in items}
    if len(mapping)!=len(items):raise ValueError(name+"标识重复")
    return mapping


def setup_gaps(draft):
    return [f"{group}[{i}].setup_id" for group in ('observations','branches')
            for i,item in enumerate(draft.get(group,[])) if not item.get("setup_id")]


def check_design(draft,bind_setup=False):
    observations=index(draft.get("observations",[]),"观测")
    if bind_setup and (gaps:=setup_gaps(draft)):
        raise ValueError('请给观测和分支注明真实测量配置，相同配置共用setup_id：'+', '.join(gaps))
    for branch in index(draft.get("branches",[]),"条件分支").values():
        available=set(branch["observables"])
        prior=set(branch.get("prior_observations",[]))
        unknown=(available|prior)-observations.keys()
        if unknown:raise ValueError(f"分支 {branch['id']} 引用了未定义观测 {sorted(unknown)}")
        if bind_setup:
            wrong=[key for key in available if observations[key]['setup_id']!=branch['setup_id']]
            if wrong:raise ValueError(f"分支 {branch['id']} 在当前配置不能直接读 {wrong}，只列这里实际能取得的读数。若沿用先前的独立测量，用 prior_observations 并说明原配置。")
            if prior & available:raise ValueError("同一观测不能同时是本配置取得和沿用的先前读数")


def example_rows(block,snapshot,draft_ref,*,human_units=False):
    from .display_text import display_unit
    def with_unit(value,unit):
        if human_units:unit='' if unit.strip()=='1' else display_unit(unit)
        return value+(' '+unit if unit else '')
    if not draft_ref:raise ValueError("算例需要固定实验草稿中的观测定义")
    draft=snapshot.object(draft_ref["id"],draft_ref["revision"])["content"]
    branch=index(draft.get("branches",[]),"分支").get(block["branch"])
    if branch is None:raise ValueError("算例分支未在固定底稿定义："+block["branch"])
    observations=index(draft.get("observations",[]),"观测")
    variables={}; rows=[]
    def unique(key):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*",key) or key in variables:
            raise ValueError("算例变量须为唯一计算标识："+key)
    for item in block["inputs"]:
        obs=observations.get(item["observation"]);key=item["key"];unique(key)
        if obs is None or obs["id"] not in branch["observables"]+branch.get("prior_observations",[]):
            raise ValueError(f"该分支无法读取 {item['observation']}；核对装置、判据与适用状态")
        if item["origin"]=="reference":
            anchor=item.get("source_anchor")
            if not anchor:raise ValueError("引用材料数字须绑定含该读数的原文引文")
            quote=snapshot.anchor(anchor)["quote"]
            if not snapshot.anchor(anchor).get("quote_in_original",True):
                raise ValueError(f"{key} 的引文须与本页原始文字一致")
            numbers=re.findall(r"(?<![\d.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?",quote)
            if not any(Decimal(n)==Decimal(str(item["value"])) for n in numbers):
                raise ValueError(f"{key} 的原文引文不含所称数值；请核对该观测原始定义")
            origin="材料示值"
        else:
            if item.get("source_anchor"):raise ValueError("自设读数不能标作原文数值")
            origin="假设读数"
        variables[key]=item["value"]
        earlier='沿用'+obs['setup']+'的读数，' if obs['id'] in branch.get('prior_observations',[]) else obs['setup']+'，'
        rows.append([obs["quantity"],earlier+obs["criterion"],with_unit(f"{item['value']:g}",obs['unit'])+f"（{origin}）"])
    for step in block["calculations"]:
        key=step["key"];unique(key)
        val=calculate_values([{"expression":step["expression"],"variables":variables}])["results"][0]["value"]
        variables[key]=val
        rows.append([step["quantity"],step["method"],with_unit(f"{val:.{step.get('significant_digits',3)}g}",step['unit'])])
    return branch,rows


def branch_rows(block,snapshot,draft_ref):
    if not draft_ref:raise ValueError("条件表需要固定实验草稿")
    draft=snapshot.object(draft_ref["id"],draft_ref["revision"])["content"]
    observations=index(draft.get("observations",[]),"观测");branches=index(draft.get("branches",[]),"分支")
    rows=[]
    def reading(identity):
        obs=observations[identity]
        if not block.get('show_observation_origins'):return obs['quantity']+'（'+obs['criterion']+'）'
        from .display_text import display_unit
        unit=display_unit(obs['unit']) if obs['unit']!='1' else ''
        title=obs['quantity']+(' / '+unit if unit else '')
        return f"{title}（{obs['setup']}，{obs['criterion']}）"
    for key in block["branches"]:
        if key not in branches:raise ValueError("条件表引用了不存在的底稿分支："+key)
        branch=branches[key]
        reads=[reading(o) for o in branch["observables"]]
        action=branch['action']
        if block.get('show_observation_origins') and branch.get('prior_observations'):
            earlier='；'.join(reading(o) for o in branch['prior_observations'])
            action='沿用先前测得的'+earlier+'。\n'+action
        rows.append([branch["condition"],"；".join(reads) or "记录所见现象",action])
    return rows
