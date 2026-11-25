# 实验工作底稿

先选清主线，再从原件留下写作和计算要用的锚。title、purpose、source_strategy 说明这次要解决什么和各材料所长。stages 依次覆盖 prestudy、procedure、recording、analysis；每项有 key、title、scope（overview/developed）及 points。point 有 id、title、knowledge（kind 为 definition/fact/formula/procedure，附 text）、material_plan、interpretations（question 与 emphasis）；可用 depends_on 引用前面要点。草稿只写写作决策，正文再展开。

先按读者使用时机在 deliverables 选交付物。课前预习 prestudy 在预习阶段发展物理问题、必要概念、为什么测和大致实验路线。深入预习展开推理。台边实验册 plan 在实施和记录阶段发展现场操作、观测判据、换接条件与记录安排；单独的记录纸是 record_sheet。共同的工作底稿覆盖四个阶段，成品只展开本次选定用途。只要其中一份就只交这一份。用户明确一册兼两用时按原话分开课前阅读和台边查用；分别要两份时分开编排。

要给现场测量或后续计算交接数据时，把要用的读数登记在 observations。每项给 id、quantity、unit、setup、criterion 和可选 sources。branches 每项给 id、condition、observables（在该状态真实可读的观测 id）、action（如何处理、能得什么结论或换哪种方法）。单纯课前理解可用定性现象及大致步骤交代实验思路。不同量同单位仍是两个观测。

有结构化观测时，给每个观测及分支一个 setup_id 表示取得该读数的实际测量配置；同配置共用标识。分支的 observables 只列这里实际读到的量。若计算还要用上一配置中独立取得的量，列入 prior_observations 并说明来源。显示符号可以沿用，观测标识要能区分不同的物理判据。

例如在光电门实验，一次遮光时长由挡光片前后缘通过同一探头取得，门间飞行时间由同一前缘先后到达两只探头取得。前者配挡光片宽度求局部速度；第二只门未触发时，仍可求局部速度，门间平均速度须另取两门时间。另一次测得的挡光片宽度可作为先前观测带入计算。具体装置的接线和示值应回到自己的材料。

在 deliverables 列出本次需实际生成的文稿/分析类型（prestudy、plan、record_sheet、program、analysis_result、workbook）。深浅和 Word/PDF 版式要求用 delivery_requests 原意记下。可在阶段及要点的 sources 保留选材出处。只调整某个观测、分支或交付要求时，用 edit_artifact 的 JSON Pointer 更新该处，程序按原基线接续，保留其它读数和全部未改段落。旧任务做过的输出不自动成为这次待办。
为数据处理建立 measurement_methods 时，先查原件的数据处理要求，按方法登记必交的定量和定性结果。连读推导、装置图和操作页，把理论符号落在具体元件或样品、两端、方向、状态、测量时刻与实际单位上。保存选页标签 material_pages、宿主提供的页标签到真实出处映射 source_index。模型不得自造锚点。

每个 targets 条目写 id、label、unit、meaning、source 和 kind（computed 或 observation），observables 写稳定 id、label、unit、form、meaning 和 source。form 区分单值、同次采样表列、有出处的给定参数或定性观察，meaning 说明实际读哪一端、哪个通道、何时取数。equations 保留课程原式、源图或式号、符号对应、适用条件、方向、选区及分支。方法从实测量逐步到达必交量，请写出中间关系。需要对同一笔读数配对时保留同表同次记录；替代接法分别定义，不把额定值填作实测。页内计算结果沿用 targets 标识。学生变更记录行数等局部操作时保留方法和原任务说明。

