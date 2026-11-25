# 实验工作底稿

先选清主线，再从原件留下写作和计算要用的锚。title、purpose、source_strategy 说明这次要解决什么和各材料所长。stages 依次覆盖 prestudy、procedure、recording、analysis；每项有 key、title、scope（overview/developed）及 points。point 有 id、title、knowledge（kind 为 definition/fact/formula/procedure，附 text）、material_plan、interpretations（question 与 emphasis）；可用 depends_on 引用前面要点。草稿只写写作决策，正文再展开。

有不同装置、判据或适用分支的测量，把要传给计算的读数登记在 observations。每项给 id、quantity、unit、setup、criterion 和可选 sources。branches 每项给 id、condition、observables（在该状态真实可读的观测 id）、action（如何处理、能得什么结论或换哪种方法）。不需做定量分析的任务可省略。某个方法没有读数时，给学生另一路可执行的操作或直接说明这次能报告的量。不同量同单位仍是两个观测。

有结构化观测时，给每个观测及分支一个 setup_id 表示取得该读数的实际测量配置；同配置共用标识。分支的 observables 只列这里实际读到的量。若计算还要用上一配置中独立取得的量，列入 prior_observations 并说明来源。显示符号可以沿用，观测标识要能区分不同的物理判据。

例如在光电门实验，一次遮光时长由挡光片前后缘通过同一探头取得，门间飞行时间由同一前缘先后到达两只探头取得。前者配挡光片宽度求局部速度；第二只门未触发时，仍可求局部速度，门间平均速度须另取两门时间。另一次测得的挡光片宽度可作为先前观测带入计算。具体装置的接线和示值应回到自己的材料。

在 deliverables 列出本次需实际生成的文稿/分析类型（prestudy、plan、record_sheet、program、analysis_result、workbook）。深浅和 Word/PDF 版式要求用 delivery_requests 原意记下。可在阶段及要点的 sources 保留选材出处。只调整某个观测、分支或交付要求时，用 edit_artifact 的 JSON Pointer 更新该处，程序按原基线接续，保留其它读数和全部未改段落。旧任务做过的输出不自动成为这次待办。
