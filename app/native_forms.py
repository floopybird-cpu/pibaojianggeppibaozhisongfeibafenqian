from __future__ import annotations

from PySide6 import QtCore as C, QtWidgets as W
from .native_widgets import label,button,row,plain,text,table,dataset_table,scroll_body
from .network import ModelClient


class SettingsDialog(W.QDialog):
    def __init__(self,ui):
        super().__init__(ui);self.ui=ui;self.setWindowTitle("设置");self.resize(760,700)
        config=ui.app.settings.read();self.fields={};outer=W.QVBoxLayout(self)
        self.tabs=W.QTabWidget();outer.addWidget(self.tabs,1)
        scroll,body,layout=scroll_body();self.tabs.addTab(scroll,"模型与课程")
        layout.addWidget(label("密钥只在本机用 Windows DPAPI 加密保存。模型按任务接收选用的资料，原生图像、联网和思考档位会真实发送给接口。","muted"))
        form=W.QFormLayout();layout.addLayout(form)
        for key,title in [("base_url","HTTPS API 根地址"),("model","模型 ID"),("api_key","API 密钥 留空保留"),("font","正文字体（留空使用文稿主题）"),("language","文档语言 zh-CN / en-US 等")]:
            field=W.QLineEdit("" if key=="api_key" else config[key]);self.fields[key]=field
            if key=="api_key":field.setEchoMode(W.QLineEdit.Password);field.setPlaceholderText("已加密保存" if config["configured"] else "填写你自己的 API 密钥")
            form.addRow(title,field)
        self.protocol=W.QComboBox();self.protocol.addItem("Chat Completions + 服务商原生搜索","chat");self.protocol.addItem("Responses 原生工具／多模态","responses");self.protocol.setCurrentIndex(self.protocol.findData(config["protocol"]));form.addRow("调用协议",self.protocol)
        self.depth=W.QComboBox()
        for title,value in [("关闭","off"),("低","low"),("高","high"),("极高","max")]:self.depth.addItem(title,value)
        self.depth.setCurrentIndex(self.depth.findData(config["thinking_depth"]));form.addRow("默认思考深度",self.depth)
        self.search=W.QCheckBox("开启服务商原生联网能力");self.search.setChecked(config["native_search"])
        self.vision=W.QCheckBox("使用模型原生图像理解 直接核对页图");self.vision.setChecked(config["native_vision"])
        form.addRow(self.search);form.addRow(self.vision)
        self.transport=W.QComboBox();self.transport.addItem("服务商 Search / Fetch API","provider");self.transport.addItem("Responses 内置 web_search","builtin");self.transport.setCurrentIndex(self.transport.findData(config["native_search_transport"]));form.addRow("原生联网通道",self.transport)
        self.rules=plain(config["course_rules"],height=110);form.addRow("本课规范与仪器约定",self.rules)
        self.products=W.QLineEdit(config.get("products_directory","") or str(ui.app.publications.root))
        def choose_products():
            value=W.QFileDialog.getExistingDirectory(self,"选择内部记录之外的产物文件夹",self.products.text())
            if value:self.products.setText(value)
        form.addRow("生成产物文件夹",row(self.products,button("选择",choose_products)))
        self.timeout=W.QSpinBox();self.timeout.setRange(0,2147483647);self.timeout.setValue(config["timeout"]);self.timeout.setSuffix(" 秒");self.timeout.setSpecialValueText("不设时限");form.addRow("单次模型请求超时",self.timeout)
        self.analysis_timeout=W.QSpinBox();self.analysis_timeout.setRange(0,2147483647);self.analysis_timeout.setValue(config["analysis_timeout"]);self.analysis_timeout.setSuffix(" 秒");self.analysis_timeout.setSpecialValueText("不设时限，可随时取消");form.addRow("单次数值运行超时",self.analysis_timeout)
        self.resource_limits={}
        for key,title in [("analysis_memory_mb","数值运行内存上限"),("analysis_output_mb","数值运行输出上限")]:
            field=W.QSpinBox();field.setRange(0,2147483647);field.setValue(config[key]);field.setSuffix(" MB");field.setSpecialValueText("不设额外限额");self.resource_limits[key]=field;form.addRow(title,field)
        self.output_tokens=W.QSpinBox();self.output_tokens.setRange(0,2147483647);self.output_tokens.setValue(config["max_output_tokens"]);self.output_tokens.setSpecialValueText("使用服务商完整输出能力");form.addRow("每次最大输出 token",self.output_tokens)
        self.clear_key=W.QCheckBox("清除本机已保存密钥");form.addRow(self.clear_key)
        self.test_result=label("", "muted");layout.addWidget(self.test_result)
        layout.addWidget(row(button("保存设置",self.save,True),button("测试真实 API",self.test_connection)))
        if ui.current_exp:
            pane=W.QWidget();options=W.QVBoxLayout(pane);self.tabs.addTab(pane,"本实验偏好")
            exp=ui.app.store.experiment(ui.current_exp)
            options.addWidget(label(exp["title"],"h2"))
            current={**config,**exp["agent_options"]}
            for key,caption in (("native_search","允许联网查资料"),("native_vision","允许模型阅读原页图像"),("worksheet_quick","并行生成独立的实验页")):
                option=W.QCheckBox(caption);option.setChecked(current.get(key,True))
                option.clicked.connect(lambda checked,k=key:ui.set_options(**{k:checked}))
                options.addWidget(option)
            options.addStretch()
        tools=W.QWidget();diagnostics=W.QVBoxLayout(tools);self.tabs.addTab(tools,"开发工具")
        diagnostics.addWidget(label("运行记录","h2"))
        diagnostics.addWidget(button("用量与请求记录",ui.open_usage))
        diagnostics.addWidget(button("任务记录",ui.open_jobs))
        diagnostics.addWidget(button("预置格式与说明",ui.formats))
        diagnostics.addWidget(button("检查计算环境",self.test_environment))
        self.env_result=label("","muted");diagnostics.addWidget(self.env_result)
        if (ui.app.directory / "examples").is_dir():
            diagnostics.addWidget(button("载入教学模拟",lambda:(self.accept(),ui.demo())))
        diagnostics.addStretch()
        outer.addWidget(button("关闭",self.accept),0,C.Qt.AlignRight)
    def save(self):
        values={k:w.text() for k,w in self.fields.items()}
        values.update(protocol=self.protocol.currentData(),thinking_depth=self.depth.currentData(),native_search=self.search.isChecked(),native_vision=self.vision.isChecked(),native_search_transport=self.transport.currentData(),course_rules=self.rules.toPlainText(),timeout=self.timeout.value(),analysis_timeout=self.analysis_timeout.value(),max_output_tokens=self.output_tokens.value(),clear_key=self.clear_key.isChecked())
        values.update({k:f.value() for k,f in self.resource_limits.items()})
        values["products_directory"]=self.products.text()
        result=self.ui.safe(lambda:self.ui.app.post("/api/settings",values))
        if result:self.fields["api_key"].clear();self.test_result.setText("设置已保存。实验中的思考按钮可独立切换。 ");self.ui.refresh()
    def test_connection(self):
        self.save();self.test_result.setText("正在请求真实模型…")
        self.ui.submit("API 连通性测试",lambda:ModelClient(self.ui.app.settings,self.ui.app.agent.runtime).complete([{"role":"user","content":"请只回答连接成功。"}]),lambda result:self.test_result.setText(str(result[0].get("content"))))
    def test_environment(self):
        from .environment_check import check_environment
        self.env_result.setText("正在实际计算、绘图并检查文件隔离…")
        self.ui.submit("计算环境检查",lambda:check_environment(self.ui.app.analysis.sandbox.runtime,self.ui.app.store.staging/'environment-checks'),
                       lambda result:self.env_result.setText("计算、绘图和文件隔离检查通过。未调用模型。记录："+result['report_file']))


class ExperimentDialog(W.QDialog):
    def __init__(self,ui,existing=None):
        super().__init__(ui);self.ui=ui;self.existing=existing;self.setWindowTitle("背景与选用教材" if existing else "建立一个实验");self.resize(765,605)
        exp=existing or {};bg=exp.get("background",{});layout=W.QVBoxLayout(self);form=W.QFormLayout();layout.addLayout(form)
        self.title=W.QLineEdit(exp.get("title",""));self.notes=plain(exp.get("notes",""),height=66);self.notes.setPlaceholderText("可填写资料中哪份是本次讲义、要做哪几项，或现场器材和讲义有什么差别。")
        self.level=plain(bg.get("level","") if existing else "微积分、线性代数、高中物理",height=57);self.level.setPlaceholderText("写下已经学过的内容，也可以补充想从哪里讲起。");self.learning=W.QLineEdit(bg.get("goal",""));self.minutes=W.QSpinBox();self.minutes.setRange(0,2147483647);self.minutes.setSpecialValueText('未指定');self.minutes.setValue(int(bg.get("minutes") or 0))
        self.notes.setFixedHeight(72);self.level.setFixedHeight(59)
        self.title.setPlaceholderText("例如：波动光学");self.learning.setPlaceholderText("例如：想看懂图样与公式的关系（选填）")
        for name,field in [("实验名称",self.title),("实验备注",self.notes),("已学知识",self.level),("学习目标",self.learning),("可用时长 分钟",self.minutes)]:form.addRow(name,field)
        layout.addWidget(label("勾选本实验使用的资料"));self.sources=W.QListWidget();self.sources.setSelectionMode(W.QAbstractItemView.NoSelection)
        for source in ui.app.state(include_usage=False)["sources"]:
            item=W.QListWidgetItem(source["name"]+("  [教学模拟]" if source["metadata"].get("simulation") else ""));item.setData(C.Qt.UserRole,source["id"]);self.sources.addItem(item);item.setFlags(item.flags()|C.Qt.ItemIsUserCheckable);item.setCheckState(C.Qt.Checked if source["id"] in exp.get("source_ids",[]) else C.Qt.Unchecked)
        layout.addWidget(self.sources);layout.addWidget(label("可以稍后在实验里添加讲义、课件或数据。","muted"));layout.addWidget(button("保存",self.save,True))
    def save(self):
        if not self.title.text().strip():
            self.ui.notify("请给这个实验或对话起个名称。");self.title.setFocus();return
        background={"level":self.level.toPlainText().strip() or '尚未提供学习背景',"goal":self.learning.text().strip() or '待在对话中说明学习目标'}
        if self.minutes.value():background['minutes']=self.minutes.value()
        values={"title":self.title.text(),"goal":(self.existing or {}).get("goal") or '待在对话中说明实验目标',"notes":self.notes.toPlainText().strip(),"background":background,"source_ids":[self.sources.item(j).data(C.Qt.UserRole) for j in range(self.sources.count()) if self.sources.item(j).checkState()==C.Qt.Checked]}
        if self.existing:values["baseline_source_ids"]=self.existing["source_ids"]
        path="/api/experiments/"+self.existing["id"]+"/settings" if self.existing else "/api/experiments"
        result=self.ui.safe(lambda:self.ui.app.post(path,values))
        if result:self.ui.open_experiment(result["id"]);self.accept()


class DatasetDialog(W.QDialog):
    def __init__(self,ui,experiment_id,existing=None):
        super().__init__(None);self.ui=ui;self.experiment_id=experiment_id;self.setWindowTitle("关联当前实验的原始数据");self.resize(920,700)
        self.existing=existing
        outer=W.QVBoxLayout(self);scroll,body,layout=scroll_body();outer.addWidget(scroll,1);layout.addWidget(label("逐列核对物理量、通道与单位。未知单位先保留缺项，不给数值结论。","muted"))
        self.source=W.QComboBox();self.source.addItem("选择已导入的 CSV / TSV / XLSX / DAT",None)
        for item in ui.app.state()["sources"]:
            if item["kind"] in ("csv","tsv","xlsx","dat"):self.source.addItem(item["name"],item["id"])
        layout.addWidget(self.source);self.table_selection=W.QComboBox();layout.addWidget(row(label("工作表／采集段"),self.table_selection));self.table_selection.currentIndexChanged.connect(self.changed_table)
        self.preview=W.QVBoxLayout();layout.addLayout(self.preview);self.mappings={}
        self.confirmed=W.QCheckBox("我已核明本次用于计算的列、通道和单位 无量纲写1");outer.addWidget(self.confirmed);outer.addWidget(button("关联到本实验",self.save,True));self.source.currentIndexChanged.connect(self.changed)
        self.reason=W.QLineEdit();self.reason.setPlaceholderText("更正依据：原始列名、仪器/接线核实或课程页码；原件和旧程序快照保留")
        if existing:
            outer.insertWidget(1,self.reason);self.source.setCurrentIndex(self.source.findData(existing["source_id"]))
            self.table_selection.setCurrentIndex(self.table_selection.findData(existing["selection"]["table_id"]))
            self.source.setEnabled(False);self.table_selection.setEnabled(False)
    def changed(self):
        self.table_selection.blockSignals(True);self.table_selection.clear()
        if self.source.currentData():
            preview=self.ui.app.materials.preview_dataset(self.source.currentData())
            for item in preview["tables"]:self.table_selection.addItem(f"{item['name']} · {item['count']} 行",item["id"])
        self.table_selection.blockSignals(False);self.changed_table()
    def changed_table(self):
        from .native_widgets import clear
        clear(self.preview);self.mappings={};self.skip_columns={};self.confirmed.setChecked(False)
        source_id=self.source.currentData()
        if not source_id:return
        preview=self.ui.app.materials.preview_dataset(source_id,{"table_id":self.table_selection.currentData() or "0"})
        grid=dataset_table(preview["columns"],preview["rows"]);grid.setMaximumHeight(350);self.preview.addWidget(grid)
        self.preview.addWidget(label(f"共 {preview['count']} 行；缺失项："+"，".join(f"{c} {n}" for c,n in preview["missing"].items())))
        for col in preview["columns"]:
            quantity,unit=W.QLineEdit(),W.QLineEdit();quantity.setPlaceholderText("物理量或通道含义");unit.setPlaceholderText("单位")
            skip=W.QCheckBox("不用于计算");self.skip_columns[col]=skip
            self.mappings[col]=(quantity,unit);self.preview.addWidget(row(label(col),quantity,unit,skip))
            if self.existing:
                previous=self.existing["mapping"][col];quantity.setText(previous.get("quantity") or "");unit.setText(previous.get("unit") or "")
                skip.setChecked(previous.get("use_for_analysis") is False)
            elif preview["count"] and preview["missing"][col]==preview["count"]:
                skip.setChecked(True);quantity.setText(col)
            skip.toggled.connect(lambda checked,q=quantity,u=unit:(q.setEnabled(not checked),u.setEnabled(not checked)))
            quantity.setEnabled(not skip.isChecked());unit.setEnabled(not skip.isChecked())
    def save(self):
        mapping={key:{"quantity":q.text(),"unit":u.text()} for key,(q,u) in self.mappings.items()}
        for key,value in mapping.items():
            if self.skip_columns[key].isChecked():value.update(use_for_analysis=False,reason="本次不用于计算，原列保留")
            elif self.existing and self.existing["mapping"][key].get("use_for_analysis") is False:value["use_for_analysis"]=True
        if self.existing:
            mapping={key:{**self.existing["mapping"][key],**value} for key,value in mapping.items()}
            result=self.ui.safe(lambda:self.ui.app.materials.correct_mapping(self.existing["id"],mapping,self.confirmed.isChecked(),self.reason.text(),self.existing["selection"].get("mapping_revision",1)))
        else:result=self.ui.safe(lambda:self.ui.app.materials.associate_dataset(self.experiment_id,self.source.currentData(),mapping,self.confirmed.isChecked(),{"table_id":self.table_selection.currentData()}))
        if result:self.ui.refresh_session();self.accept()


class CategoryDialog(W.QDialog):
    def __init__(self,ui):
        super().__init__(ui);self.ui=ui;self.setWindowTitle("管理动态目录");self.resize(590,210)
        layout=W.QFormLayout(self);self.categories=ui.app.state()["categories"]
        from .knowledge_catalog import category_paths
        self.paths=category_paths(ui.app.store)
        self.identity=W.QComboBox();self.identity.addItem("新建目录",None)
        for identity,path in sorted(self.paths.items(),key=lambda item:item[1]):
            self.identity.addItem(" › ".join(path),identity)
        self.name=W.QLineEdit();self.parent=W.QComboBox()
        self.identity.currentIndexChanged.connect(self.changed)
        for name,widget in [("操作对象",self.identity),("名称",self.name),("上级目录",self.parent)]:layout.addRow(name,widget)
        layout.addRow(button("保存目录",self.save,True));self.changed()
    def changed(self):
        identity=self.identity.currentData()
        item=next((c for c in self.categories if c["id"]==identity),None)
        self.name.setText(item["name"] if item else "")
        path=self.paths.get(identity)
        self.parent.clear();self.parent.addItem("顶层",None)
        for key,parts in sorted(self.paths.items(),key=lambda pair:pair[1]):
            if not path or parts[:len(path)]!=path:self.parent.addItem(" › ".join(parts),key)
        if item:self.parent.setCurrentIndex(self.parent.findData(item["parent"]))
    def save(self):
        result=self.ui.safe(lambda:self.ui.app.gateway.category(self.name.text(),self.parent.currentData(),category_id=self.identity.currentData()))
        if result:self.ui.refresh();self.accept()
