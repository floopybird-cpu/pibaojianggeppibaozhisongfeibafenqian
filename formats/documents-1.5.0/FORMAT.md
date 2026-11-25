实验工作草稿与文稿分开。生成任务先保存ExperimentDraft，按底稿组织知识、选材和诠释，再展开本稿；宿主记录draft_ref和draft_points，原生阅读和可取走副本可查固定草稿。写作时正常整理，独立审计不驱动同轮修稿求pass。

# 学习文档与实验单包

当前为1.5.0，数据9、接口7。默认整图依原 PDF 的嵌图边界补回被裁掉的图缘，原始锚点保留；局部特写用 framing=exact。台边目标可前指正文同式，独用的目标算式不占正文式号。Word/PDF 共用这些规则。完整台边册包括记录附录，独立取走的一张或一组表才选 record_sheet。genres.json 定义按读者用途选取的写作指南，在草稿交接和文稿工具中共用。课前的简要或深入预习都解释物理问题、测量理由与概略流程；实验册展开现场判断、操作和记录。Word/PDF共用的数学、插图和出处排印也覆盖结构化目标与步骤。请以本包 authoring.md 为当前写作语法。条件表在“这一情形能读什么”显示当前配置和读数判据，在处理栏列出要沿用的先前测量，Word、PDF、原生阅读和配套 CSV 使用同一份固定底稿。来源校验会一次返回各个错误出处在文稿中的位置。以下保留历版能力说明。

发布版本 1.2.8，数据版本 4，宿主接口 2。科学内容模型与版式分离，新增 Markdown 和自由布局。旧数据是新模型的子集；1.0.4 原包在 documents-legacy 保留，固定旧资产不改写。升级预览基线后生成新修订，不替旧稿编造事实。接口2增加定性目标渲染；新宿主兼容旧接口1和旧数据1/2/3，旧宿主明确拒绝本包而不生成错误文件。

## 内容与布局

学生说用途、深浅和排版偏好，Agent 负责按本包编排，不让学生填写 JSON 或模板。预习文稿统一为 prestudy：篇幅、学习背景、解释与推导深度由用户自然语言要求决定，同一文稿可简明也可深入，不再按深浅分成两类格式。数学成立条件和推导在任务需要时完整提供，章节名和安排自由；实验单仍有可分别定位的 records、errors、instruments、reading 附录，但位置、标题、分段和页数不固定。

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

record_sheet 是可独立取用的现场记录表，可用 parent_document 固定回指原完整文稿。原 plan 及四类附录保留；派生现场表不冒充预习文稿，不强塞完整讲义结构。summary 可为空，现场未知与来源仍须可读。print.json 提供记录表主题，table_padding_horizontal_pt 与 table_padding_vertical_pt 分别控制横向留白与手写行空间，sources_layout=compact 可合并同一原件的出处。

实际 PDF 页数写入 pagination.json，并通过保存回执 layout.pdf_pages 返回。用户要求单页时据实际页数继续调整或说明未达到，不能凭标题宣称。Word 页数必须单独打开检查，不能用 PDF 页数代替。生成器兼容中文标点相邻的粗体，保护代码及转义星号；中文行距按实际字形边界计算，避免紧凑排版时重叠。

Word 显式关闭默认文档网格，按主题设置安全最小行距和标题间距；heading_space_before_pt、heading_space_after_pt、title_space_after_pt 可覆盖。公开文件以文稿自然标题命名，源稿单独分类。旧1.0.4与1.1.0包均随应用保留，原资产不改写。

## 合并与打印约定

prestudy 是统一预习文稿类型，plan 是实验单，record_sheet 是从完整实验单派生的现场记录表。新增文稿不再使用 short/deep 类型。旧版按固定包正常阅读；显式升级时只转换类型标签并保留原主题、全部章节、公式、图表、来源与旧修订，不重新研究或缩短内容。PDF 是固定打印件；Word 保持可编辑，不把两种引擎的页数相互冒充。

公式中的中文注释使用当前 Windows 已安装字体补足矢量字形；临时字体仅留在本机临时缓存，不进入格式包、发行包或文稿源资产。Word 原生公式保持不变。

中文数学字形的水平度量在固定 ziafont 后端中保持完整，避免连续汉字出现异常字距。

## 内容质量与出处呈现

条件在相应模型或步骤就近说明，现场待测量写入操作与记录栏；warnings仅供全局使用说明，默认位于主文后，不再把所有说明冠为前置待确认。原件笔误和内部核验轨迹不作为学生必答清单。

正文按原件统一编号、就近标页，同一原件的整页和裁图锚点合并显示；sources.json仍逐锚完整保存，裁框、定位、摘要和核验状态不丢失。原件引用不能冒充对自设数值或拓展结论的支持。

生成Agent可用实际裁后图与inspect_document核对生成页，calculate_values复算正文算例；结构校验通过不自动代表科学、讲解或排版合格。不限定章节、页数或正文长度。旧1.2.2包随应用保留。

## 直接出处、推导与示例

全文仍须有真实原件来源。正文/附录的段落、Markdown、公式、表格等可以sources:[]，但须用evidence指明derivation（由已说明关系推导）、example（自设示例）、explanation（自己的桥接解释）；依据、推导与示例身份在就近正文讲清，不增加机械的审计段落。evidence=source的直接来源块仍须有真实锚。图像继续要求实际来源锚列入sources。

不为满足逐块非空来源强制给假设值、独立推导或排版说明挂一个不支持它们的教材页；来源只支持它真正给出的命题。公式有固定知识引用时依旧严格匹配，必要课程来源和科学条件不删。旧1.2.3与1.2.2包原样保留。

## 未设时限

background.minutes是用户明确给出的可用分钟数；没有给出时可省略，不强迫生成器填一个假时限。自行给阅读时间建议可在就近正文说明是建议，不能冒充用户条件。省略仅影响背景提示，不删除内容、改变深浅或施加正文配额。旧版本原包保留。

## 1.2.6：计划的必要信息与简洁呈现

每项定量目标保留量、单位、时机、条件、仪器和来源；precision可在有依据或本次确需时填写，不提供即未记录，不能由小数位补造精度。当前用途不需要它时不强塞一列“待确认”；需要不确定度时在仪器附录或现场栏记录实际分度/精度及来处，不能因省略而声称完整不确定度已知。

新主题默认concise_plan:true：目标显示文内编号供反查，步骤用短编号关联目标，完整保留理由、条件和完成判据；仪器附录入口按流程集中一次，不在每一步复制同一链接和目标全名。设false可用原详细呈现。四项附录和科学链仍保留，作者可用goal_summary/procedure嵌入主文适当位置，无需再手写同一组表。不要在主文、目标表、记录表、误差表反复复述同一定义；有用途的教学重复仍可保留。旧1.2.5和所有固定旧资产保持原样。

## 1.2.7：可读的公式标题与目标编号

文稿标题、章节标题、步骤和图表题注与正文使用同一数学渲染规则，允许行内公式，不把 TeX 源码当作可读公式交付。目标的 id 仅用于保存与引用；文稿和应用阅读界面显示按顺序编排的“目标1”等短编号，步骤关联使用同一编号，原始关联不改。量和读数的单位显示可读符号与上标（例如 °C、Ω、m²）；内部单位字符串保持原样供计算与追溯。旧1.2.6包及固定资产保留。

## 1.2.8：定性目标与定量目标

求量目标保持quantity/unit/model/formula_ref/measurements/conditions/sources，kind可省略或为quantitative，原有读数和公式链不减少。定性目标用kind:qualitative、id、objective、conditions、observation_plan、sources，可补model解释；无需编一个单位、公式引用或额外读数。observation_plan按真实需要写何时、观察什么、怎样记录，支持正文数学与Markdown，不强排六列表。

两类目标可同单混排，步骤仍按真实id覆盖，阅读显示同一目标编号；定性演示中的说明关系不自动升级为测参数任务。实际要求计算的目标不能改标定性来避开公式/单位/未知输入。旧1.2.7原包保留，数据3旧内容可无损迁入数据4并生成新修订；旧资产不重写，升级不自动把旧目标改类或删除既有读数。


## 1.3.1：按实际基线增加手写空间

已有表格“空格高一点”是相对当前成品的要求。edit_artifact或apply_artifact_edits可用{"op":"increase_writing_space","path":"/style","value":3}，表示每行增加3毫米。宿主读取固定包的实际主题与现有覆盖值，等量增加上下单元格留白；保留正文、字形、横向留白和来源。value为有限正数，可按用户需要选择；没有总行高或页数上限。它调整该文稿全部表格；只改指定表格的复杂版式应先说明目标并用现有结构编辑。

将同一次版式任务的必要更改合在一次编辑中。先读取现有成品，不臆测旧样式数值；除非用户明确要求固定页数，增加手写空间允许页数自然增加，不同时缩字/缩行/压边距抵消。不必为纯交付要求重做实验知识、材料或诠释。升级保留旧版和全部内容，旧1.3.0包不改。


## 固定测量记录表块（1.3.2，数据6/接口4）

实际读数不要从笔记凭记忆再抄一张普通表。已有规范记录时，用measurement_table：workbook_ref给DataWorkbook固定id/revision，record_ids可选实际行，fields可选quantity/values/unit/section/note/status（默认量名、原数字串、单位），sources给该原件真实锚点，可自由指定headers表头、caption与column_weights。生成器直接取固定原数字串和状态，Word/PDF同源展开；不经浮点化、不造空单位、不改变分组。正文解释和推导仍自由组织，教学假设值用普通table并写明example；引用记录不是证明所有解释正确。

例如电路记录0.80、0.85V已经整理，预习只选电压行的workbook_ref与record_ids，排版可以窄列或通栏，不手抄成0.8、0.86。后来有真实更正则先接续规范表并明确使用新版，旧文稿继续固定旧版可恢复。外部authored-composition.json保留引用写法，measurement-sources.json保留用到的固定规范记录，composition.json是Word/PDF的实际展开；无任意代码、路径或联网能力。旧1.3.1原样保留。
