"""The native experiment home. Its primary action follows the saved work in this experiment."""
from PySide6 import QtCore as C, QtWidgets as W

from .native_widgets import label, button
from .native_markdown import MarkdownView


def small_button(caption, callback):
    widget = button(caption, callback)
    widget.setProperty("quiet", True)
    return widget


def card(layout, name="panel"):
    frame = W.QFrame()
    frame.setObjectName(name)
    frame.setSizePolicy(W.QSizePolicy.Preferred,W.QSizePolicy.Maximum)
    body = W.QVBoxLayout(frame)
    body.setContentsMargins(18, 15, 18, 15)
    body.setSpacing(8)
    layout.addWidget(frame)
    return frame, body


def heading(layout, caption, action=None):
    line = W.QHBoxLayout()
    line.addWidget(label(caption, "section"))
    line.addStretch()
    if action:
        line.addWidget(action)
    layout.addLayout(line)


class HomeColumns(W.QBoxLayout):
    def __init__(self):
        super().__init__(W.QBoxLayout.LeftToRight)
        self.setSpacing(18)

    def setGeometry(self, rect):
        direction = W.QBoxLayout.TopToBottom if rect.width() < 775 else W.QBoxLayout.LeftToRight
        if self.direction() != direction:
            self.setDirection(direction)
        super().setGeometry(rect)


def build_home(ui):
    scroll = W.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(W.QFrame.NoFrame)
    page = W.QWidget()
    scroll.setWidget(page)
    ui.pages["session"] = scroll
    ui.stack.addWidget(scroll)
    layout = W.QVBoxLayout(page)
    layout.setContentsMargins(30, 22, 30, 22)
    layout.setSpacing(13)
    back = small_button("‹  我的实验", lambda:ui.switch("experiments"))
    layout.addWidget(back, 0, C.Qt.AlignLeft)
    ui.exp_title = label("", "h1")
    layout.addWidget(ui.exp_title)
    divider = W.QFrame()
    divider.setFrameShape(W.QFrame.HLine)
    divider.setObjectName("divider")
    layout.addWidget(divider)

    columns = HomeColumns()
    layout.addLayout(columns, 1)
    left, right = W.QWidget(), W.QWidget()
    left.setMinimumWidth(290)
    right.setMinimumWidth(215)
    columns.addWidget(left, 7)
    columns.addWidget(right, 4)
    main, side = W.QVBoxLayout(left), W.QVBoxLayout(right)
    for body in (main, side):
        body.setContentsMargins(0,0,0,0)
        body.setSpacing(12)
    heading(main, "数据处理")
    _, action = card(main, "actionCard")
    ui.card_title = label("", "cardTitle")
    ui.exp_context = label("", "bodyNote")
    action.addWidget(ui.card_title)
    action.addWidget(ui.exp_context)
    controls = W.QHBoxLayout()
    ui.worksheet_button = button("", ui.home_action, True)
    controls.addWidget(ui.worksheet_button)
    ui.open_partial = small_button("打开已有页", lambda:ui.activity("worksheet"))
    controls.addWidget(ui.open_partial)
    controls.addStretch()
    action.addLayout(controls)

    heading(main, "实验问答")
    ui.messages = MarkdownView("", ui.app.store, auto_height=False, strict=False)
    ui.messages.anchorClicked.connect(ui.follow_source)
    ui.messages.setMinimumHeight(145)
    ui.messages.setSizePolicy(W.QSizePolicy.Expanding, W.QSizePolicy.Expanding)
    main.addWidget(ui.messages, 1)
    ui.shown_messages = None
    _, compose = card(main, "composer")
    ui.prompt = W.QPlainTextEdit()
    ui.prompt.setObjectName("questionInput")
    ui.prompt.setPlaceholderText("问讲义里的步骤或公式，也可以说想怎么处理数据…")
    ui.prompt.setFixedHeight(65)
    compose.addWidget(ui.prompt)
    bar = W.QHBoxLayout()
    bar.addWidget(small_button("添加文件", ui.import_into_experiment))
    ui.depth = W.QComboBox()
    ui.depth.setAccessibleName("思考深度")
    for title, value in [("关闭","off"),("低","low"),("高","high"),("极高","max")]:
        ui.depth.addItem("思考深度 · "+title, value)
    ui.depth.activated.connect(lambda _:ui.set_options(thinking_depth=ui.depth.currentData()))
    bar.addWidget(ui.depth)
    bar.addStretch()
    ui.cancel = small_button("取消任务", ui.cancel_agent)
    ui.send = button("发送", ui.send_message, True)
    bar.addWidget(ui.cancel)
    bar.addWidget(ui.send)
    compose.addLayout(bar)
    ui.more_button = small_button("更多工具", None)
    ui.more_menu = W.QMenu(ui.more_button)
    ui.more_button.setMenu(ui.more_menu)
    main.addWidget(ui.more_button, 0, C.Qt.AlignLeft)
    main.addStretch()

    _, materials = card(side)
    add = small_button("添加资料", None)
    menu = W.QMenu(add)
    menu.addAction("从电脑添加…", ui.import_into_experiment)
    menu.addAction("从资料库选用…", ui.edit_experiment)
    add.setMenu(menu)
    heading(materials, "本实验材料", add)
    ui.materials_list = W.QListWidget()
    ui.materials_list.setObjectName("materialList")
    ui.materials_list.setWordWrap(True)
    ui.materials_list.setMinimumHeight(48)
    ui.materials_list.itemDoubleClicked.connect(lambda _: ui.open_home_source())
    materials.addWidget(ui.materials_list)
    ui.no_material = label("可添加讲义、课件或实测数据。","bodyNote")
    materials.addWidget(ui.no_material)
    ui.source_open = small_button("打开原件", ui.open_home_source)
    materials.addWidget(ui.source_open, 0, C.Qt.AlignLeft)
    ui.import_note = label("", "bodyNote")
    materials.addWidget(ui.import_note)
    ui.retry_import = small_button("重试导入", ui.retry_home_import)
    materials.addWidget(ui.retry_import, 0, C.Qt.AlignLeft)

    ui.output_panel, outputs = card(side)
    heading(outputs, "实验成果", small_button("查看文件", lambda:ui.open_products(True)))
    ui.artifacts = W.QListWidget()
    ui.artifacts.setWordWrap(True)
    ui.artifacts.setMaximumHeight(140)
    ui.artifacts.itemDoubleClicked.connect(lambda i:ui.open_object(i.data(C.Qt.UserRole)))
    outputs.addWidget(ui.artifacts)
    actions = W.QHBoxLayout()
    actions.addWidget(small_button("打开", lambda:ui.open_object(ui.artifacts.currentItem().data(C.Qt.UserRole)) if ui.artifacts.currentItem() else None))
    actions.addWidget(small_button("所在文件夹", ui.open_home_folder))
    actions.addStretch()
    outputs.addLayout(actions)

    ui.data_panel, data = card(side)
    heading(data, "实验数据", small_button("更正列说明", ui.correct_dataset))
    ui.data_runs = W.QListWidget()
    ui.data_runs.setMaximumHeight(125)
    ui.data_runs.itemDoubleClicked.connect(ui.open_run)
    data.addWidget(ui.data_runs)
    data.addWidget(label("双击数据或运行记录可查看详情。","bodyNote"))
    side.addStretch(1)


def home_styles():
    return """
    QFrame#actionCard {background:#eaf2e9;border:1px solid #c9dccd;border-radius:10px;}
    QFrame#panel,QFrame#composer {background:#ffffff;border:1px solid #d5e2d8;border-radius:10px;}
    QFrame#divider {color:#cfdbd1; max-height:1px;}
    QLabel[role='cardTitle'] {font-size:20px;font-weight:650;color:#205542;}
    QLabel[role='section'] {font-size:17px;font-weight:650;color:#263e32;}
    QLabel[role='bodyNote'] {font-size:13px;color:#536857;}
    QPushButton[quiet='true'] {border:0;background:transparent;color:#276550;padding:5px 6px;text-align:left;}
    QPushButton[quiet='true']:hover {background:#e6efe7;}
    QListWidget#materialList {border:0;background:white;}
    QPlainTextEdit#questionInput {background:#fbfdfb;border:1px solid #aebfb2;border-radius:6px;padding:8px;}
    QPlainTextEdit#questionInput:focus {border:1px solid #397a5b;}
    QListWidget#experimentList::item {border-bottom:1px solid #e2eae4;padding:11px;}
    QWidget#sidebar {background:#edf2eb;}
    """
