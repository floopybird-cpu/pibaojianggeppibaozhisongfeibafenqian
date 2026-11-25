"""Bounded interpreter for data-only format programs.

Programs have no eval, imports, callbacks, paths, SQL, credentials or host handles.
Only the opcodes below are executable. Domain schemas/rules live in each package.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path


OPERATIONS={"schema","rules","normalize","view","docx","pdf","workbook","program_files","copy_resources","required_assets","upgrade_preview","migrate","runtime_environment","analysis_inputs","experiment_draft_files"}
OPERATIONS.add("collect_document_sources")
OPERATIONS.add("worksheet_definition")
CHECKS={"scope","subset","set_contains","equal_sets","unique","math","python_syntax","reference_match","image_exists","required_when","table_width","catalog_subset","workbook","composition","experiment_draft"}
SCHEMA_KEYS={"type","const","enum","oneOf","required","properties","additionalProperties","items","minLength","maxLength","minItems","maxItems","minimum","maximum","x-source-anchor","x-reference-types"}
CHECKS.add("worksheet")


def schema_program_supported(schema):
    unknown=set(schema)-SCHEMA_KEYS
    if unknown:raise ValueError("未支持的结构指令："+", ".join(sorted(unknown)))
    for child in schema.get("properties",{}).values():schema_program_supported(child)
    if "items" in schema:schema_program_supported(schema["items"])
    for child in schema.get("oneOf",[]):schema_program_supported(child)


def values(value, path):
    parts=path.split(".") if isinstance(path,str) else path
    if not parts:
        return [value]
    head,*tail=parts
    if head=="*":
        children=list(value.values()) if isinstance(value,dict) else value if isinstance(value,list) else []
        return [v for child in children for v in values(child,tail)]
    if isinstance(value,dict) and head in value:
        return values(value[head],tail)
    return []


def check_schema(value, schema, snapshot, path="content"):
    if "const" in schema and value!=schema["const"]:
        raise ValueError(path+" 必须为 "+str(schema["const"]))
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(path+" 的取值不支持")
    if "oneOf" in schema:
        matches=0;relevant_error=None
        for candidate in schema["oneOf"]:
            try:
                check_schema(value,candidate,snapshot,path);matches+=1
            except (ValueError,TypeError,KeyError) as exc:
                discriminators={key:spec['const'] for key,spec in candidate.get('properties',{}).items() if 'const' in spec}
                if isinstance(value,dict) and discriminators and all(value.get(key)==expected for key,expected in discriminators.items()):relevant_error=exc
        if matches!=1:
            if relevant_error is not None:raise ValueError(str(relevant_error))
            raise ValueError(path+" 不匹配包声明的任一单一结构")
    expected=schema.get("type")
    valid={"object":isinstance(value,dict),"array":isinstance(value,list),"string":isinstance(value,str),
           "integer":isinstance(value,int) and not isinstance(value,bool),"number":isinstance(value,(int,float)) and not isinstance(value,bool),"boolean":isinstance(value,bool),"null":value is None}
    if expected and not valid.get(expected,False):
        raise ValueError(path+" 类型应为 "+str(expected))
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if value<schema.get("minimum",float('-inf')) or value>schema.get("maximum",float('inf')):
            raise ValueError(path+" 超出包声明范围")
    if isinstance(value,str):
        if len(value.strip())<schema.get("minLength",0):
            raise ValueError(path+" 不能为空")
        if "maxLength" in schema and len(value)>schema["maxLength"]:
            raise ValueError(path+" 过长")
    if isinstance(value,list):
        if len(value)<schema.get("minItems",0) or ("maxItems" in schema and len(value)>schema["maxItems"]):
            raise ValueError(path+" 数量不符合包声明")
        for i,item in enumerate(value):
            check_schema(item,schema.get("items",{}),snapshot,path+f"[{i}]")
    if isinstance(value,dict):
        for field in schema.get("required",[]):
            if field not in value:
                raise ValueError(path+" 缺字段 "+field)
        properties=schema.get("properties",{})
        if schema.get("additionalProperties") is False and set(value)-set(properties):
            raise ValueError(path+" 含包未声明字段 "+", ".join(sorted(set(value)-set(properties))))
        for field,subschema in properties.items():
            if field in value:
                check_schema(value[field],subschema,snapshot,path+"."+field)
    if schema.get("x-source-anchor"):
        snapshot.anchor(value)
    if "x-reference-types" in schema:
        obj=snapshot.object(value["id"],value["revision"])
        if schema["x-reference-types"] and obj["kind"] not in schema["x-reference-types"]:
            raise ValueError(path+" 的引用对象类型不符")


class Snapshot:
    """Finite readonly input adapter. No authority connection or writable handle."""
    def __init__(self, objects, anchors, pages, image_paths, experiment, datasets=None):
        self._objects=copy.deepcopy(objects);self._anchors=copy.deepcopy(anchors)
        self._pages=copy.deepcopy(pages);self._image_paths=dict(image_paths)
        self.experiment=copy.deepcopy(experiment)
        self.datasets=copy.deepcopy(datasets or {})
    def object(self, identity, revision=None):
        key=(identity,revision)
        if key not in self._objects:
            raise ValueError("引用不在本次冻结输入中")
        return copy.deepcopy(self._objects[key])
    def anchor(self, identity):
        if identity not in self._anchors:
            raise ValueError("来源不在本次冻结输入中")
        return copy.deepcopy(self._anchors[identity])
    def one(self, sql, args):
        # Adapter for the shared renderer; this never runs SQL.
        if sql!="SELECT image FROM pages WHERE source_id=? AND number=?":
            raise ValueError("生成器查询不在有限接口中")
        return copy.deepcopy(self._pages.get(tuple(args)))
    def asset_path(self, relative):
        if relative not in self._image_paths:
            raise ValueError("图像不在冻结输入中")
        return self._image_paths[relative]


class Program:
    def __init__(self, pack):
        directory=Path(pack["directory"])
        self.definition=json.loads((directory/"program.json").read_text(encoding="utf-8"))
        self.schemas=json.loads((directory/"schema.json").read_text(encoding="utf-8"))
        for schema in self.schemas.values():schema_program_supported(schema)
        self.pack=pack
        if self.definition.get("language")!="format-pipeline/1":
            raise ValueError("包程序语言不兼容")
        for name in ("read","query","apply","build","finish","planUpgrade","upgrade"):
            if not isinstance(self.definition.get(name),list) or not self.definition[name]:
                raise ValueError("包程序缺入口 "+name)
            for step in self.definition[name]:
                if step.get("op") not in OPERATIONS:
                    raise ValueError("未支持或越权的程序操作 "+str(step.get("op")))
                for rule in step.get("checks",[]):
                    if rule.get("check") not in CHECKS:
                        raise ValueError("包检查指令未知或越权 "+str(rule.get("check")))
                    if rule.get('template') and (Path(rule['template']).name!=rule['template'] or not (directory/rule['template']).is_file()):
                        raise ValueError('检查模板不在包内')
                if step.get("template") and (Path(step["template"]).name!=step["template"] or not (directory/step["template"]).is_file()):
                    raise ValueError("生成模板不在包内")
                for filename in step.get("files",[]) if isinstance(step.get("files"),list) else step.get("files",{}):
                    if Path(filename).name!=filename:
                        raise ValueError("生成资产名越出暂存区")

    def execute(self, entry, *, kind, content, snapshot=None, out=None, extra=None):
        value=copy.deepcopy(content)
        for step in self.definition[entry]:
            if step.get("kinds") and kind not in step["kinds"]:
                continue
            op=step["op"]
            if op=="schema":
                if kind not in self.schemas:
                    raise ValueError("包未定义此对象结构")
                check_schema(value,self.schemas[kind],snapshot)
            elif op=="collect_document_sources":
                from .observation_paths import source_union
                value["sources"]=source_union(value)
                if step.get("branch_origins"):
                    from .document_composition import all_blocks
                    for block in all_blocks(value):
                        if block.get("type")=="branch_table":block["show_observation_origins"]=True
            elif op=="runtime_environment":
                environment=(extra or {}).get("runtime_environment")
                if not environment:raise ValueError("缺少宿主核实的固定运行环境")
                value["environment"]=copy.deepcopy(environment)
                available={k.casefold():v for k,v in environment["dependencies"].items()}
                value["environment"]["required_dependencies"]={name:available[name.casefold()] for name in self.pack.get("dependencies",{})}
            elif op=="analysis_inputs":
                requested=value["inputs"]["dataset_ids"]
                if any(identity not in snapshot.datasets for identity in requested):raise ValueError("缺少本程序的完整固定数据快照")
                (out/"inputs.json").write_text(json.dumps({"datasets":[snapshot.datasets[i] for i in requested],"simulation":value["simulation"]},ensure_ascii=False,indent=2),encoding="utf-8")
            elif op=="normalize":
                # Only finite field normalization, never interpretation or eval.
                for field in step.get("trim",[]):
                    if isinstance(value.get(field),str):value[field]=value[field].strip()
                for field in step.get("deduplicate",[]):
                    if isinstance(value.get(field),list):
                        value[field]=list({json.dumps(v,ensure_ascii=False,sort_keys=True):v for v in value[field]}.values())
            elif op=="rules":
                for rule in step.get("checks",[]):
                    self.rule(rule,value,snapshot,extra or {})
            elif op=="view":
                # View fields/labels belong to the package. The UI also retains
                # ordinary content/asset reads even when the fixed pack is lost.
                return {"fields":[{"path":f["path"],"label":f["label"],"presentation":f.get("presentation","text"),"values":values(value,f["path"])} for f in step.get("fields",[]) if values(value,f["path"])],"content":value}
            elif op=="docx":
                from .documents import build_document
                build_document(value,out,snapshot,Path(self.pack["directory"])/step["template"])
            elif op=="worksheet_definition":
                (out/"definition.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            elif op=='experiment_draft_files':
                from .experiment_drafts import build_outline
                build_outline(value,out)
            elif op=="pdf":
                from .rich_documents import docx_to_pdf
                config=json.loads((Path(self.pack["directory"])/step["template"]).read_text(encoding="utf-8"))
                if config.get("composition")=="markdown-layout/1":
                    from .document_composition import pdf_design
                    pdf_config=pdf_design(value,Path(self.pack["directory"])/step["template"])
                else:pdf_config=config.get("pdf",{})
                docx_to_pdf(out/"document.docx",out/"document.pdf",pdf_config)
                import pymupdf
                with pymupdf.open(out/'document.pdf') as document:
                    layout={'pdf_pages':len(document),'page_sizes_mm':[[round(p.rect.width*25.4/72,2),round(p.rect.height*25.4/72,2)] for p in document], 'last_page_text_characters':len(document[-1].get_text()),'word_pages':'Office打开后核实，PDF页数不能代替Word页数'}
                    if config.get('reading_edition')==2:
                        layout['figure_pages']=[];layout['outside_page']=[]
                        for page in document:
                            if page.get_image_info():layout['figure_pages'].append(page.number+1)
                            bounds=page.rect+(-1,-1,1,1)
                            for block in page.get_text('blocks'):
                                if not bounds.contains(pymupdf.Rect(block[:4])):
                                    layout['outside_page'].append({'page':page.number+1,'box':list(block[:4])})
                (out/'pagination.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2),encoding='utf-8')
            elif op=="workbook":
                from .data_workbooks import build_workbook
                build_workbook(value,out,snapshot,include_context=bool(step.get('source_context',False)))
            elif op=="program_files":
                (out/"analysis.py").write_text(value["code"],encoding="utf-8")
                for name,field in step["files"].items():
                    if Path(name).name!=name:
                        raise ValueError("包输出名称必须是单个文件名")
                    (out/name).write_text(json.dumps(value[field],ensure_ascii=False,indent=2),encoding="utf-8")
            elif op=="required_assets":
                required=step.get("by_kind",{}).get(kind,step.get("files",[]))
                if out is not None and any(not (out/name).is_file() or not (out/name).stat().st_size for name in required):
                    raise ValueError("包缺必需交付，未完成："+", ".join(required))
            elif op=="copy_resources":
                for source,name in step["files"].items():
                    if Path(source).name!=source or Path(name).name!=name:raise ValueError("包资源复制路径越界")
                    (out/name).write_bytes((Path(self.pack["directory"])/source).read_bytes())
            elif op=="upgrade_preview":
                missing=[key for key in self.schemas[kind].get("required",[]) if key not in content]
                if step.get('check_measurement_setup'):
                    from .observation_paths import setup_gaps
                    missing.extend(setup_gaps(content))
                return {"migration":self.definition.get("upgrade",[]),"preserves_content":True,"requires_content_work":bool(missing),"missing_fields":missing}
            elif op=="migrate":
                for action in step.get("operations",[]):
                    operation=action.get('operation')
                    if operation=='set_style_default':value.setdefault("style",{}).setdefault(action["field"],action["value"])
                    elif operation in ('map_enum','set_style_by_enum'):
                        field=action['field'];mapping=action['values']
                        if not isinstance(mapping,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in mapping.items()):raise ValueError('枚举迁移须为固定字符串映射')
                        original=value.get(field)
                        if original not in mapping:continue
                        if operation=='map_enum':
                            allowed=self.schemas[kind]['properties'].get(field,{}).get('enum',[])
                            if any(v not in allowed for v in mapping.values()):raise ValueError('迁移目标不是包声明的类型枚举')
                            value[field]=mapping[original]
                        else:value.setdefault('style',{}).setdefault(action['style_field'],mapping[original])
                    else:raise ValueError('不支持的有限迁移操作；科学内容变更须经正式内容修订')
        return value

    def rule(self, rule, content, snapshot, extra):
        if rule.get("kinds") and extra.get("kind") not in rule["kinds"]:
            return
        condition=rule.get("when")
        if condition and values(content,condition["path"])!=[condition["equals"]]:
            return
        op=rule["check"]
        if op=="worksheet":
            from .worksheet_math import check_id, validate_page
            seen=set()
            for page in content["pages"]:
                check_id(page["id"], seen)
                validate_page(page)
            ref=content["draft_ref"]
            if snapshot.object(ref["id"],ref["revision"])["experiment_id"] != snapshot.experiment["id"]:
                raise ValueError("草稿与数据页必须属于同一实验")
            return
        if op=='experiment_draft':
            from .experiment_drafts import validate_outline
            validate_outline(content,rule.get('bind_measurement_setup',False))
            return
        if op=="composition":
            from .document_composition import validate_composition
            validate_composition(content,snapshot,Path(self.pack["directory"])/rule["template"],self.schemas["LearningDocument"]["properties"]["sections"]["items"]["properties"]["blocks"]["items"])
            return
        if op=="workbook":
            from .data_workbooks import validate_workbook
            validate_workbook(content,snapshot)
            return
        if op=="scope":
            if rule["value"]=="global" and snapshot.experiment:
                raise ValueError("本包对象只能属于全局知识")
            if rule["value"]=="experiment" and not snapshot.experiment:
                raise ValueError("本包对象必须绑定一个实验")
        elif op in ("subset","set_contains","equal_sets","unique"):
            left=[v for path in rule.get("paths",[rule.get("path","")]) for v in values(content,path)]
            if op=="unique":
                if len(left)!=len(set(map(str,left))):
                    raise ValueError(rule["message"])
            else:
                right=rule.get("values",[v for path in rule.get("other_paths",[rule.get("other","")]) for v in values(content,path)])
                if op=="subset" and not set(left)<=set(right) or op=="set_contains" and not set(right)<=set(left) or op=="equal_sets" and set(left)!=set(right):
                    raise ValueError(rule["message"])
        elif op=="math":
            from .documents import formula_omml
            for latex in values(content,rule["path"]):formula_omml(latex)
        elif op=="python_syntax":
            for code in values(content,rule["path"]):compile(code,"analysis.py","exec")
        elif op=="reference_match":
            for node in values(content,rule["path"]):
                if rule["reference"] not in node:continue
                reference=node[rule["reference"]]
                obj=snapshot.object(reference["id"],reference["revision"])
                if values(node,rule["field"])!=values(obj["content"],rule["reference_field"]):
                    raise ValueError(rule["message"])
        elif op=="image_exists":
            for anchor_id in values(content,rule["path"]):
                a=snapshot.anchor(anchor_id)
                page=snapshot.one("SELECT image FROM pages WHERE source_id=? AND number=?",(a["source_id"],a["page"]))
                if not page or not page["image"]:raise ValueError("图块来源没有真实页图")
        elif op=="required_when":
            if any(not content.get(field) for field in rule["fields"]):
                raise ValueError(rule["message"])
        elif op=="table_width":
            for path in rule["paths"]:
                for block in values(content,path):
                    if block.get("type")=="table" and any(len(row)!=len(block["columns"]) for row in block["rows"]):
                        raise ValueError("表格数据列数与列标题不一致")
        elif op=="catalog_subset":
            filename=rule["file"]
            if Path(filename).name!=filename:raise ValueError("包目录查询越界")
            catalog=json.loads((Path(self.pack["directory"])/filename).read_text(encoding="utf-8"))
            allowed={item[rule["id_field"]] for item in catalog[rule["key"]]}
            if not set(values(content,rule["path"]))<=allowed:raise ValueError(rule["message"])
        else:
            raise ValueError("包规则不支持此检查："+op)
