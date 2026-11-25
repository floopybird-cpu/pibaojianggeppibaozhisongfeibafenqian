# 原始测量记录整理格式

`physics.data` 1.1.0，数据版本 1，运行接口 1。内部类型为全局 `DataWorkbook`，可整理一份混合记录中的多个原文实验分组；不绑定一次实验，不跨实验计算结果。

## 学生看到的文件

生成 `data.xlsx` 和 `normalized.json`。Excel 每个实验分组使用一张长表，另有“原始记录”、“待确认”和“原文观察”；不把每一个量拆成单独工作表。“原始读数字符串”保留 `5.40` 等原精度；相邻“数值（原单位）”为真正数字，可用于 Excel。超过 Excel 的 15 位有效数字或数值范围时，数字格留空并注明原因，原字符串仍完整保留。量名与单位缺失时显示待确认。分组测量表不混入第二套表头或说明行，数值列保持统一数据类型。原文掉探头、T1 为室温等内容在“原文观察”页按原句列出；整理不会自动删除任何读数，也不会自动把观察当成已经完成的数据剔除。

内部保存正式修订及其完整文件，外部可阅读产物是独立副本。拿走外部 Excel 不会删除内部记录。

## 内容字段与出处

- `title`：自然标题。
- `source_id/source_sha/original_lines[{number,text}]`：由宿主从原字节全文填充，模型提供的这些字段不能替换原件。
- `records[]`：`id, section, quantity, values, unit, source_line, source_quote, status, note`。`values` 是对应原句中按原精度保留的数字字符串；不得把 `5.40` 改成 `5.4`，不得将档位数当成读数而不说明。`source_quote` 必须出自 `source_line` 指定的真实行。
- `observations[]`：`section, text, source_line`，`text` 为原文观察的原句。
- `issues[]`：`id, question, record_ids, field, status, answer, confirmation_question_id`。问题与涉及行的身份固定；新问题另建 ID。缺项要提问，不能用课程常见值填造。
- `history[]`：宿主保存答复、原问题 ID、真实提问 ID、时间、修改前后字段及确认是否核验；模型不能覆盖。
- `sources[]`：宿主自动建立完整原文和逐条记录锚点。模型可不填。

`recorded` 只用于本句明确给出物理量与单位的读数；`pending` 保留缺项；`confirmed` 必须经过真实答复的对应问题。原文“铜盘 10.00 …”不能直接声明为铜盘厚度及 mm；下一行只有数字也不能仅凭习惯视为上一行的继续。“温度16.5度”保留“度”的原句，不能自行补成摄氏度；上、下盘温度不能互换。可将相关记录留在待确认分组，并向用户问清。

## 宿主服务

`DataWorkbooks(store,gateway,materials)` 暴露：

```python
submit(source_id, content, object_id=None, base_revision=0)
read(id, revision=None)
search(query='')
resolve_issue(object_id, base_revision, issue_id, answer, updates, question_id=None)
```

`updates` 为 `[{record_id, quantity?, unit?, section?, status?, note?}]`，仅修改原问题 `record_ids` 内的记录，不接受原始数值、原文或来源字段。确认更新通过宿主 `answer_lookup(question_id)` 核验已提交答复，其 `targets` 必须包含此成果和此问题。兼容单个 `target_object_id/issue_id` 回执。`answer` 保留真实原文或完整子句。

未记录、不知道等答复保留 `unknown` 问题与 `pending` 记录，不补造单位；有明确答复的部分可通过另一个具体问题继续处理。无真实答复不能设置 `confirmed`。普通重新提交不能把待确认改为已确认，不能改已确认解释，不能改写答复历史。

## 格式程序与扩展

`apply` 经 schema、全局范围及 `workbook` 纯校验；`build` 只调用有限输入的 `workbook` 指令；`finish` 要求两个实际文件。原始字节验证在宿主服务，完整原文与冻结锚点比较也在生成时执行。通用编辑入口应路由到本服务，不能直接伪造确认历史。

普通阅读不依赖执行模型或 Excel。更改格式时发布新版本；旧成果固定原包，升级预览后保留内容生成新修订。包不执行任意脚本、网络或数据库操作。

## 原文结构与上下文推断

整理前用inspect_measurement_notes取得完整原文行和宿主给出的独立成行文本线索。这些线索不是自动确定的实验分类，应结合上下文核对；明确的实验标题不能当成前一实验的普通观察。先恢复原文分组，再转录数值。学生说“不记得/未记录”表示不能亲自确认，不抹掉原文已经写出的标题。提问针对仍影响当前整理的歧义，不能让学生重新提供笔记中已有线索。

新增inferred状态，表示按可指出的原文上下文推断，非用户确认。需note解释依据、evidence_lines列真实原文行号；数值/尾零仍逐一匹配原句，不由推断填读数。物理量或单位有推断、或量名附加了原句没写的“加热前”等时机，都不笼统标recorded。recorded不覆盖额外解释，confirmed仍必须有真实对应答复。没有足够依据仍pending，不能为了不提问而猜。

新包Excel额外展示每行的“原文分段线索”供回读；它是原文最近独立文本，不替代归类判断，也不假装个人确认。保存回执附分组核对线索，正文/最终回复按原文、推断、真实用户确认、正式文件已核验分别表述，不把笔记读数叫作已核验正式数据。1.0.1原包及旧资产保留。
