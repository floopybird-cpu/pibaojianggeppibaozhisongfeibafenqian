"""Map each draft method and its source equations to the actual recorded quantities."""
import copy
import json
import re
from pathlib import Path

from . import worksheet_evidence as evidence
from .storage import canonical
from .worksheet_design import page_from_receipt
from .worksheet_identity import corroborate, formula_items
from .worksheet_math import bound_tree, scalar_aliases
from .worksheet_units import quantity_context, transform


def measurement_inventory(raw):
    if isinstance(raw.get("pages"), list) and len(raw["pages"]) == 1:
        raw = raw["pages"][0]
    def quantities(items,role):
        return [{**quantity_context(v),"execution_role":role} for v in items if isinstance(v, dict)]
    return {"unit_system":"SI", "single_readings": quantities(raw.get("fields", []),"recorded_scalar"),
            "tables": [{**{k:copy.deepcopy(v) for k,v in t.items() if k not in ("columns","derived")},
                        "readings": quantities(t.get("columns", []),"recorded_series"),
                        "row_values": quantities(t.get("derived", []),"derived_series")}
                       for t in raw.get("tables", []) if isinstance(t, dict)],
            "scalar_values": quantities(raw.get("results", []),"calculated_scalar"),
            "unresolved":copy.deepcopy(raw.get("unresolved",[]))}


def draft_source_labels(service,draft,catalog):
    """Use provider-facing page labels only in the request copy of a saved draft."""
    result=copy.deepcopy(draft)
    labels={(v.get("source"),v.get("number")):key for key,v in catalog.items()}
    for stage in result.get("stages",[]):
        for point in stage.get("points",[]):
            sources=[]
            for source in point.get("sources",[]):
                if source not in catalog:
                    anchor=service.store.anchor(source)
                    source=labels.get((anchor["source_id"],anchor["page"]),source)
                if source not in sources:sources.append(source)
            if "sources" in point:point["sources"]=sources
    return result


def lecture_order(parts,catalog):
    """Use a shared course overview only when it gives each method its own ordered section."""
    def pages(part,source):
        return {catalog[k]["number"] for k in part.get("material_pages",[]) if k in catalog and catalog[k]["source"]==source}
    overviews={r["source"] for r in catalog.values() if re.search(r"课件|PPT|课程大纲|实验总览",r["title"],re.I)}
    for source in sorted(overviews):
        sections=[pages(p,source) for p in parts]
        unique=[s-set().union(*(o for j,o in enumerate(sections) if j!=i)) for i,s in enumerate(sections)]
        if all(unique):
            order=sorted(range(len(parts)),key=lambda i:min(unique[i]))
            if all(max(unique[i])<min(unique[j]) for i,j in zip(order,order[1:])):
                return [parts[i] for i in order]
    return parts


def by_id(items):
    return {v["id"]: v for v in items if isinstance(v, dict) and isinstance(v.get("id"), str) and v["id"]}


def valid_unit(unit):
    try:
        transform(unit)
    except (TypeError, ValueError, SyntaxError, AttributeError, KeyError):
        return False
    return isinstance(unit, str)


def merge_inputs(data, part, reading, allowed, pending):
    fields, tables = by_id(data["fields"]), by_id(data["tables"])
    columns = {c["id"]: (t["id"], c) for t in tables.values() for c in t["columns"]}
    calculated=by_id(formula_items(data))
    declared = by_id(part.get("observables", []))
    amendments = by_id(reading.get("quantities", []))
    for key in dict.fromkeys([*declared, *amendments]):
        spec = declared.get(key, {})
        edit = amendments.get(key, {})
        page = edit.get("page") or spec.get("source")
        if page not in allowed:
            continue
        form = spec.get("form") or edit.get("form") or "reading"
        unit = spec.get("unit") or edit.get("unit")
        label = edit.get("label") or spec.get("label")
        table_id = edit.get("table") or (columns[key][0] if key in columns else None)
        item = columns[key][1] if key in columns else fields.get(key)
        # An observable can name a measured aggregate or a group of raw columns.
        # Neither creates a second independent input with the same identity.
        if key in tables and item is None:continue
        if item is None and (key in calculated or spec.get("derived_from")):
            item=calculated.get(key)
            if item is None:
                pending.append({"label":label or key,"reason":"该观测声明由其他读数得到，尚未交回可执行关系",
                                "definition":copy.deepcopy(spec)})
                continue
        if item is None:
            if not label or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", key) or not valid_unit(unit):
                pending.append({"label": label or key, "reason": "读数缺少可用的标识、物理量名或单位", "definition": {"id": key}})
                continue
            item = {"id": key, "label": label, "unit": unit}
            if form in ("series", "observation") or table_id:
                if not table_id or not isinstance(table_id, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", table_id):
                    pending.append({"label": label, "reason": "尚未确定该观测属于哪一组同次记录", "definition": item})
                    continue
                if table_id not in tables:
                    tables[table_id] = {"id": table_id, "title": edit.get("table_title") or label + "记录", "rows": 3, "columns": []}
                tables[table_id]["columns"].append(item)
                columns[key] = (table_id, item)
                if form == "observation":
                    item["type"] = "text"
            else:
                fields[key] = item
        if label:
            item["label"] = label
        meaning = edit.get("meaning") or spec.get("meaning")
        if meaning:
            item["hint"] = meaning
        reference = edit.get("reference")
        if isinstance(reference, dict) and key in fields:
            item["reference"] = {**reference, "page": page}
        if form == "constant" and key in fields:
            # A source parameter is editable, and always exists before same-row expressions.
            data["results"][:] = [v for v in data["results"] if v["id"] != key]
    data["fields"] = list(fields.values())
    data["tables"] = list(tables.values())


def merge_windows(data, reading, catalog, allowed, pending, shown_images):
    tables = by_id(data["tables"])
    fields = {f["id"]: f["unit"] for f in data["fields"]}
    windows = {}
    for w in reading.get("windows", []):
        if isinstance(w, dict) and w.get("table") in tables:
            windows.setdefault(w["table"], []).append(w)
    for table_id, proposed in windows.items():
        table = tables[table_id]
        w = proposed[0]
        source = w.get("page")
        columns = by_id(table["columns"] + table.get("derived", []))
        axis = columns.get(w.get("column"))
        bounds = {key: w[key] for key in ("min", "max") if w.get(key)}
        try:
            if len(proposed) != 1:
                raise ValueError("同一采样表收到多个不同的取数范围")
            if source not in allowed or not (evidence.matched_passage(catalog[source].get("text", ""), w.get("quote"))
                                            or source in shown_images and w.get("locator")):
                raise ValueError("原件中尚未定位这项取数条件")
            if not axis or not bounds:
                raise ValueError("尚未绑定原件指定的筛选列和取数界限")
            for expression in bounds.values():
                bound_tree(expression, fields, axis["unit"], True, scalar_aliases(data))
        except (KeyError, TypeError, ValueError, SyntaxError) as err:
            problem={"label": table["title"] + "的取数范围", "reason": str(err), "definition": w}
            previous=table.get("selection")
            try:
                previous_axis=columns[previous["column"]]
                for side in ("min","max"):
                    if previous.get(side):bound_tree(previous[side],fields,previous_axis["unit"],True,scalar_aliases(data))
            except (KeyError,TypeError,ValueError,SyntaxError):
                table.pop("selection", None)
                table["selection_pending"] = str(err)
            else:problem["retained_definition"]=copy.deepcopy(previous)
            pending.append(problem)
        else:
            table.pop("selection_pending", None)
            table["selection"] = {"column": axis["id"], **bounds}


def reconcile_page(raw, part, reading, catalog, shown_images=()):
    if isinstance(raw.get("pages"), list) and len(raw["pages"]) == 1:
        raw = raw["pages"][0]
    data = {k: copy.deepcopy(raw.get(k, [])) for k in ("fields", "tables", "results", "plots", "sources")}
    for table in data["tables"]:
        table.setdefault("derived", [])
    original = page_from_receipt(copy.deepcopy(raw),part,catalog)
    allowed = set(part.get("material_pages", [])) & catalog.keys()
    pending = []
    merge_inputs(data, part, reading, allowed, pending)
    tables = by_id(data["tables"])
    results = by_id(data["results"])
    targets = by_id(part.get("targets", []))
    for target in reading.get("targets", []) + reading.get("derived", []):
        if not isinstance(target, dict) or any(not isinstance(target.get(k), str) or not target[k] for k in ("id", "label", "unit", "expr")):
            continue
        basis = target.get("basis")
        if not isinstance(basis, dict) or basis.get("page") not in allowed:
            continue
        key = target["id"]
        if key in {f["id"] for f in data["fields"]}:
            continue
        table_id = target.get("series_table")
        if table_id:
            if table_id not in tables:
                pending.append({"label": target["label"], "reason": "逐行计算没有关联到本方法的读数表", "definition": target})
                continue
            values = by_id(tables[table_id].get("derived", []))
            values[key] = {**values.get(key, {}), **target}
            tables[table_id]["derived"] = list(values.values())
            results.pop(key, None)
        else:
            existing = results.get(key, {})
            role = "primary" if key in targets else target.get("role", existing.get("role", "intermediate"))
            results[key] = {**existing, **target, "role": role}
            for table in tables.values():
                table["derived"]=[v for v in table.get("derived",[]) if v["id"]!=key]
    data["results"] = list(results.values())
    merge_windows(data, reading, catalog, allowed, pending, set(shown_images))
    # Compare only executable complete graphs under the same SI, dimensional and
    # dependency contract. A rejected proposal is not evidence against an old branch.
    candidate = page_from_receipt(copy.deepcopy(data), part, catalog)
    valid_new={v["id"] for v in formula_items(candidate)}
    locations={v["id"]:t["id"] for t in original["tables"] for v in t.get("derived",[])}
    restored={}
    for item in formula_items(original):
        if item["id"] in valid_new:continue
        restored[item["id"]]=copy.deepcopy(item)
        tid=locations.get(item["id"])
        if tid:
            table=by_id(candidate["tables"]).get(tid)
            if table is None:continue
            table.setdefault("derived",[]).append(copy.deepcopy(item))
        else:candidate["results"].append(copy.deepcopy(item))
    for problem in candidate.get("unresolved",[]):
        key=problem.get("definition",{}).get("id")
        if key in restored:
            problem["retained_definition"]=copy.deepcopy(restored[key])
            problem["reason"]="来源修订未通过共同执行契约；保留原可执行定义。"+problem["reason"]
    if restored:
        from .worksheet_compile import usable_page
        candidate=usable_page(candidate)
    corroborate(original, candidate, part, catalog, set(shown_images), pending)
    from .worksheet_compile import usable_page
    candidate=usable_page(candidate)
    if pending:
        candidate.setdefault("unresolved", []).extend(pending)
    final_ids={v["id"] for v in formula_items(candidate)}
    for item in formula_items(candidate):
        if item["id"] in restored:
            # The replacement was invalid; comparing the fallback with itself is
            # not a proof that the rejected proposal was equivalent or scientific.
            item["relation_check"]="retained_valid_definition"
    for problem in candidate.get("unresolved",[]):
        retained=problem.get("retained_definition")
        if isinstance(retained,dict) and retained.get("id") and retained["id"] not in final_ids:
            problem["fallback_definition"]=problem.pop("retained_definition")
            problem["reason"]=problem["reason"].replace("保留原可执行定义。","原可执行定义回退后仍需核对依赖。")
    if not reading.get("targets") and part.get("equations"):
        candidate["source_check_note"] = "本页尚未核实主要测量关系，原式可在所列讲义页查看。"
    return candidate


def source_relations(service, jid, parts, pages, catalog, pictures, options, prompt, pack, saved, keep, check,draft_content=None):
    from .worksheets import decode_json
    from .measurement_plan import source_keys

    directory = Path(pack["directory"])
    jobs = []
    raws = {}
    for part, page in zip(parts, pages):
        raw = saved("page:" + part["id"]) or page
        raws[part["id"]] = raw
        jobs.append({"method": part["id"], "draft": {k: v for k, v in part.items() if k not in ("source_index", "page")},
                     "measurement_inventory": measurement_inventory(page),
                     "submitted_definition":measurement_inventory(raw)})
    keys = {k for p in parts for k in p["material_pages"]}
    material = {k: v for k, v in catalog.items() if k in keys}
    equation_pages = [key for p in parts for eq in p.get("equations", []) if isinstance(eq, dict)
                      for key in source_keys(eq.get("source")) + source_keys(eq.get("evidence_pages", []))]
    shown_images = list(dict.fromkeys(k for k in [*pictures, *equation_pages]
                       if options.get("native_vision") and k in material and material[k].get("image")))
    receipt = saved("source_relations")
    if receipt is None:
        text = "各方法的固定草稿及完整输入到计算图：\n" + canonical(jobs)
        if draft_content is not None:
            text+="\n完整固定草稿（保留阶段与要点标识）：\n"+canonical(draft_source_labels(service,draft_content,catalog))
        text+="\n课程原件：\n"+evidence.texts(material)
        images = evidence.image_blocks(service.app, material, shown_images) if shown_images else []
        user = [{"type":"text", "text": text}, *images] if images else text
        tool = {"type":"function", "function":{"name":"record_source_relations", "description":"逐个核实草稿的目标与测量过程，补齐实际读数和计算定义。",
                "parameters":json.loads((directory / "relations_contract.json").read_text(encoding="utf8"))}}
        check()
        try:
            answer, _ = service.agent.client.complete(
                [{"role":"system", "content":(directory / "relations.md").read_text(encoding="utf8")},
                 {"role":"user", "content":user}], tools=[tool], options={**options, "native_search":False,"_stream":False,"_purpose":"worksheet_source_relations"})
            calls = [v["function"] for v in answer.get("tool_calls", []) if v.get("function", {}).get("name") == "record_source_relations"]
            receipt = decode_json(calls[0]["arguments"] if calls else answer.get("content", ""))
            if not isinstance(receipt.get("pages"), list):
                raise ValueError("未交回按方法排列的原式")
            keep("source_relations", receipt)
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as err:
            if isinstance(err, InterruptedError):
                raise
            for page in pages:
                page["source_check_note"] = "本次未读到独立的原式映射，可点讲义原页核对主要量。"
            service.agent.runtime.event(jid, "worksheet_source_check_unavailable", {"reason":service.app.settings.redact(str(err))})
            return pages
    if draft_content is not None:
        draft_content=service.review_measurement_draft(jid,draft_content,receipt,catalog)
        parts=draft_content["measurement_methods"]
    by_method = {r.get("method"): r for r in receipt["pages"] if isinstance(r, dict)}
    for i, part in enumerate(parts):
        method = part["id"]
        if method not in by_method:
            pages[i]["source_check_note"] = "本次来源审读未交回此方法。"
            continue
        try:
            pages[i] = reconcile_page(raws[method], part, by_method[method], catalog, shown_images)
        except (ValueError, TypeError, KeyError) as err:
            pages[i]["source_check_note"] = "这一页的原式关系还需核对。"
            service.agent.runtime.event(jid, "worksheet_source_check_unavailable", {"page":method, "reason":str(err)})
    return pages
