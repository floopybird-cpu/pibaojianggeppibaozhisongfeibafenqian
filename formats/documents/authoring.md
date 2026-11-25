# 背景阅读与实验单的写作接口

背景阅读遵循 background_reading.md，以明确选定的一个实验或一组实验为范围，从教材原页选材讲清物理概念。实验册按动作、观测、判断与记录展开。每段推进一个问题，图、数学与出处就近排印。

提交 content 的 title、doc_type（prestudy/plan/record_sheet）、summary、background（level、goal，可选minutes）、sections、appendices、knowledge_refs、warnings、simulation。sources 可给空数组，程序从全稿准确汇总。每节有 id、title、blocks；heading_level 为1、2或3。一级对应物理问题或阶段，二级展开下属概念、理由或具体操作；用自然标题，不在标题里复写层级编号。篇幅短可只有一级。开篇写读者需要做和理解的事，summary 和首段各有用途。按学生目的自由选择段落、图、表、分栏和推导。无全局事项时 warnings 用空数组。字号、页边距、行距和缩进默认由主题统一配置；只有读者明确的排版要求或特定栏宽需要才用 style 覆盖。

- paragraph 或 markdown 有 text、sources。Markdown 支持常规标题、列表、表格、强调、数学。数学符号、上下标、比值、物理单位在正文、标题、图注和表格统一使用 $...$；代码或文件名用反引号。数值与单位指标清楚，不在句子之间穿插裸 LaTeX。正文给物理解释，式子给关系。
- equation 有 latex、sources，可选 symbols（首次出现时用完整短句定义）、label（本稿编号）。自动编号，原教材式号请在正文另指明。符号已经解释过可以省略，数字上标脚注和引文由引擎排。
- figure 有 source_anchor、caption、sources，可选width_cm。整图用默认 framing=auto，格式包在可识别的原页嵌图上补齐边缘、避开重复总题注；需要局部放大或精确的手选框时用 framing=exact。矢量图、扫描页和有独立箭头标注的图也要对照原页，确认连线、坐标轴及端点完整。图示的物理关系或台边动作，紧邻首次使用位置讲。可用 columns 自由并排图和短说明：columns 为 [{blocks:[...]}]，可选 weights。表用 caption、columns（字符串）、rows、sources 和可选column_weights。
- 来源是内部证据。直接引述、装置参数、操作判据和图片绑定真实锚点。推导、假设示例或解释用 evidence=derivation/example/explanation，可用 sources:[]。citation 默认 auto，连续叙述共享一处可查标注。关键分歧或指定原文位置可用 nearby，背景出处可用 bibliography。全文和定位保留在原件清单。
- 需要数值算例时用 worked_example。caption、branch（底稿中该状态的 id）、sources、inputs、calculations。每个输入给 key（计算英文变量）、observation（该分支可读的量 id）、value（按底稿原单位）、origin（hypothetical 或 reference，后者另给含数值的原文引文 source_anchor）。每个计算项给 key、quantity、method（给学生的代入/换算关系，可用行内数学）、expression（可执行四则、**、sqrt 等，变量取前面项）、unit，可选 significant_digits。结果由程序算。光电门单门遮挡时长与两门间隔不能串用；材料只给示意值时，讲清物理路径即可，勿补造本次实测。符号推导无需编数。现场真实记录用 measurement_table 固定规范表版本和 record_ids。
- 如果不同状态下能读的量不同，用 branch_table（caption、branches 为底稿分支id数组、sources）。程序按各分支生成判据、可读量和处理动作。接下来的公式和记录表只要求该条件实际可得的输入。
- 条件表会带出本步骤的实际读数位置与判据。计算若沿用上一步的独立测量，请在草稿的 prior_observations 指向它，导出的处理栏会保留原测量配置和单位。
- 预习没有固定章节数。实验册 plan 用 goals 和 steps 组织现场任务，附录 id 为 records、errors、instruments、reading。定量 goal 的 formula_ref 用知识库中真实 Formula 的固定 id 和 revision；查无该式时，可按原件用知识工具保存公式。定性观察用 qualitative 目标。read_format_contract 可用 block_type=goal 或 step 单查这些字段。正文用 procedure 或 goal_summary 块时宿主在该处排出步骤或目标；同一内容只编排一次。
- 初次学习时根据知识背景解释原理。台边册才把原理落实到图中的部件、端子、实际示值与可填写的位置。图上无端口标号时依据原材料给出的可观察判据定位。引用某节、某表或某数量前对照成品结构。
- 最后一份用户要的文稿通过 submit_document 提交整稿及 final_message，宿主一次建立正式修订、核对 Word/PDF 并发布。若还有别的任务先继续完成。候选稿若报字段错误，按返回的 candidate 只传修改项。需人读版面或用户要求打开时仍可检查、取用或再改，正常生成完成即交付。
