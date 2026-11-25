"""Traceable, global transcription workbooks; no numerical analysis or filtering.

The host reads immutable source bytes. A bounded format generator receives only
the resulting content and frozen source anchors. External XLSX copies can be
removed without changing these versioned records.
"""
from __future__ import annotations

from collections import Counter
import copy
from decimal import Decimal
import json
import math
from pathlib import Path
import re

from .storage import canonical, digest, now
from .tabular import decode_text


STATUSES = {"recorded", "pending", "inferred", "confirmed"}
EDITABLE = {"quantity", "unit", "section", "status", "note"}
UNKNOWN = ("待确认", "未知", "未记录", "不知道", "不清楚", "没有记录", "没记录", "不记得", "无法确认", "无法确定", "不确定")
NUMBER = re.compile(r"(?<![\d.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![\d.])")


def original_lines(text):
    """Retain every actual source line, including blank and final content lines."""
    return [{"number": i + 1, "text": line} for i, line in enumerate(text.splitlines())]


def section_cues(lines):
    """Literal layout hints, never an automatic experiment classification."""
    return [dict(line) for line in lines if re.fullmatch(r'[^\W\d_]+(?:[ \t]+[^\W\d_]+)*',line['text'].strip(),re.UNICODE)
            or re.match(r'^#{1,6}\s+\S',line['text'].strip())]


def preceding_cue(number,cues):
    return next((cue for cue in reversed(cues) if cue['number']<=number),None)


def classification_cues(content):
    cues=section_cues(content['original_lines']);result=[]
    for i,cue in enumerate(cues):
        end=cues[i+1]['number'] if i+1<len(cues) else len(content['original_lines'])+1
        assignments=[{'kind':kind,'index':index,'id':item.get('id'),'source_line':item['source_line'],'section':item.get('section','')}
                     for kind,items in (('record',content['records']),('observation',content['observations']))
                     for index,item in enumerate(items) if cue['number']<=item['source_line']<end]
        groups=list(dict.fromkeys(item['section'] for item in assignments))
        result.append({**cue,'assigned_sections':groups,'assignments':assignments,'check_context':bool(groups) and not all(cue['text'].strip() in group for group in groups)})
    return result


def _missing(value):
    return not str(value or "").strip() or any(token in str(value) for token in UNKNOWN)


def _unit_explicit(unit, line):
    if _missing(unit):
        return False
    groups = [
        ("degC", "℃", "°C", "C", "摄氏度"), ("mm", "毫米"), ("cm", "厘米"),
        ("m", "米"), ("g", "克"), ("kg", "千克", "公斤"), ("V", "伏", "伏特"),
        ("A", "安", "安培"), ("Ω", "ohm", "欧姆"), ("mL", "ml", "毫升"),
        ("L", "升"), ("s", "秒"), ("K", "开尔文"), ("1", "无量纲"),
    ]
    variants = next((group for group in groups if unit.casefold() in [v.casefold() for v in group]), (unit,))
    for variant in variants:
        if re.search(r"[A-Za-z0-9]", variant):
            pattern = r"(?<![A-Za-z])" + re.escape(variant) + r"(?![A-Za-z])"
        elif variant in ("米", "克", "升"):
            pattern = r"(?<![毫厘千微纳])" + re.escape(variant)
        else:
            pattern = re.escape(variant)
        if re.search(pattern, line, re.I):
            return True
    # Bare “度” is retained as written; Celsius is not asserted until the
    # actual source says C/℃/摄氏 or the user confirms it.
    return False


def _quantity_explicit(quantity, line):
    if _missing(quantity):
        return False
    normal = lambda s: re.sub(r"[\s的]", "", s)
    if normal(quantity) in normal(line):
        return True
    concepts = [
        (("质量",), ("质量",)), (("厚度",), ("厚度",)), (("直径",), ("直径",)),
        (("温度", "室温", "水温"), ("温度", "室温", "度加热")),
        (("电压",), ("电压",)), (("电阻", "阻值"), ("电阻", "欧姆", "Ω")),
        (("体积", "水量", "水的量"), ("体积", "ml", "mL", "毫升", "水V")),
    ]
    return any(any(v in quantity for v in labels) and any(v in line for v in evidence) for labels, evidence in concepts)


def _direction_matches(record):
    quantity = record.get("quantity", "")
    expected = "上" if any(v in quantity for v in ("上盘", "上方", "上铜盘", "热端")) else "下" if any(v in quantity for v in ("下盘", "下方", "下铜盘", "冷端")) else None
    if expected is None:
        return True
    quote = record["source_quote"]
    markers = [(m.start(), "上" if m.group().startswith(("上", "热")) else "下") for m in re.finditer(r"上方|下方|上盘|下盘|上铜盘|下铜盘|热端|冷端", quote)]
    for value in record["values"]:
        positions = [m.start() for m in NUMBER.finditer(quote) if m.group() == value]
        if not any(next((direction for position, direction in reversed(markers) if position < start), None) == expected for start in positions):
            return False
    return True


def validate_workbook(content, snapshot=None):
    """Pure format rule. With Snapshot, also reject truncated/altered originals."""
    lines = content.get("original_lines", [])
    if not isinstance(lines, list) or not lines or any(not isinstance(v, dict) or v.get("number") != i + 1 or not isinstance(v.get("text"), str) for i, v in enumerate(lines)):
        raise ValueError("原始记录必须包含连续编号的完整原文行")
    if not isinstance(content.get("source_id"), str) or not re.fullmatch(r"[a-f0-9]{64}", content.get("source_sha", "")):
        raise ValueError("缺少原件身份或完整 SHA-256")
    by_line = {item["number"]: item["text"] for item in lines}
    sources = content.get("sources", [])
    if snapshot is not None:
        anchors = [snapshot.anchor(identity) for identity in sources]
        same = [a for a in anchors if a["source_id"] == content["source_id"] and a["source_sha"] == content["source_sha"]]
        if not any(original_lines(a.get("quote", "")) == lines for a in same):
            raise ValueError("完整原文与冻结来源不一致；不能删末尾、改行或伪写来源摘要")
    records = content.get("records", [])
    record_ids = [item.get("id") for item in records]
    if any(not isinstance(v, str) or not v.strip() for v in record_ids) or len(record_ids) != len(set(record_ids)):
        raise ValueError("测量记录 ID 为空或重复")
    confirmed_by_history = set()
    for entry in content.get("history", []):
        if entry.get("confirmation_verified") and entry.get("question_id"):
            for update in entry.get("updates", []):
                if update.get("after", {}).get("status") == "confirmed":
                    confirmed_by_history.add(update.get("record_id"))
    for record in records:
        line = by_line.get(record.get("source_line"))
        quote = record.get("source_quote", "")
        if line is None or not isinstance(quote, str) or not quote.strip() or quote not in line:
            raise ValueError("记录的来源句不在对应真实原文行：" + str(record.get("id")))
        values = record.get("values", [])
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not NUMBER.fullmatch(v) for v in values):
            raise ValueError("读数必须是保留原精度的数字字符串，不能改为浮点数或填造")
        available = Counter(NUMBER.findall(quote))
        if Counter(values) - available:
            raise ValueError("读数或其原始精度不在真实来源句中：" + str(record["id"]))
        status = record.get("status")
        if status not in STATUSES:
            raise ValueError("未知测量记录状态")
        if status=='inferred':
            evidence=record.get('evidence_lines',[])
            if not record.get('note','').strip() or not evidence or any(type(n) is not int or n not in by_line for n in evidence):
                raise ValueError('上下文推断须有明确备注及真实原文依据行，不能冒充用户确认：'+record['id'])
        if status == "recorded" and (not _quantity_explicit(record.get("quantity", ""), quote) or not _unit_explicit(record.get("unit", ""), quote)):
            raise ValueError("量名/单位没有在本句明确记录，须保留待确认：" + record["id"])
        if status == "recorded" and not _direction_matches(record):
            raise ValueError("读数与原句的上/下盘归属不符，不能互换或猜测：" + record["id"])
        if status == "confirmed" and (_missing(record.get("quantity")) or _missing(record.get("unit")) or record["id"] not in confirmed_by_history):
            raise ValueError("已确认读数必须有真实答复历史及明确量名和单位")
    for observation in content.get("observations", []):
        line = by_line.get(observation.get("source_line"))
        if line is None or not observation.get("text") or observation["text"] not in line:
            raise ValueError("原文观察必须能回到其原始行，不能替换为模型推断")
    issues = content.get("issues", [])
    ids = [issue.get("id") for issue in issues]
    if any(not isinstance(v, str) or not v.strip() for v in ids) or len(ids) != len(set(ids)):
        raise ValueError("待确认问题 ID 为空或重复")
    for issue in issues:
        if not set(issue.get("record_ids", [])) <= set(record_ids):
            raise ValueError("待确认问题引用了不存在的记录")
        if issue.get("status") not in ("open", "answered", "unknown", "partial"):
            raise ValueError("待确认问题状态无效")
    return content


def _sheet_name(value, used):
    from .excel_names import sheet_name
    return sheet_name(value, used)


def _set_cell(cell, value):
    if isinstance(value, str):
        # Explicit string cells retain 5.40 and prevent source text being treated
        # as an Excel formula. This is a transcription, not an analysis run.
        cell.value = value
        cell.data_type = "s"
        cell.number_format = "@"
    else:
        cell.value = value


def _numeric_value(value):
    parsed = Decimal(value)
    number = float(parsed)
    if len(parsed.as_tuple().digits) > 15:
        return None, "超过 Excel 的 15 位有效数字能力；此格留空，完整原精度见原始读数字符串列。"
    if not math.isfinite(number) or (parsed and number == 0):
        return None, "超出 Excel 数值范围；此格留空，完整读数见原始读数字符串列。"
    return number, "只把原数字串解析为数值，未作单位换算、筛除或物理计算。"


def build_workbook(content, out, snapshot, include_context=False):
    """Bounded generator: finite content + Snapshot in; XLSX/JSON files out."""
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.table import Table, TableStyleInfo

    validate_workbook(content, snapshot)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    book.remove(book.active)
    book.properties.title = content["title"]
    book.properties.creator = "物理实验助手"
    book.properties.description = "原文整理，不跨实验计算；原件 SHA-256 " + content["source_sha"]
    used = {"原始记录".casefold(), "待确认".casefold(),"原文观察".casefold(),"实验背景".casefold()}
    sections = list(dict.fromkeys([r.get("section") or "未分组记录" for r in content["records"]] + [v.get("section") or "未分组记录" for v in content["observations"]]))
    cues=section_cues(content['original_lines']) if include_context else []
    section_sheets = {section: book.create_sheet(_sheet_name(section, used)) for section in sections}
    raw = book.create_sheet("原始记录")
    raw.append(["行号", "原文（全文保留）"])
    line_rows = {}
    for item in content["original_lines"]:
        # Excel itself limits a string cell to 32767 characters. Split an
        # unusually long physical line over rows instead of silently truncating.
        chunks = [item["text"][i:i+32767] for i in range(0, len(item["text"]), 32767)] or [""]
        for part, chunk in enumerate(chunks):
            row = raw.max_row + 1
            line_rows.setdefault(item["number"], row)
            _set_cell(raw.cell(row, 1), item["number"] if not part else str(item["number"]) + "（续）")
            _set_cell(raw.cell(row, 2), chunk)
    questions = book.create_sheet("待确认")
    questions.append(["问题", "对应记录", "待确认内容", "涉及字段", "状态", "答复", "答复历史"])
    record_by_id = {r["id"]: r for r in content["records"]}
    for i, issue in enumerate(content["issues"], 1):
        relevant = [record_by_id[r] for r in issue["record_ids"]]
        labels = "；".join((r.get("section") or "未分组") + " / " + (r.get("quantity") or "量名待确认") + " / 原文第" + str(r["source_line"]) + "行" for r in relevant)
        history = "\n".join(h["updated"] + " " + (("来源问题："+h["source_question"]+"\n") if h.get("source_question") and h["source_question"]!=issue["question"] else "") + h["answer"] for h in content.get("history", []) if h["issue_id"] == issue["id"])
        values = [f"问题{i}", labels, issue["question"], issue["field"], {"open": "待答复", "answered": "已答复", "unknown": "未记录 / 仍未知", "partial":"部分已明确 / 其余仍未知"}[issue["status"]], issue.get("answer", ""), history]
        # Split long question/answer histories rather than losing the tail.
        count = max(1, max((len(str(v)) + 32766) // 32767 for v in values))
        for part in range(count):
            row = questions.max_row + 1
            for col, value in enumerate(values, 1):
                text = str(value)
                _set_cell(questions.cell(row, col), text[part*32767:(part+1)*32767])
            questions.cell(row, 1).comment = Comment("内部问题引用：" + issue["id"], "物理实验助手")
    if content.get('material_background'):
        background=book.create_sheet('实验背景');background.append(['核对问题','现场答复及差异','依据材料','适用范围'])
        for item in content['material_background']:
            references='；'.join(dict.fromkeys(r['name']+' p'+str(r['page']) for r in item['references']))
            background.append([item['question'],item['answer'],references,'本批原始记录；按实际答复理解适用部分与差异'])
    for section, sheet in section_sheets.items():
        sheet.append(["序号", "物理量", "原始读数字符串", "数值（原单位）", "单位", "状态", "原文行", "备注"]+(['原文分段线索'] if include_context else []))
        number = 0
        for record in content["records"]:
            if (record.get("section") or "未分组记录") != section:
                continue
            number += 1
            for value in record["values"]:
                numeric, numeric_note = _numeric_value(value)
                values = [number, record.get("quantity") or "待确认", value, numeric, record.get("unit") or "待确认", {"recorded": "原文明确记录", "pending": "待确认", "inferred":"依据原文上下文推断", "confirmed": "已确认"}[record["status"]], f"第{record['source_line']}行", record.get("note", "")]
                if include_context:
                    cue=preceding_cue(record['source_line'],cues)
                    values.append((cue['text']+'（原文第'+str(cue['number'])+'行）') if cue else '')
                row = sheet.max_row + 1
                for col, item in enumerate(values, 1):
                    if len(str(item)) > 32767:
                        raise ValueError("本行备注超过 Excel 单元格能力；完整内容已保留在内部记录，请拆分备注后生成")
                    _set_cell(sheet.cell(row, col), item)
                cell = sheet.cell(row, 7)
                cell.hyperlink = "#'原始记录'!A" + str(line_rows[record["source_line"]])
                cell.style = "Hyperlink"
                sheet.cell(row, 3).comment = Comment("原始读数字符串保留末尾零及原文精度；右侧数值列便于使用 Excel。", "物理实验助手")
                sheet.cell(row, 4).comment = Comment(numeric_note, "物理实验助手")
                if numeric is not None and "e" not in value.casefold() and "." in value:
                    places = len(value.rsplit(".", 1)[1])
                    sheet.cell(row, 4).number_format = "0." + "0" * places if places else "0"
                if record["status"] == "pending":
                    for cell in sheet[row]:
                        cell.fill = PatternFill("solid", fgColor="FFF2CC")
    observations_sheet=book.create_sheet("原文观察")
    observations_sheet.append(["实验分组","原文观察（不自动剔除数据）","原文行"])
    for observation in content["observations"]:
        text=observation["text"]
        for start in range(0,len(text),32767):
            row=observations_sheet.max_row+1
            for column,item in enumerate([observation["section"],text[start:start+32767],observation["source_line"]],1):_set_cell(observations_sheet.cell(row,column),item)
    for sheet_index, sheet in enumerate(book, 1):
        sheet.freeze_panes = "A2"
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
            cell.font = Font(name="微软雅黑", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="244A61")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.row_dimensions[1].height = 29
        widths = [10, 105] if sheet is raw else [32,105,12] if sheet is observations_sheet else [12, 32, 52, 16, 22, 34, 58] if sheet is questions else [9, 27, 22, 20, 15, 22, 14, 52]+([32] if include_context else [])
        for i, width in enumerate(widths, 1):
            sheet.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width
        for row in sheet.iter_rows(min_row=2):
            import math,unicodedata
            lines=1
            for cell in row:
                cell.font = Font(name="微软雅黑", size=11, color="172631")
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                width=sheet.column_dimensions[cell.column_letter].width or 12
                visible=sum(max(1,math.ceil(sum(2 if unicodedata.east_asian_width(ch) in ('W','F') else 1 for ch in line)/max(1,width-2))) for line in str(cell.value or '').splitlines() or [''])
                lines=max(lines,visible)
            sheet.row_dimensions[row[0].row].height=min(409,lines*16+8)
        sheet.auto_filter.ref = sheet.dimensions
        sheet.print_title_rows = "1:1"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    book.active = 0
    book.save(out / "data.xlsx")
    book.close()
    from .excel_names import validate_exported_names
    validate_exported_names(out / "data.xlsx")
    (out / "normalized.json").write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class DataWorkbooks:
    def __init__(self, store, gateway, materials):
        self.store, self.gateway, self.materials = store, gateway, materials
        self.answer_lookup = None

    def _source(self, source_id):
        source = self.store.one("SELECT * FROM sources WHERE id=?", (source_id,))
        if not source:
            raise ValueError("请先从资料库导入原始文字记录")
        if source["kind"] not in ("txt", "md"):
            raise ValueError("当前整理入口读取原始文字记录；PDF/Word 先由资料阅读工具定位原文，不冒充相同字节")
        data = self.store.asset_path(source["original"]).read_bytes()
        if digest(data) != source["sha"]:
            raise ValueError("原件摘要不符；未整理或改写原件")
        text = decode_text(data)
        if not text.strip():
            raise ValueError("原始记录为空")
        return source, text

    def inspect_source(self,source_id):
        source,text=self._source(source_id);lines=original_lines(text)
        return {'source_id':source_id,'name':source['name'],'sha256':source['sha'],'text':text,'original_lines':lines,
                'section_cues':section_cues(lines),'existing_workbooks':self.existing(source_id),
                'scope':'完整原文及独立文本行线索；线索可能是标题，须结合上下文核对，不自动确认实验归属或测量条件。已有整理表请读完整固定版本与真实答复历史再接续，未记录仍是未记录。'}

    def existing(self,source_id):
        return self.store.all("SELECT o.id,o.revision,o.title,r.created FROM objects o JOIN revisions r ON r.object_id=o.id AND r.revision=o.revision WHERE o.kind='DataWorkbook' AND o.experiment_id IS NULL AND json_extract(r.content,'$.source_id')=? ORDER BY r.created DESC,o.id",(source_id,))

    def submit(self, source_id, content, object_id=None, base_revision=0):
        source, text = self._source(source_id)
        value = copy.deepcopy(content)
        value.update(source_id=source_id, source_sha=source["sha"], original_lines=original_lines(text))
        value.setdefault("records", [])
        value.setdefault("observations", [])
        value.setdefault("issues", [])
        for record in value["records"]:
            for key in ("section", "quantity", "unit", "note"):
                record.setdefault(key, "")
            record.setdefault("status", "pending")
        for issue in value["issues"]:
            issue.setdefault("record_ids", [])
            issue.setdefault("status", "open")
            issue.setdefault("answer", "")
            issue.setdefault("confirmation_question_id", "")
        old = self.read(object_id) if object_id else None
        if old:
            if old["revision"] != base_revision:
                raise ValueError("整理记录基线已变化，请重读后保存")
            if old["content"]["source_id"] != source_id:
                raise ValueError("不能把整理成果悄悄换成另一份原始记录")
            value["history"] = copy.deepcopy(old["content"].get("history", []))
            previous = {r["id"]: r for r in old["content"]["records"]}
            incoming = {r["id"]: r for r in value["records"]}
            if not set(previous) <= set(incoming):
                raise ValueError("旧测量记录须保留；用注释说明重复或异常，不删除原始观察")
            for identity, record in previous.items():
                if record["status"] == "confirmed" and incoming[identity] != record:
                    raise ValueError("已确认解释只能通过对应问题的真实答复修改")
            for record in value["records"]:
                if record["status"] == "confirmed" and previous.get(record["id"], {}).get("status") != "confirmed":
                    raise ValueError("待确认读数不能由模型自行确认；请先提问并保存真实答复")
            old_issues = {q["id"]: q for q in old["content"]["issues"]}
            new_issues = {q["id"]: q for q in value["issues"]}
            for identity, issue in new_issues.items():
                if identity not in old_issues and (issue["status"] != "open" or issue["answer"] or issue.get("confirmation_question_id")):
                    raise ValueError("新增问题不能伪称已有用户答复")
            for identity, issue in old_issues.items():
                if identity not in new_issues:
                    value["issues"].append(copy.deepcopy(issue))
                elif any(new_issues[identity].get(k) != issue.get(k) for k in ("question", "record_ids", "field")):
                    raise ValueError("同一问题身份须保留原问题和涉及记录；新问题请另建问题 ID")
                elif any(new_issues[identity].get(k) != issue.get(k) for k in ("status", "answer", "confirmation_question_id")):
                    raise ValueError("答复状态与历史只能通过对应提问更新")
        else:
            value["history"] = []
            if any(r["status"] == "confirmed" for r in value["records"]) or any(q["status"] != "open" or q["answer"] for q in value["issues"]):
                raise ValueError("首次整理不得伪称用户已经确认")
        self._snapshot_background(value,old)
        validate_workbook(value)
        anchors = [self.materials.make_anchor(source_id, 1, text)["id"]]
        for item in value["records"]:
            anchors.append(self.materials.make_anchor(source_id, 1, item["source_quote"])["id"])
        for item in value["observations"]:
            anchors.append(self.materials.make_anchor(source_id, 1, item["text"])["id"])
        value["sources"] = list(dict.fromkeys(anchors))
        for item in value.get('material_background',[]):value['sources']=list(dict.fromkeys(value['sources']+item['basis_anchors']))
        validate_workbook(value)
        result=self.gateway.apply("DataWorkbook", value, experiment_id=None, object_id=object_id, base_revision=base_revision)
        self._delivery_info(result)
        result['source_structure_check']={'cues':classification_cues(value),'scope':'核对原文分段线索与所给分组；不同名称可能是合理别名或非标题，不机械追求提示清空，不把原文标题与个人不记得混为一谈。'}
        return result

    def read(self, identity, revision=None):
        obj = self.gateway.read(identity, revision)
        if obj["kind"] != "DataWorkbook" or obj["experiment_id"] is not None:
            raise ValueError("此对象不是全局原始记录整理表")
        from .material_context import background_records
        obj['material_background']=background_records(self.store,[obj['content']['source_id']],include_other_experiments=True)
        obj['material_background_scope']='当前同源背景问答索引，单独保留；不表示固定旧修订已包含后来的答复，也不自动适用于其他实验。'
        return self._delivery_info(obj)

    def _snapshot_background(self,value,previous=None):
        from .format_program import Program
        from .material_context import background_records
        pack=self.gateway.registry.pinned(previous['pack_id'],previous['pack_version'],previous['pack_digest']) if previous else self.gateway.registry.owner('DataWorkbook')
        if 'material_background' in Program(pack).schemas['DataWorkbook']['properties']:
            value['material_background']=[row for row in background_records(self.store,[value['source_id']],include_other_experiments=True) if row['status']=='answered' and row['answer']]
            if 'sources' in value:
                value['sources']=list(dict.fromkeys(value['sources']+[a for row in value['material_background'] for a in row['basis_anchors']]))

    def _delivery_info(self, obj):
        import openpyxl
        obj.setdefault('material_background',obj['content'].get('material_background',[]))
        asset=next((a for a in obj['assets'] if a['name']=='data.xlsx'),None)
        if asset:
            book=openpyxl.load_workbook(self.store.asset_path(asset['path']),read_only=True,data_only=False)
            try:
                obj['workbook_sheets']=book.sheetnames
                obj['answer_history_locations']=[{'sheet':s.title,'column':openpyxl.utils.get_column_letter(i)} for s in book for i,c in enumerate(next(s.iter_rows(),[]),1) if c.value=='答复历史']
            finally:book.close()
        return obj

    def search(self, query=""):
        query = str(query).strip().casefold()
        found = []
        for obj in self.gateway.query(kind="DataWorkbook"):
            if obj["experiment_id"] is not None:
                continue
            value = obj["content"]
            records = [r for r in value["records"] if not query or query in (r.get("section", "") + " " + r.get("quantity", "") + " " + r.get("note", "") + " " + r["source_quote"]).casefold()]
            if query and not records and query not in value["title"].casefold():
                continue
            found.append({"id": obj["id"], "revision": obj["revision"], "title": value["title"], "source_id": value["source_id"], "sections": list(dict.fromkeys(r.get("section", "") for r in value["records"])), "records": records if records else value["records"], "issues": value["issues"]})
        return found

    def _confirmed_answer(self, object_id, issue_id, answer, question_id, source_item_id=None):
        if not question_id or not callable(self.answer_lookup):
            return False
        receipt = self.answer_lookup(question_id)
        if not receipt or receipt.get("status") != "answered":
            raise ValueError("这条提问还没有实际用户答复")
        if source_item_id:
            item=next((q for q in receipt.get('items',[]) if q['id']==source_item_id),None)
            if not item:raise ValueError('真实提问中没有这个答复项')
            obj=self.store.object(object_id)
            same_object=item.get('target_object_id')==object_id
            background_scope=not item.get('target_object_id') and obj['content']['source_id'] in (item.get('material_basis') or {}).get('record_source_ids',[])
            if not (same_object or background_scope):raise ValueError('答复项不属于本规范表或该原始记录的背景范围')
            actual=item.get('answer')
            if actual is None:actual=receipt.get('unassigned_answer')
            if actual is None:raise ValueError('该题尚无实际答复，不能使用另一题已经分配的答案')
        else:
            targets = receipt.get("targets", []) or [{"target_object_id": receipt.get("target_object_id"), "issue_id": receipt.get("issue_id")}]
            if not any(t.get("target_object_id") == object_id and t.get("issue_id") == issue_id for t in targets):
                raise ValueError("真实答复未关联这份整理表的这个问题")
            actual = receipt.get("answer", "")
            if len(targets)>1 and "answers" not in receipt:
                raise ValueError("批量提问必须保存逐题答复，不能把整段中任一题的答案挪给另一题")
            if "answers" in receipt:
                specific=next((a for a in receipt["answers"] if a.get("target_object_id")==object_id and a.get("issue_id")==issue_id),None)
                if not specific:raise ValueError("多题自由答复尚未明确对应这一题，请按自然语言说明本题答案，或在提问窗口逐题填写；不能挪用另一题的答复")
                actual=specific["answer"]
        if not isinstance(actual, str):
            actual = json.dumps(actual, ensure_ascii=False)
        clauses = [part.strip() for part in re.split(r"[。；;，,\n]", actual) if part.strip()]
        for clause in list(clauses):
            labelled = re.match(r"^(?:用户|实际)?(?:答复|回答|answer)\s*[:：]\s*(.+)$", clause, re.I)
            if labelled:
                clauses.append(labelled.group(1).strip())
        selected=[part.strip() for part in re.split(r"[。；;，,\n]", answer) if part.strip()]
        if answer.strip() != actual.strip() and (not selected or any(part not in clauses for part in selected)):
            raise ValueError("答复须保留真实已提交原文或完整子句，不能由模型改写确认")
        return True

    def _prepare_resolution(self, obj, current, issue_id, answer, updates, question_id=None, source_item_id=None):
        object_id=obj["id"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("请先提交真实答复；不清楚可直接说未记录")
        value = copy.deepcopy(current)
        issue = next((q for q in value["issues"] if q["id"] == issue_id), None)
        if not issue:
            raise ValueError("原问题不存在，不能把答复接到另一个问题")
        answer = answer.strip()
        verified = self._confirmed_answer(object_id, issue_id, answer, question_id, source_item_id)
        # A structured receipt may repeat a question/options containing the word
        # “未记录”. Classify the actual answer portion, not that question text.
        answer_part = re.split(r"(?:用户答复|实际答复|答复|回答|answer)[\"']?\s*[:：]\s*[\"']?", answer, flags=re.I)[-1]
        unknown = any(word in answer_part for word in UNKNOWN)
        if not verified and not unknown:
            raise ValueError("确认需要提问入口已提交的真实答复，不能由模型自行填写")
        if not isinstance(updates, list):
            raise ValueError("答复更新须为所涉及记录的字段列表")
        records = {r["id"]: r for r in value["records"]}
        changes = []
        known_updates=False
        used = set()
        for update in updates:
            if not isinstance(update, dict) or set(update) - {"record_id", "answer_quote", *EDITABLE}:
                raise ValueError("答复只能改量名、单位、分组、状态或备注；不能改读数及原文")
            identity = update.get("record_id")
            if identity not in issue["record_ids"] or identity not in records or identity in used:
                raise ValueError("答复不能改动本问题之外的记录，也不能重复更新同一行")
            used.add(identity)
            support=update.get('answer_quote',answer)
            if not isinstance(support,str) or not support.strip():raise ValueError('更新依据须为本题真实答复的非空原文子句')
            support_verified=self._confirmed_answer(object_id,issue_id,support,question_id,source_item_id)
            support_part=re.split(r"(?:用户答复|实际答复|答复|回答|answer)[\"']?\s*[:：]\s*[\"']?",support,flags=re.I)[-1]
            support_unknown=any(word in support_part for word in UNKNOWN)
            if not support_verified and not support_unknown:
                raise ValueError('更新须引用这个问题的真实已提交答复，不能自行构造已知部分')
            fields = {k: v for k, v in update.items() if k not in ("record_id","answer_quote")}
            if not fields or any(not isinstance(v, str) for v in fields.values()):
                raise ValueError("答复更新字段必须是明确文字")
            if support_unknown and (set(fields) - {"status", "note"} or fields.get("status", "pending") != "pending"):
                raise ValueError("未记录的量名/单位不能被填造确认，原读数继续待确认")
            if fields.get("status") == "confirmed" and not support_verified:
                raise ValueError("已确认状态必须对应真实用户答复")
            record = records[identity]
            before = {k: record.get(k, "") for k in fields}
            record.update(fields)
            known_updates |= not support_unknown and bool(set(fields)-{'status','note'} or fields.get('status')=='confirmed')
            changes.append({"record_id": identity, "before": before, "after": fields,
                            **({'answer_quote':support} if 'answer_quote' in update else {})})
        state='partial' if unknown and known_updates else 'unknown' if unknown else 'answered'
        if state=='partial' or any('answer_quote' in u for u in updates):
            from .format_program import Program
            pack=self.gateway.registry.pinned(obj['pack_id'],obj['pack_version'],obj['pack_digest'])
            schema=Program(pack).schemas['DataWorkbook']['properties']
            if 'partial' not in schema['issues']['items']['properties']['status']['enum']:
                raise ValueError('当前固定格式不支持部分答复，请先预览并升级数据格式；原版本未改变')
        if source_item_id:
            from .format_program import Program
            pack=self.gateway.registry.pinned(obj['pack_id'],obj['pack_version'],obj['pack_digest'])
            if 'source_item_id' not in Program(pack).schemas['DataWorkbook']['properties']['history']['items']['properties']:
                raise ValueError('当前固定格式不支持跨相关问题的答复来源，请先预览升级；旧表未改')
        issue.update(status=state, answer=answer, confirmation_question_id=question_id or "")
        value.setdefault("history", []).append({"issue_id": issue_id, "question_id": question_id or "", "answer": answer, "updated": now(), "updates": changes, "confirmation_verified": verified, **({'source_item_id':source_item_id,'source_question':next(q['question'] for q in self.answer_lookup(question_id)['items'] if q['id']==source_item_id)} if source_item_id else {})})
        validate_workbook(value)
        return value, {"issue_id": issue_id, "question_id": question_id, "source_item_id":source_item_id, "answer": answer, "verified": verified, "status": issue["status"], "record_ids": list(used)}

    def resolve_issues(self, object_id, base_revision, resolutions):
        obj=self.read(object_id)
        if obj['revision']!=base_revision:raise ValueError('整理记录基线已变化，请重读后答复')
        if not isinstance(resolutions,list) or not resolutions:raise ValueError('请提供实际已答复的问题')
        value=copy.deepcopy(obj['content']);receipts=[];errors=[];seen=set()
        for item in resolutions:
            issue_id=item.get('issue_id') if isinstance(item,dict) else None
            try:
                if not isinstance(item,dict) or set(item)-{'issue_id','question_id','source_item_id','answer','updates'}:raise ValueError('答复项含未声明字段')
                if issue_id in seen:raise ValueError('同批不能重复处理同一事项')
                seen.add(issue_id)
                prepared,receipt=self._prepare_resolution(obj,value,issue_id,item['answer'],item['updates'],item.get('question_id'),item.get('source_item_id'))
                value=prepared;receipts.append(receipt)
            except (ValueError,KeyError,TypeError) as exc:errors.append({'issue_id':issue_id,'error':str(exc)})
        if receipts:
            self._snapshot_background(value,obj)
            result=self.gateway.apply('DataWorkbook',value,experiment_id=None,object_id=object_id,base_revision=base_revision)
            self._delivery_info(result)
        else:result={'id':object_id,'kind':'DataWorkbook','revision':base_revision}
        return {**result,'resolution_receipts':receipts,'resolution_errors':errors,'committed':bool(receipts),'all_succeeded':not errors}

    def resolve_issue(self, object_id, base_revision, issue_id, answer, updates, question_id=None, source_item_id=None):
        result=self.resolve_issues(object_id,base_revision,[{'issue_id':issue_id,'answer':answer,'updates':updates,'question_id':question_id,**({'source_item_id':source_item_id} if source_item_id else {})}])
        if result['resolution_errors']:raise ValueError(result['resolution_errors'][0]['error'])
        result['confirmation_receipt']=result['resolution_receipts'][0]
        return result
