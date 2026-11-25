"""Persist source corrections in the shared draft before compiling its final pages."""
import copy
import json
from pathlib import Path

from .format_program import check_schema
from .measurement_plan import source_keys
from .storage import canonical, digest


def review(service, job, draft_content, receipt, catalog):
    from .worksheets import linked_sources, normalize_outline_kinds

    corrections = [row for row in receipt.get("pages", [])
                   if isinstance(row, dict) and isinstance(row.get("draft"), dict)]
    points = copy.deepcopy(receipt.get("draft_points") or [])
    if not corrections and not points:
        return draft_content
    content = copy.deepcopy(draft_content)
    methods = {method["id"]: method for method in content["measurement_methods"]}
    def original_pages(keys):
        resolved = []
        for key in keys:
            if key not in catalog:
                anchor = service.store.anchor(key)
                key = next((label for label, source in catalog.items()
                            if source["source"] == anchor["source_id"]
                            and source["number"] == anchor["page"]), None)
            if key is None:
                raise ValueError("草稿修正引用的原页不属于本次材料")
            resolved.append(key)
        return list(dict.fromkeys(resolved))
    contract = json.loads((Path(service.app.registry.owner("ExperimentWorksheet")["directory"])
                           / "outline_contract.json").read_text(encoding="utf8"))
    changed = []
    for row in corrections:
        method = methods.get(row["method"])
        candidate = {**copy.deepcopy(method or {}), **copy.deepcopy(row["draft"])}
        if method is None or candidate.get("id") != method["id"]:
            raise ValueError("来源核对的草稿方法与本次实验不一致")
        check_schema(candidate, contract["properties"]["experiments"]["items"], None, "核对后的方法")
        if not {v["id"] for v in method["targets"]}.issubset(v["id"] for v in candidate["targets"]):
            raise ValueError("来源核对须保留本次课程的待求量")
        keys = list(candidate["material_pages"])
        for item in candidate["targets"] + candidate["observables"] + candidate["equations"]:
            if isinstance(item, dict):
                keys.extend(source_keys(item.get("source")))
                keys.extend(source_keys(item.get("evidence_pages", [])))
        keys = original_pages(keys)
        candidate["source_index"] = dict(zip(keys, linked_sources(service.app, catalog, keys)))
        candidate["material_pages"] = keys
        for key in ("id", "title", "purpose", "targets", "observables", "equations",
                    "material_pages", "source_index"):
            method[key] = candidate[key]
        changed.append({"method": method["id"], "revision": "source_review"})

    by_point = {point["id"]: point for stage in content["stages"] for point in stage["points"]}
    point_schema = contract["properties"]["stages"]["items"]["properties"]["points"]["items"]
    for update in points:
        if not isinstance(update, dict) or update.get("id") not in by_point:
            raise ValueError("来源核对的知识点不在当前草稿内")
        update = {**copy.deepcopy(by_point[update["id"]]), **update}
        normalize_outline_kinds({"stages":[{"points":[update]}]})
        check_schema(update, point_schema, None, "核对后的知识点")
        point = by_point[update["id"]]
        for key in ("title", "knowledge", "material_plan", "interpretations"):
            point[key] = copy.deepcopy(update[key])
        point["sources"] = linked_sources(service.app, catalog, original_pages(update["sources"]))
        changed.append({"point": point["id"], "revision": "source_review"})
    if content == draft_content:
        return draft_content

    payload = json.loads(service.store.one("SELECT payload FROM jobs WHERE id=?", (job,))["payload"])
    experiment = payload["experiment_id"]
    drafts = service.agent.experiment_drafts
    binding = drafts.require(experiment, job)
    original = service.store.object(binding["id"], binding["revision"])
    if original["content"] != draft_content:
        raise ValueError("实验草稿已有新修订，请从当前修订接续来源核对")
    saved = drafts.save(experiment, job, content, binding["id"], binding["revision"])
    service.agent.runtime.event(job, "worksheet_draft_source_review", {
        "before": binding, "after": {"id": saved["id"], "revision": saved["revision"]},
        "changes": changed, "receipt_sha256": digest(canonical(receipt))})
    return saved["content"]
