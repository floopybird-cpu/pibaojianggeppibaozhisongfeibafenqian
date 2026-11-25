from __future__ import annotations

import csv
import base64
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import threading

from .sandbox import WindowsSandbox, output_files
from .storage import canonical, digest, now, uid
from .tabular import select_table

RUN_IMAGE_SUFFIXES={'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'}

class Analysis:
    def __init__(self, store, gateway, runtime):
        self.store, self.gateway = store, gateway
        self.sandbox = WindowsSandbox(runtime, store.staging)
        self.cancellations = set()

    def datasets(self, experiment_id):
        self.store.experiment(experiment_id)
        datasets = self.store.all("SELECT d.*,s.name,s.sha,s.original,s.kind FROM datasets d JOIN sources s ON d.source_id=s.id WHERE d.experiment_id=?", (experiment_id,))
        for dataset in datasets:
            dataset["mapping"] = json.loads(dataset["mapping"])
            dataset["selection"]=json.loads(dataset["selection"])
            data = self.store.asset_path(dataset.pop("original")).read_bytes()
            selected,_=select_table(data,dataset["kind"],dataset["selection"])
            dataset["rows"]=selected["rows"];dataset["table_name"]=selected["name"]
        return datasets

    def queue(self, experiment_id, program_id, revision, parameters=None):
        obj = self.gateway.read(program_id, revision)
        if obj["experiment_id"] != experiment_id or obj["kind"] != "AnalysisProgram":
            raise ValueError("程序未绑定当前实验")
        run_id = uid("run")
        self.store.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)", (run_id,experiment_id,program_id,revision,"queued","{}",now(),now()))
        threading.Thread(target=self.run_sync,args=(experiment_id,program_id,revision,parameters),kwargs={"run_id":run_id},daemon=True).start()
        return {"id":run_id}

    def run_sync(self, experiment_id, program_id, revision, parameters=None, cancel=None, run_id=None):
        obj = self.gateway.read(program_id, revision)
        if obj["experiment_id"] != experiment_id or obj["kind"] != "AnalysisProgram":
            raise ValueError("程序未绑定当前实验")
        content = obj["content"]
        datasets = self.datasets(experiment_id)
        requested = set(content["inputs"].get("dataset_ids", []))
        selected = [d for d in datasets if d["id"] in requested]
        if not requested or requested != {d["id"] for d in selected} or any(not d["confirmed"] for d in selected):
            raise ValueError("缺本实验已确认列含义与单位的数据；不先运行数值分析")
        if parameters is not None and not isinstance(parameters,dict):raise ValueError("运行参数须为覆盖字段对象")
        parameters = {**content["parameters"],**(parameters or {})}
        snapshot=next((a for a in obj["assets"] if a["name"]=="inputs.json"),None)
        frozen=json.loads(self.store.asset_path(snapshot["path"]).read_text(encoding="utf-8")) if snapshot else {}
        inputs = frozen if "datasets" in frozen else {"datasets":selected,"simulation":content["simulation"]}
        if {d["id"] for d in inputs["datasets"]}!=requested:raise ValueError("固定输入快照与本程序数据身份不符")
        info = {"program_ref":{"id":program_id,"revision":revision},"code_sha":digest(content["code"]),
                "input_sha":digest(canonical(inputs)),"parameters":parameters,"environment":content["environment"],
                "simulation":content["simulation"],"verification":{"status":"pending","note":"成功退出不代表物理结果已核验"}}
        if not run_id:
            run_id=uid("run")
            self.store.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)", (run_id,experiment_id,program_id,revision,"queued",canonical(info),now(),now()))
        self.store.execute("UPDATE runs SET status='running',info=?,updated=? WHERE id=?", (canonical(info),now(),run_id))
        stage=None
        try:
            from .settings import Settings
            config=Settings(self.store.authority).read()
            result=self.sandbox.execute(content["code"],inputs,parameters,timeout=config["analysis_timeout"],memory_mb=config["analysis_memory_mb"],output_mb=config["analysis_output_mb"],
                    cancel=lambda:run_id in self.cancellations or bool(cancel and cancel()),run_id=run_id,
                    extras={a["name"]:self.store.asset_path(a["path"]).read_bytes() for a in obj["assets"] if a["name"]=="analysis_methods.py"})
            stage=Path(result.pop("output"))
            extra={}
            total=0
            for path, _ in output_files(stage):
                attrs = path.lstat().st_file_attributes if hasattr(path.lstat(), "st_file_attributes") else 0
                if path.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError("运行输出含重解析点，拒绝回收")
                if not path.is_file():
                    continue
                data=path.read_bytes(); total+=len(data)
                if path.suffix.lower()==".png":
                    from PIL import Image
                    with Image.open(io.BytesIO(data)) as im:
                        im.verify()
                extra[path.relative_to(stage).as_posix()]=data
            extra["inputs.json"]=json.dumps(inputs,ensure_ascii=False,indent=2).encode()
            extra["parameters.json"]=json.dumps(parameters,ensure_ascii=False,indent=2).encode()
            extra["environment.lock"]=json.dumps(content["environment"],ensure_ascii=False,indent=2).encode()
            info.update(result)
            if "result.json" in extra:
                info["result"]=json.loads(extra["result.json"])
            elif result["status"]=="succeeded":
                info["status"]="failed"
                info["stderr"] += "\n脚本未输出固定接口 result.json，未标完成。"
            result_content={"title":content["title"]+" 运行结果", "run_id":run_id,"program_ref":info["program_ref"],
                            "status":info["status"],"verification":info["verification"],"simulation":content["simulation"],
                            "sources":content["sources"],"run_info":info}
            obj_result=self.gateway.apply("AnalysisResult",result_content,experiment_id=experiment_id,extra_assets=extra)
            info["artifact_id"]=obj_result["id"]
            self.store.execute("UPDATE runs SET status=?,info=?,updated=? WHERE id=?", (info["status"],canonical(info),now(),run_id))
            return {"id":run_id,**info}
        except Exception as exc:
            info.update(status="failed",error=str(exc))
            from .sandbox import RuntimePreparationUnavailable
            if isinstance(exc, RuntimePreparationUnavailable):
                info.update(error_code=exc.error_code, failure_layer="environment",
                            phase=exc.phase, execution_started=exc.execution_started,
                            retryable=exc.retryable,
                            next_action="修复应用运行环境后再试；改写分析代码不能解决这次启动错误。")
            self.store.execute("UPDATE runs SET status='failed',info=?,updated=? WHERE id=?", (canonical(info),now(),run_id))
            return {"id":run_id,**info}
        finally:
            self.cancellations.discard(run_id)
            if stage:
                shutil.rmtree(stage.parent,ignore_errors=True)

    def inspect(self, experiment_id, run_id, *, file=None, offset=0, count=None, include_images=False):
        row=self.store.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not row or row["experiment_id"]!=experiment_id:
            raise ValueError("运行不存在或不属于本实验")
        row["info"]=json.loads(row["info"])
        if offset<0 or (count is not None and count<1):raise ValueError('offset应非负，count应为正；省略count读取全文')
        identity=row['info'].get('artifact_id')
        artifact=self.gateway.read(identity) if identity else None
        assets={a['name']:a for a in artifact['assets']} if artifact else {}
        row['files']=[{'name':name,'sha256':a['sha'],'bytes':self.store.asset_path(a['path']).stat().st_size} for name,a in sorted(assets.items())]
        def read_image(name):
            from PIL import Image
            data=self.store.asset_path(assets[name]['path']).read_bytes()
            with Image.open(io.BytesIO(data)) as picture:
                picture.verify();mime=Image.MIME.get(picture.format,'application/octet-stream')
            return {'name':name,'sha256':assets[name]['sha'],'_image':'data:'+mime+';base64,'+base64.b64encode(data).decode('ascii')}
        if file is not None:
            if file not in assets:raise ValueError('运行产物中没有这个文件，请按files中的准确名称读取')
            data=self.store.asset_path(assets[file]['path']).read_bytes()
            if Path(file).suffix.lower() in RUN_IMAGE_SUFFIXES:
                if not include_images:raise ValueError('当前原生图像已关闭，未发送运行产物图像，不能声称已看图')
                row['output']=read_image(file)
            else:
                try:text=data.decode('utf-8-sig')
                except UnicodeDecodeError:raise ValueError('该产物不是UTF-8文本或受支持图片；完整原文件仍可导出打开') from None
                lines=text.splitlines(keepends=True);end=len(lines) if count is None else min(len(lines),offset+count)
                row['output']={'name':file,'sha256':assets[file]['sha'],'text':''.join(lines[offset:end]),'offset':offset,'total_lines':len(lines),'next_offset':end if end<len(lines) else None,'complete':offset==0 and end==len(lines)}
        else:
            if offset or count is not None:raise ValueError('逐行读取请先指定files中的file名称')
            names=[name for name in sorted(assets) if Path(name).suffix.lower() in RUN_IMAGE_SUFFIXES]
            if include_images:
                previews=[]
                for name in names:
                    try:previews.append(read_image(name))
                    except (ValueError,OSError) as exc:previews.append({'name':name,'error':str(exc),'image_not_provided':True})
                row['output_previews']=previews
            else:row['image_files_not_sent']=names
        return row


def run_image_receipts(result):
    if isinstance(result,list):return [item for value in result for item in run_image_receipts(value)]
    if not isinstance(result,dict):return []
    candidates=list(result.get('output_previews',[]))
    if isinstance(result.get('output'),dict):candidates.append(result['output'])
    found=[{'run_id':result['id'],'name':item['name'],'sha256':item['sha256']}
            for item in candidates if result.get('id') and isinstance(item,dict) and item.get('_image') and item.get('name') and item.get('sha256')]
    for key in ('results','result'):found.extend(run_image_receipts(result.get(key)))
    return found


def pending_run_image_reviews(agent,completed,experiment_id,job_id):
    from .document_review import vision_enabled
    if not vision_enabled(agent,job_id):return []
    latest={};seen=set()
    for receipt in completed:
        if not receipt.get('success'):continue
        for run in receipt.get('generated_runs',[]):latest[run['program_id']]=run['id']
        seen.update((item['run_id'],item['name'],item['sha256']) for item in receipt.get('run_image_inspections',[]))
    pending=[]
    for run_id in latest.values():
        run=agent.analysis.inspect(experiment_id,run_id)
        if run['status']!='succeeded':continue
        files=[item for item in run['files'] if Path(item['name']).suffix.lower() in RUN_IMAGE_SUFFIXES
               and (run_id,item['name'],item['sha256']) not in seen]
        if files:pending.append({'run_id':run_id,'files':files})
    return pending
