"""Native, read-only request ledger; no key or request headers are exposed."""
import json
from pathlib import Path
from PySide6 import QtWidgets as W
from .native_widgets import label,button,row
from .session_runtime import normalized_usage


class UsageDialog(W.QDialog):
    def __init__(self,ui):
        super().__init__(ui);self.ui=ui;self.setWindowTitle('实际模型用量与请求记录');self.resize(1120,700)
        layout=W.QVBoxLayout(self);self.scope=W.QComboBox();self.scope.addItem("全部实验",None)
        if ui.current_exp:self.scope.addItem("当前实验 · "+ui.app.store.experiment(ui.current_exp)["title"],ui.current_exp)
        layout.addWidget(self.scope);self.scope.currentIndexChanged.connect(self.refresh)
        self.summary=label('','muted');layout.addWidget(self.summary)
        layout.addWidget(label('逐次保留请求与返回用量。失败、中断、接续整理均列入记录；未收到服务商用量时显示未知，费用以账单为准。','muted'))
        self.table=W.QTableWidget(0,8);self.table.setHorizontalHeaderLabels(['请求时间','用途','模型／思考','状态','输入 token','输出 token','缓存命中','用时 秒'])
        self.table.setEditTriggers(W.QAbstractItemView.NoEditTriggers);self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeToContents);layout.addWidget(self.table,1)
        layout.addWidget(row(button('刷新',self.refresh),button('导出完整用量记录',self.export),button('关闭',self.accept)));self.refresh()

    def refresh(self):
        runtime=self.ui.app.agent.runtime;eid=self.scope.currentData();total=runtime.summary(experiment_id=eid);self.records=runtime.requests(experiment_id=eid)
        self.summary.setText(f"{self.scope.currentText()}：模型请求 {total['model_requests']} 次；输入 {total['input_tokens']:,}，输出 {total['output_tokens']:,} token；搜索 {total['search_requests']} 次；用量未知 {total['unknown_usage_requests']} 次。")
        self.table.setRowCount(len(self.records))
        from datetime import datetime
        for i,record in enumerate(reversed(self.records)):
            usage=normalized_usage(record['usage']);time=datetime.fromisoformat(record['started']).astimezone().strftime('%m-%d %H:%M:%S')
            missing='不适用（按搜索计）' if record['purpose']=='search' else '未知'
            values=[time,{'model':'任务','context_summary':'历史接续整理','content_review':'文稿内容对照','search':'原生搜索'}.get(record['purpose'],record['purpose']),record['model']+' / '+str(record['thinking_depth']),
                    {'started':'进行中','returned':'已返回','rejected':'服务商拒绝','unknown':'返回不完整／用量待核对'}.get(record['status'],record['status']),
                    f"{usage['input_tokens']:,}" if usage else missing,f"{usage['output_tokens']:,}" if usage else missing,f"{usage['cache_hit_tokens']:,}" if usage else missing,
                    str(round(record['elapsed_ms']/1000,2)) if record['elapsed_ms'] is not None else '进行中']
            for j,value in enumerate(values):
                item=W.QTableWidgetItem(value);item.setToolTip(record.get('error') or record.get('request_id') or '');self.table.setItem(i,j,item)

    def export(self):
        path,_=W.QFileDialog.getSaveFileName(self,'导出完整用量记录','模型用量记录.json','JSON (*.json)')
        if path:
            data={'summary':self.ui.app.agent.runtime.summary(experiment_id=self.scope.currentData()),'requests':self.ui.app.agent.runtime.requests(experiment_id=self.scope.currentData()),'notice':'用量来自服务商实际返回；未返回的用量为未知，不能补零。费用以服务商账单为准。'}
            self.ui.safe(lambda:Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8'))
