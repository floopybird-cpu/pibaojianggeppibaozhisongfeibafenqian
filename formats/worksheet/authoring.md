# 编制单个实验数据页

调用 submit_measurement_page，参数为这一页的 fields、tables、results、plots 和 sources。页面名称、标识和顺序由已保存的测量草稿确定。输入原件用作证据。围绕本方法的 targets、observables 和 equations 编制可填写、可计算的定义，同时阅读所附完整原页及公式图。

计算目标的 components 非空时，为其中每个分量建立同 id 的结果，并沿用各自单位、含义和来源。组合目标的名称用于组织这些结果。components 为空时，结果沿用目标自己的 id。显示单位与表达式共用现有 SI 约定，例如光学透过率由透射强度除以入射强度得到，表达式为 I_transmitted/I_incident，显示单位为 % 时由程序换成百分数。

## 多组读数的录入方式

每套重复数据在 tables 中声明 entry_mode。学生逐次测量、改变条件、做平行测定时选 groups。共同参数提到步骤上方；每组并列放其条件、所测读数和由该组直接得到的 derived 量。相同条件下有固定的成对观测时，优先采用一组一条记录，每个读数有具名输入；若底稿按分支逐行计算，grouping 用 condition、variant 列标识和实际 variants 合并展示。保留分支各自的测量与计算语义。rows 表示底层记录条数；如每组有两条分支，三组对应六条记录。固定顺序不等于已取得读数。学生可增减组数。
仪器自动采集的时序、同步多通道波形等选 samples，使用可批量粘贴的采样表。手记几次用时仍按组录入。CSV 是文件载体，两类读数都可以导入。曲线或拟合也可用于离散测量组。

## 物理关系与课堂目标

computed 目标沿用草稿 id 和实际单位，连接到本次读数。每个课程必交结果标 role="primary"，中间量标 "intermediate"，有独立测量路径的核对量标 "check"。observation 目标由宿主建立文字记录栏，tables 用于其它确有用途的读数表。

原式、系数、符号和读数时机依草稿与操作原页逐项核实。结果的 note 简述物理含义、适用条件和如何使用它，界面直接从 expr 显示计算公式。举例，光学相位与时间差的关系沿用原式的角频率定义，说明文本中的普通频率与角频率也保持对应。物理事件时间与学生拟合所选区间分别使用各自的观测值。

主要结果用 basis={page:本次原页标签,locator:原式号或小节名,quote:逐字相邻原文} 标出依据。图像公式从原页图读取，按式号或标题定位。推导所得关系在 note 简述从哪条原式得到、用了哪些条件。关键的几何路径、测量端点、方向和操作状态需同时得到装置图与步骤支持。

两条替代测法各自接到最终结果，可选 choose(selector==1,计算A,计算B)，仅求选中分支；或 coalesce(分支甲,分支乙)，按顺序选首个读数齐全的同量纲分支并注明优先次序。保留实际有用的独立核对，现场数据按真实记录填写。

## 输入与单位

每个字段用稳定的ASCII标识，label 给可理解的量名。fields 为单值 {id,label,unit,hint?,symbol?,reference?}。hint 简述位置、时机或操作选择。tables 包含 id、title、rows、columns，每列 {id,label,unit,hint?,type?,symbol?,source_headers?}。实际测量值留空，行数可增减。文字记录的 unit 为“文字”，其余使用标准单位符号，无量纲用1。source_headers 仅填写原件或已确认数据真实存在的通道名和列名。

观测已注明 recorded_unit、formula_unit 及 host_input_scale/offset。宿主把录入的mm、g、mL、mV、℃等换成SI量值后执行expr，计算结果再换到所标单位。绝对摄氏温度有273.15零点差，温差用K。expr按formula_unit直接计算。derived_from所指的平均、差值或拟合量给计算表达式，其原始输入各自保留。

同一实际量只建立一个输入或计算身份。不同表的列标识各自唯一，可共享显示量名。仪器常数、校准值可用 reference={value:原数值,page:"D2p3",quote:"包含数值、物理量或单位的逐字原文"}，宿主核对后显示可修改的参考值。式中有单位的给定常数用 quantity(数值,"单位") 或带来源的字段。

## 执行表达式与图

results 为标量结果数组 {id,label,unit,expr,note?,digits?,uncertainty?,u_digits?,domain?,role?,basis?,symbol?,table?}，按依赖顺序排列。支持 + - * / **（或^）、pi、e，以及 sqrt、abs、exp、log、log10、sin、cos、tan、hypot、atan、atan2。单位和标量/向量形状由宿主核对。

表列为向量，算术逐行进行。同表 derived:[{id,label,unit,expr,symbol?}] 保存逐点变换，可引用本表输入、先前派生列及标量参数。计算和图可引用它。mean、std、sem、min、max、sum、count、first、last 返回标量，std为样本标准差，sem为均值标准不确定度。同表同行列才作逐点配对。

slope(x,y[,sigma])、intercept、r2、slope_se、intercept_se 使用同一线性模型，sigma是有依据的因变量标准差。integral(x,y) 为梯形积分；sample_period(时间列) 求实测采样间隔。diffslope(x,y,lag) 对相隔lag组的读数逐差再平均斜率。diff(column)取相邻差，lag(column,1)保留行数、首行缺失。x_at_max(x,y)找峰值对应横坐标，rms(y)求有效值。课程给定采样和积分规则时按其实际规则编写，说明与其它积分方法的端点权重差异。

plots 每项 {title,table,x,ys:[列id],fit:"none"|"linear",sigma?}。曲线指向实际所画的物理量，功率等先派生再绘图。只承诺已由本定义提供的标线、拟合和坐标变换。单摆多长度法须明确周期数及周期平方与长度的拟合关系，然后由斜率得到目标量。

若按时间、温度、频率筛选，table.selection={column:实际列,min:下界标量id,max:上界标量id}，单边可省另一边。界限可用只依赖可填参数的标量结果，以图中单位显示。宿主统一把选区用于该表的计算和拟合，原始点继续保留。选择依据和仪器时间零点分别解释，必要界限先留空供学生选取。普通重复几何测量按实际行录入。

## 解释与不确定度

domain可选signed、positive、nonnegative、fraction，按量的定义使用。核对原接线、原式、方向和一次正常趋势下的正负意义。例如阻尼振幅对数斜率为负，衰减时间常数为正。现场通道方向可变时给明确可确认的配置及相应公式。

不确定度沿物理来源传播，区分标准差与均值标准不确定度，相关量按其关系处理。digits遵循课程有效位数，u_digits省略时沿用课程约定，也可填1、2或3。未知的仪器精度保持待填，并明确影响哪个不确定度；有充分读数的中心值可先计算。每条说明紧贴对应字段、公式和使用条件。

草稿 equations[].calculations 已确定的表达式由程序直接用于页面。按原 id 设置所需字段和表列（包括中间量），图线、原始列与计算共用这些 id。为每个曲线指定它所在的读数表和横纵坐标。需要区分符号或实验条件时，沿用草稿对对象及正方向的说明。讲义给出的操作和定性目标均保留记录位置。

用 entry_steps 按实际操作顺序排每步的 fields、tables 和 results，各写已有 id。一行可以同时放多个读数和多个可核对的中间量；同一次测量的输入与当场能算出的结果排在一起。曲线随所属表出现。比如光学台同一行放初、末刻度与位移；多次读数的逐组结果放进该表 derived 列。reading_hint 简短交代读哪件仪器、哪段记录或哪一栏，材料未交代时使用中性的记录提示。
