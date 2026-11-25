from PySide6 import QtCore as C, QtWidgets as W
from .native_widgets import label, button, plain, row, scroll_body


class QuestionsDialog(W.QDialog):
    def __init__(self,ui,experiment_id=None,include_all=False):
        super().__init__(None);self.ui=ui
        self.setWindowTitle("助手需要确认");self.resize(850,790)
        layout=W.QVBoxLayout(self);layout.addWidget(label("请按实际情况回答。可以选建议项，也可以直接说自己的情况；没有记录就写“未记录”。","muted"))
        scroll,_,body=scroll_body();layout.addWidget(scroll,1)
        records=ui.app.questions.pending(experiment_id,include_all)
        if not records:body.addWidget(label("当前没有等待答复的问题。"));return
        for record in records:
            group=W.QGroupBox(record["title"]);box=W.QVBoxLayout(group);fields=[]
            for question in record["questions"]:
                box.addWidget(label(question["question"],"h3"))
                if question.get('material_basis'):
                    references=[]
                    for identity in question['material_basis']['sources']:
                        anchor=ui.app.store.anchor(identity);source=ui.app.store.one('SELECT name FROM sources WHERE id=?',(anchor['source_id'],))
                        references.append(source['name']+' p'+str(anchor['page']))
                    box.addWidget(label('依据：'+'；'.join(dict.fromkeys(references)),'muted'))
                choices=W.QButtonGroup(group);choices.setExclusive(True)
                for option in question.get("options",[]):
                    control=W.QRadioButton(option);choices.addButton(control);box.addWidget(control)
                answer=plain(height=75);answer.setPlaceholderText("直接回答，补充说明，或写“未记录”……");box.addWidget(answer)
                fields.append((question,choices,answer))
            def submit(checked=False,r=record,f=fields,g=group):
                answers=[];individual={}
                for question,choices,field in f:
                    selected=choices.checkedButton();value=field.toPlainText().strip()
                    if selected:value=selected.text()+("；"+value if value else "")
                    if not value:self.ui.notify("请回答每个问题；暂时不知道可以直接写“未记录”。");return
                    answers.append(question["question"]+"\n答复："+value)
                    individual[question["id"]]=value
                result=self.ui.safe(lambda:self.ui.app.questions.answer(r["id"],"\n\n".join(answers),individual))
                if result:g.setEnabled(False);self.ui.refresh();self.ui.status.showMessage("答复已保存，助手正在接着处理",8000)
            box.addWidget(button("提交答复并继续",submit,True));body.addWidget(group)
        body.addStretch();layout.addWidget(button("稍后回答",self.accept))
