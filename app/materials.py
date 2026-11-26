from __future__ import annotations

import base64
import csv
import io
import json
from pathlib import Path
import shutil
import threading
from zipfile import ZipFile
from .tabular import decode_text,read_tables,select_table

from .storage import Store, canonical, digest, now, uid


class ImportCancelled(Exception):
    pass


class Materials:
    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.Lock()

    def queue(self, name: str, data: bytes, *, metadata=None, experiment_id=None):
        if experiment_id:self.store.experiment(experiment_id)
        name = Path(name.replace("\\", "/")).name
        if Path(name).suffix.lower() not in (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".md", ".csv", ".tsv", ".xlsx", ".dat", ".zip"):
            raise ValueError("支持 PDF、Word DOCX、图片、文本、CSV/TSV、XLSX、已验证 DAT 与数据 ZIP；PPT 请先导出 PDF")
        if not data:
            raise ValueError("不能导入空文件")
        job_id = uid("import")
        upload = self.store.staging / (job_id + ".upload")
        upload.write_bytes(data)
        payload = {"name": name, "upload": str(upload), "metadata": metadata or {"origin": "用户导入", "license": "仅本地使用，未确认可分发"}}
        if experiment_id:payload["experiment_id"]=experiment_id
        self.store.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)", (job_id, "import", "queued", 0, canonical(payload), None, None, now(), now()))
        threading.Thread(target=self.process, args=(job_id,), daemon=True).start()
        return {"id": job_id}

    def retry(self, job_id):
        job = self.store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job or job["kind"] != "import" or job["status"] not in ("failed", "cancelled", "interrupted"):
            raise ValueError("仅失败、取消或中断的导入可以重试")
        payload = json.loads(job["payload"])
        if not Path(payload["upload"]).is_file():
            raise ValueError("暂存原件已回收，请重新选择文件")
        self.store.execute("UPDATE jobs SET status='queued',cancel=0,error=NULL,progress=0,updated=? WHERE id=?", (now(), job_id))
        threading.Thread(target=self.process, args=(job_id,), daemon=True).start()
        return {"id": job_id}

    def progress(self, job_id, value):
        row = self.store.one("SELECT cancel FROM jobs WHERE id=?", (job_id,))
        if row["cancel"]:
            raise ImportCancelled()
        self.store.execute("UPDATE jobs SET progress=?,updated=? WHERE id=?", (value, now(), job_id))

    def process(self, job_id):
        job = self.store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        payload = json.loads(job["payload"])
        self.store.execute("UPDATE jobs SET status='running', updated=? WHERE id=?", (now(), job_id))
        staged = self.store.staging / (job_id + "-pages")
        destination = None
        try:
            data = Path(payload["upload"]).read_bytes()
            sha = digest(data)
            with self._lock:
                self.progress(job_id, 2)
                existing = self.store.one("SELECT id,name FROM sources WHERE sha=?", (sha,))
                if existing:
                    result = {"source_id": existing["id"], "duplicate": True, "message": "原字节相同；沿用原来源，不重复建知识或关系"}
                else:
                    source_id = uid("src")
                    staged.mkdir()
                    kind = Path(payload["name"]).suffix.lower().lstrip(".")
                    pages = self._extract(data, kind, staged, job_id)
                    self.progress(job_id, 93)
                    destination = self.store.originals / source_id
                    shutil.move(str(staged), str(destination))
                    original = destination / ("original." + kind)
                    original.write_bytes(data)
                    # Files stay byte-identical; the UI has no original-edit endpoint.
                    original.chmod(0o444)
                    with self.store.transaction() as conn:
                        if conn.execute("SELECT cancel FROM jobs WHERE id=?", (job_id,)).fetchone()[0]:
                            raise ImportCancelled()
                        conn.execute("INSERT INTO sources(id,sha,name,kind,original,created,metadata) VALUES(?,?,?,?,?,?,?)", (source_id, sha, payload["name"], kind,
                                     str(original.relative_to(self.store.authority)), now(), canonical(payload["metadata"])))
                        for page in pages:
                            image = str((destination / page["image"]).relative_to(self.store.authority)) if page["image"] else None
                            conn.execute("INSERT INTO pages VALUES(?,?,?,?,?,?)", (source_id, page["number"], page["label"], page["text"], image, canonical(page["warnings"])))
                    result = {"source_id": source_id, "duplicate": False, "pages": len(pages), "warnings": sum(len(p["warnings"]) for p in pages)}
                eid = payload.get("experiment_id")
                linked = [result["source_id"]]
                if payload["name"].lower().endswith(".zip") and (not existing or eid):
                    children = []
                    with ZipFile(io.BytesIO(data)) as archive:
                        for entry in archive.infolist():
                            if entry.is_dir() or Path(entry.filename).suffix.lower() not in (".csv",".tsv",".xlsx",".dat",".txt"):
                                continue
                            member = archive.read(entry)
                            known = self.store.one("SELECT id FROM sources WHERE sha=?", (digest(member),))
                            if known:
                                linked.append(known["id"])
                            else:
                                meta = {**payload["metadata"], "container_sha":sha, "archive_member":entry.filename}
                                child = self.queue(entry.filename, member, experiment_id=eid, metadata=meta)
                                children.append(child["id"])
                    result["child_jobs"] = children
                with self.store.transaction() as conn:
                    if eid:
                        for sid in linked:
                            conn.execute("""UPDATE experiments SET source_ids=json_insert(source_ids,'$[#]',?),updated=?
                                WHERE id=? AND NOT EXISTS (SELECT 1 FROM json_each(experiments.source_ids) WHERE value=?)""",
                                (sid, now(), eid, sid))
                    conn.execute("UPDATE jobs SET status='succeeded',progress=100,result=?,updated=? WHERE id=?", (canonical(result), now(), job_id))
        except Exception as exc:
            status = "cancelled" if isinstance(exc, ImportCancelled) else "failed"
            self.store.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?", (status, "用户取消" if status == "cancelled" else str(exc), now(), job_id))
            if destination and not self.store.one("SELECT id FROM sources WHERE original LIKE ?", (str(destination.relative_to(self.store.authority)) + "%",)):
                shutil.rmtree(destination, ignore_errors=True)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
            # Failed/cancelled imports retain their upload for explicit retry.
            final = self.store.one("SELECT status FROM jobs WHERE id=?", (job_id,))
            if final["status"] == "succeeded":
                Path(payload["upload"]).unlink(missing_ok=True)

    def _extract(self, data, kind, target, job_id):
        pages = []
        if kind in ("pdf", "docx"):
            import pymupdf
            if kind == "docx":
                from .rich_documents import docx_to_pdf
                docx_to_pdf(data, target/"reading.pdf")
                pdf_data=(target/"reading.pdf").read_bytes()
            else:
                pdf_data=data
            with pymupdf.open(stream=pdf_data, filetype="pdf") as doc:
                if doc.is_encrypted:
                    raise ValueError("PDF 已加密，请先提供可读取的授权副本")
                for i, page in enumerate(doc):
                    self.progress(job_id, 5 + int(85 * i / max(1, len(doc))))
                    text = page.get_text(sort=True)
                    image = f"page-{i+1}.png"
                    page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False).save(target / image)
                    warnings = ["公式和图示尚未人工核对；抽取文本不替代原页"]
                    if kind == "docx":
                        warnings.append("页码为本机 Word 阅读排版；Word 原件及其公式、图片、表格原字节保留")
                    if len(text.strip()) < 30:
                        warnings.append("此页文本稀少或为扫描页；请回页图核对，不自动猜测 OCR")
                    pages.append(dict(number=i+1, label=page.get_label() or str(i+1), text=text, image=image, warnings=warnings))
        elif kind in ("png", "jpg", "jpeg"):
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                im.convert("RGB").save(target / "page-1.png")
            pages.append(dict(number=1, label="图 1", text="", image="page-1.png", warnings=["图中公式、刻度和仪器读数需核对"] ))
        elif kind in ("xlsx","dat"):
            for index,table in enumerate(read_tables(data,kind)):
                self.progress(job_id,30+index)
                text=canonical({"table_id":table["id"],"name":table["name"],"row_count":len(table["rows"]),"columns":table["columns"],"rows":table["rows"]})
                pages.append(dict(number=index+1,label=table["name"],text=text,image=None,warnings=table["warnings"]))
        elif kind=="zip":
            with ZipFile(io.BytesIO(data)) as archive:
                entries=archive.infolist()
                for entry in entries:
                    parts=entry.filename.replace('\\','/').split('/')
                    if '..' in parts or ':' in entry.filename or entry.filename.startswith(('/', '\\')):raise ValueError("ZIP 含越界路径")
                pages.append(dict(number=1,label="压缩包索引",text="\n".join(e.filename for e in entries),image=None,warnings=["仅支持的原始数据附件入库，报告文档不自动作为知识来源。原始 ZIP 字节保留。 "]))
        else:
            text=decode_text(data)
            self.progress(job_id, 75)
            pages.append(dict(number=1, label="全文", text=text, image=None, warnings=[]))
        return pages

    def page_text(self,source_id,page):
        source=self.store.one("SELECT * FROM sources WHERE id=?",(source_id,))
        if source and source["kind"] in ("xlsx","dat"):
            selected,_=select_table(self.store.asset_path(source["original"]).read_bytes(),source["kind"],{"table_id":str(int(page)-1)})
            return canonical({"table_id":selected["id"],"name":selected["name"],"columns":selected["columns"],"row_count":len(selected["rows"]),"rows":selected["rows"]})
        result=self.store.one("SELECT text FROM pages WHERE source_id=? AND number=?",(source_id,page))
        if not result:raise ValueError("来源或页码不存在")
        return result["text"]

    def make_anchor(self, source_id, page, quote="", region=None, status="unverified"):
        src = self.store.one("SELECT * FROM sources WHERE id=?", (source_id,))
        p = self.store.one("SELECT * FROM pages WHERE source_id=? AND number=?", (source_id, int(page)))
        if not src or not p:
            raise ValueError("来源或页码不存在")
        if status not in ("verified", "unverified", "ocr_uncertain"):
            raise ValueError("无效核对状态")
        if quote and quote not in self.page_text(source_id,page) and not (region and p["image"]):
            raise ValueError("引文不在原页文本中；图片公式请选区域并标注待核对")
        if region and (len(region) != 4 or any(not 0 <= v <= 1 for v in region) or region[2] <= region[0] or region[3] <= region[1]):
            raise ValueError("区域需为归一化 [x0,y0,x1,y1]")
        encoded_region=canonical(region) if region else None
        existing = self.store.one("SELECT id FROM anchors WHERE source_id=? AND page=? AND quote=? AND region IS ?", (source_id, int(page), quote,encoded_region))
        if existing:
            if status == "verified":
                self.store.execute("UPDATE anchors SET status='verified' WHERE id=?", (existing["id"],))
            return self.store.anchor(existing["id"])
        identity = uid("anchor")
        self.store.execute("INSERT INTO anchors VALUES(?,?,?,?,?,?,?,?)", (identity, source_id, src["sha"], int(page), p["label"], quote,
                           encoded_region, status))
        return self.store.anchor(identity)

    def preview_dataset(self, source_id,selection=None):
        src = self.store.one("SELECT * FROM sources WHERE id=?", (source_id,))
        if not src:raise ValueError("数据来源不存在")
        selected,tables=select_table(self.store.asset_path(src["original"]).read_bytes(),src["kind"],selection)
        columns,rows=selected["columns"],selected["rows"]
        return {"columns":columns,"rows":rows,"count":len(rows),"tables":tables,"selection":{"table_id":selected["id"]},"missing":{c:sum(str(r.get(c,"")).strip() in ("","--\\--","None") for r in rows) for c in columns}}

    @staticmethod
    def checked_mapping(mapping, columns):
        if not isinstance(mapping,dict) or set(mapping)!=set(columns):raise ValueError("列解释须保留全部实际原列")
        result={};active=0
        for column,value in mapping.items():
            if not isinstance(value,dict):raise ValueError("列解释无效")
            use=value.get("use_for_analysis",True)
            if type(use) is not bool:raise ValueError("是否用于计算须为真实选择")
            item=dict(value)
            if not use:
                if not isinstance(value.get("reason"),str) or not value["reason"].strip():raise ValueError("不用于本次计算的列须说明原因，原始列仍保留")
                item["quantity"]=value.get("quantity") or column;item["unit"]=value.get("unit") or ""
            else:
                if any(not isinstance(value.get(k),str) or not value[k].strip() or value[k].strip().casefold() in ("unknown","pending","未知","待确认","未记录","?","？") for k in ("quantity","unit")):
                    raise ValueError("用于计算的列须核明物理量/通道和单位；无量纲写1，未知不猜")
                item.pop("use_for_analysis",None)
                if "use_for_analysis" in value:item.pop("reason",None)
                active+=1
            result[column]=item
        if not active:raise ValueError("本次没有用于计算的列；原文件可保留在资料库中")
        return result

    def associate_dataset(self, experiment_id, source_id, mapping, confirmed,selection=None):
        self.store.experiment(experiment_id)
        preview = self.preview_dataset(source_id,selection)
        if len(preview["tables"])>1 and selection is None:raise ValueError("必须明确选择工作表／采集段，不能把备份表或另一段测量混入")
        mapping=self.checked_mapping(mapping,preview["columns"])
        if not confirmed:
            raise ValueError("必须明确确认列、通道与单位")
        identity = uid("dataset")
        self.store.execute("INSERT INTO datasets(id,experiment_id,source_id,mapping,confirmed,created,selection) VALUES(?,?,?,?,?,?,?)", (identity, experiment_id, source_id, canonical(mapping), 1, now(),canonical({**(selection or {}),**preview["selection"]})))
        return {"id": identity, **preview}

    def correct_mapping(self,dataset_id,mapping,confirmed,reason,base_revision=1):
        data=self.store.one("SELECT * FROM datasets WHERE id=?",(dataset_id,))
        if not data:raise ValueError("数据关联不存在")
        selection=json.loads(data["selection"]);revision=selection.get("mapping_revision",1)
        if revision!=base_revision:raise ValueError("列解释基线已变化，请重读后修改")
        preview=self.preview_dataset(data["source_id"],selection)
        if not confirmed or not reason.strip():raise ValueError("须核明本次使用列及填写更正依据")
        mapping=self.checked_mapping(mapping,preview["columns"])
        old=json.loads(data["mapping"])
        selection.setdefault("mapping_history",[]).append({"revision":revision,"mapping":old,"basis":selection.get("mapping_basis"),"reason":reason,"updated":now()})
        if "mapping_basis" in selection:selection["mapping_basis"]={"method":"explicit_mapping_correction","reason":reason,"previous_revision":revision,"updated":now()}
        selection["mapping_revision"]=revision+1
        self.store.execute("UPDATE datasets SET mapping=?,selection=? WHERE id=?",(canonical(mapping),canonical(selection),dataset_id))
        self.store.audit("dataset_mapping_corrected",{"dataset_id":dataset_id,"old_revision":revision,"new_revision":revision+1,"reason":reason})
        return {"id":dataset_id,"mapping_revision":revision+1,"original_unchanged":True,"old_program_snapshots_preserved":True}
