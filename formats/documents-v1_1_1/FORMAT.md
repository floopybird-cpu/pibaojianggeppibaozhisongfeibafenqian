# 学习文档与实验单包

发布版本 1.1.1，数据版本 2，宿主接口 1。科学内容模型与版式分离，新增 Markdown 和自由布局。旧数据是新模型的子集；1.0.4 原包在 documents-legacy 保留，固定旧资产不改写。升级预览基线后生成新修订，不替旧稿编造事实。

## 内容与布局

学生说用途、深浅和排版偏好，Agent 负责按本包编排，不让学生填写 JSON 或模板。短预习可只有一个完整章节，也可自由展开；深入版仍有 math_basis 和 derivation；实验单仍有可分别定位的 records、errors、instruments、reading 附录，但位置、标题、分段和页数不固定。

markdown{text,sources} 使用 CommonMark，支持标题、粗斜体、删除线、嵌套列表、引用、代码块、链接、Markdown 表格、$行内数学$、$$独立公式$$、\(数学\) 和 \[数学\]。需编号或引用知识修订的公式继续用 equation。与 paragraph、equation、table、figure、code、crossref 任意混排，不以纯文本替代正式文稿。图片用 ![说明](source:来源锚点)，锚点也列入本块 sources；章节链接用 #章节ID。HTML 原文仅作文字，不执行。

columns{columns:[{blocks:[...]}],weights:[...]} 并排图与短说明，允许嵌套，按权重分配物理宽度。Word 用无边框布局表，PDF 用 Typst grid。pagebreak 控制分页，space{height_pt} 留手写空间。section 可设 page_break_before、heading_level、show_heading；table 可设 column_weights；figure 可设 width_cm、align。宽度受纸张空间约束，不设正文、章节、数据行或输出总量配额。

goal_summary 与 procedure 可在合适章节安排目标读数和步骤；show_title:false 使用所在章节标题，避免重复。未放置时完整补到正文后。目标、条件、读数、单位、时机、仪器精度、理由和完成判据必须可读，不能为美化而隐藏。已显示的固定公式不重复抄排，未知项明确待确认。

## 主题资源

template.json 选择文稿类型的默认主题并索引包内资源。reading.json 为舒展阅读，worksheet.json 为现场记录，classic.json 为传统讲义。新增主题只需包内 JSON、资源声明和新发布版本，不改宿主。style.theme 选择模板中的主题键名，可用新主题。

每稿可覆盖 font、language、font_size、line_spacing、paragraph_spacing、title_size、heading_size、subheading_size、table_font_size、table_padding_pt、source_font_size、source_spacing、margins_cm、page、orientation、accent、table_header、table_header_text、table_stripe、show_background、flow。颜色用六位 RGB，不带 #。字号／行距需有限正数，间距可为零。flow 使用 summary/background/warnings/sections/goals/steps/appendices/sources；遗漏必要内容会补出，不能删掉科学内容或待确认事项。

## 生成与交付

schema.json 声明内容，program.json 的 apply 执行结构、来源与科学关联检查；build 调用通用 Markdown／OOXML 编排器与随应用提供的 Typst 0.15.1；finish 检查全部必需资产。包仍是 format-pipeline/1 的有界数据程序，不执行包内 Python、任意 Typst 脚本或联网下载。模板固定在包内，图片只读本次冻结原页，保留裁切。Typst 源由可信生成器从转义后的文本和本地图片／数学生成，不导入外部包。

Word 数学为可编辑 OMML；PDF 数学使用矢量路径，正文可选择。PDF 按相同内容与主题独立排版，支持指定列宽、分栏、表头重复与跨页，不保证与各 Office 软件逐行同页。用户界面是原生 Qt，内容阅读也使用 Qt 富文本和数学图像，不打开 WebView。

正式保存生成 document.docx、document.pdf、document.md、preview.html、sources.json、design.json、composition.json 与排版源文件/document.typ 及其全部矢量和图片。缺必需文件不能完成。独立产物以 Word/PDF 为主，文稿源文件单独分类，导出 Markdown 的图片链接已替换为同目录文件。内部固定记录用于恢复，外部副本移走或删除不损坏历史，手改文件不静默覆盖。

不支持的数学或 Word 结构明确失败，保留旧修订，不静默丢项。本版未实现完整人工 Word 稿导回、第二台实体 Windows 验收或 PPT 生成格式包，不能用本地样稿冒充完成。

## 现场记录表与真实页数

record_sheet 是可独立取用的现场记录表，可用 parent_document 固定回指原完整文稿。原 plan 及四类附录保留；派生现场表不冒充短预习，不强塞完整讲义结构。summary 可为空，现场未知与来源仍须可读。print.json 提供记录表主题，table_padding_horizontal_pt 与 table_padding_vertical_pt 分别控制横向留白与手写行空间，sources_layout=compact 可合并同一原件的出处。

实际 PDF 页数写入 pagination.json，并通过保存回执 layout.pdf_pages 返回。用户要求单页时据实际页数继续调整或说明未达到，不能凭标题宣称。Word 页数必须单独打开检查，不能用 PDF 页数代替。生成器兼容中文标点相邻的粗体，保护代码及转义星号；中文行距按实际字形边界计算，避免紧凑排版时重叠。

Word 显式关闭默认文档网格，按主题设置安全最小行距和标题间距；heading_space_before_pt、heading_space_after_pt、title_space_after_pt 可覆盖。公开文件以文稿自然标题命名，源稿单独分类。旧1.0.4与1.1.0包均随应用保留，原资产不改写。
