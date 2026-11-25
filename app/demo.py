"""Self-authored simulated teaching fixtures. Never a real course experiment."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import time


PAGES = [
    ("弹簧与线性模型 教学模拟材料", [
        "本材料为软件开发者自编的公开教学示例，允许随软件分发。它不描述本课具体仪器，也不包含真实测量。",
        "弹性范围内，弹簧力的大小 F 与相对自然长度的伸长量 x 成正比。用大小表示：F = k x，其中 k > 0 为劲度系数。矢量恢复力方向与位移相反，符号不能混用。",
        "条件：小形变、未越过弹性限度、静态平衡；用同一弹簧、固定几何装置和温度。若讨论增量，则使用同一参考状态的力增量与位移增量。",
        "可观测量为力 F（单位 N）和伸长 x（单位 m），k 的单位为 N/m，维度为 M T^(-2)，三者均为标量大小。传感器通道、量程和最小分度在真实实验前由学生确认，本材料不提供仪器精度。",
        "第一种教学约定：零点已经独立校准且偏移可忽略，可用 F = k x。不能仅为得到理想直线而强制消除截距。",
        "读数前确认零点及参考长度；分级改变载荷，每次达到稳定后成对记录 F 与 x。卸载后检查是否回到参考状态；迟滞或永久变形要保留原始观察，不能删掉使曲线更理想。",
        "图示的横坐标是 x，纵坐标是 F。直线斜率具有 N/m 的单位；是否允许过原点需要先检验零点与模型条件。",
    ]),
    ("最小二乘与实验计划 教学模拟材料", [
        "当自变量 x 的误差可忽略、力的误差近似独立且等方差时，以 F 为因变量的普通最小二乘模型为 F_i = k x_i + b + e_i。至少两个不同 x 才能求斜率；评估残差需额外数据。",
        "数学基础：最小化残差平方和 S = sum((F_i - k*x_i - b)^2)。令对 k 与 b 的偏导为零，得到两条正规方程，消去 b。",
        "斜率 k = sum((x_i-x_mean)*(F_i-F_mean)) / sum((x_i-x_mean)^2)。截距 b = F_mean - k*x_mean。分母不能为零。数学量 x_i 映射到伸长 m，y_i 映射到力 N，斜率映射到劲度系数 N/m。",
        "为测 k，必需成对记录静态平衡时的伸长与力，同时记录参考长度、参考力、通道、单位、时机、仪器精度和迟滞现象。目标量不能直接替代原始读数。",
        "顺序：确认装置与量程，再确认参考状态；分级采样，保持弹性条件；核对单位和残差，再决定可解释区间。完成判据是每组读数都有成对量、单位与状态说明，且零点／卸载检查已经记录。",
        "误差检查包括长度和力的零点偏移、分度／校准不确定度、未达平衡、超出弹性范围、迟滞和拟合模型失效。没有仪器精度不能报告完整不确定度。",
        "仪器附录应填写本课实际传感器的开机、清零、读数和量程步骤；本材料没有这些按钮细节，保留待确认。记录表应分别列原始观察、模型解释和推断。",
        "拓展阅读应回到教材中静力平衡、弹性条件和最小二乘的对应段落；网络补充注明网址及访问时间，不覆盖教师的具体规范。",
    ]),
]


def create_examples(directory: Path):
    import pymupdf
    directory.mkdir(parents=True,exist_ok=True)
    first=directory/"模拟教材A_弹簧与最小二乘.pdf"
    if not first.exists():
        doc=pymupdf.open()
        for title, paragraphs in PAGES:
            page=doc.new_page(width=595,height=842)
            page.insert_textbox(pymupdf.Rect(42,40,555,85),title,fontsize=18,fontname="china-s",color=(.1,.22,.25))
            result=page.insert_textbox(pymupdf.Rect(42,105,552,790),"\n\n".join(paragraphs),fontsize=11,fontname="china-s",lineheight=1.55)
            if result<0:
                raise ValueError("示例教材页面文字溢出")
        doc.set_metadata({"title":"教学模拟 弹簧与最小二乘","author":"Physics Harness contributors","subject":"Self-authored simulated teaching source; redistribution permitted"})
        doc.save(first)
    second=directory/"模拟教材B_零点约定差异.pdf"
    if not second.exists():
        doc=pymupdf.open(); page=doc.new_page(width=595,height=842)
        page.insert_textbox(pymupdf.Rect(42,40,550,95),"零点约定差异 教学模拟补充",fontsize=18,fontname="china-s")
        page.insert_textbox(pymupdf.Rect(42,110,550,760),"\n\n".join([
            "开发者自编教学模拟材料，允许随软件分发；不是真实课程仪器规范。",
            "弹性范围内，弹簧力的大小 F 与相对自然长度的伸长量 x 成正比。",
            "第二种教学约定：本补充讨论未经独立清零的力传感器。保留截距 b，使用 F = k x + b，不允许未经检验就强制过原点。小形变、静态平衡、未越过弹性限度仍然需要满足。",
            "教材 A 的过原点约定以零点偏移已经校准为前提；本补充的条件不同。跨来源归并时应保留条件差异并请使用方选择，不将两段话改成没有条件的单一规则。",
            "若迟滞、温漂或长度零点变化，先保存原始观察，再检查模型适用性。本材料也没有仪器精度或真实数据。",
        ]),fontsize=12,fontname="china-s",lineheight=1.6)
        doc.save(second)
    csv=directory/"模拟读数_已知斜率100.csv"
    if not csv.exists():
        csv.write_text("x_m,F_N\n0.01,1\n0.02,2\n0.03,3\n0.04,4\n0.05,5\n",encoding="utf-8-sig")
    return first,second,csv


def wait_import(app, job_id):
    for _ in range(1000):
        job=app.store.one("SELECT * FROM jobs WHERE id=?",(job_id,))
        if job["status"]=="succeeded":
            return json.loads(job["result"])["source_id"]
        if job["status"] in ("failed","cancelled"):
            raise ValueError(job["error"])
        time.sleep(.02)
    raise ValueError("示例导入超时")


DEMO_CODE = '''# Fixed simulated acceptance example; NOT live model-generated code.
import csv, json, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

inputs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
parameters = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
out = Path(sys.argv[3])
rows = inputs["datasets"][0]["rows"]
x = np.array([float(r["x_m"]) for r in rows])
force = np.array([float(r["F_N"]) for r in rows])
chosen = (x >= parameters["xmin"]) & (x <= parameters["xmax"])
x, force = x[chosen], force[chosen]
if len(x) < 3 or np.ptp(x) == 0:
    raise ValueError("Need at least three distinct selected readings")
x_mean, f_mean = x.mean(), force.mean()
denominator = np.sum((x - x_mean)**2)
k = np.sum((x - x_mean) * (force - f_mean)) / denominator
b = f_mean - k*x_mean
predicted = k*x+b
residuals = force-predicted
with (out / "intermediate.csv").open("w",newline="",encoding="utf-8") as stream:
    writer=csv.writer(stream); writer.writerow(["x_m","F_N","prediction_N","residual_N"])
    writer.writerows(zip(x,force,predicted,residuals))
fig, axes = plt.subplots(2,1,figsize=(6,6),layout="constrained")
axes[0].plot(x,force,"o",label="SIMULATED readings")
axes[0].plot(x,predicted,"--",label="F = k x + b")
axes[0].set(xlabel="Extension x / m",ylabel="Force F / N",title="Simulated teaching example")
axes[0].legend()
axes[1].plot(x,residuals,"o-"); axes[1].axhline(0,color="black",linestyle="--")
axes[1].set(xlabel="Extension x / m",ylabel="Residual / N")
fig.savefig(out / "plot.png",dpi=160); plt.close(fig)
result={"k_N_per_m":float(k),"b_N":float(b),"n":len(x),"simulation":True,
        "limitations":["Instrument precision not supplied; no complete uncertainty claim."]}
(out / "result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result))
'''


def ref(obj):
    return {"id":obj["id"],"revision":obj["revision"]}


def install_demo(app):
    marker=app.store.one("SELECT value FROM meta WHERE key='demo_experiment'")
    if marker and app.store.one("SELECT id FROM experiments WHERE id=?",(marker["value"],)):
        return {"experiment_id":marker["value"],"existing":True}
    paths=create_examples(app.directory/"examples")
    source_ids=[]
    for path in paths:
        queued=app.materials.queue(path.name,path.read_bytes(),metadata={"origin":"开发者自编教学模拟","simulation":True,"license":"允许随软件分发"})
        source_ids.append(wait_import(app,queued["id"]))
    a1=app.materials.make_anchor(source_ids[0],1)
    a2=app.materials.make_anchor(source_ids[0],2)
    b1=app.materials.make_anchor(source_ids[1],1)
    physics_sources=[a1["id"]]; math_sources=[a2["id"]]
    category=app.gateway.category("弹性与静态测量","domain-力","力")["id"]
    formula=app.gateway.import_knowledge("Formula",{
        "title":"含零点偏移的弹性线性模型","latex":r"F=kx+b","symbols":[
            {"symbol":"F","quantity":"力的大小","unit":"N","dimension":"M L T^-2","shape":"scalar"},
            {"symbol":"k","quantity":"劲度系数","unit":"N/m","dimension":"M T^-2","shape":"scalar"},
            {"symbol":"x","quantity":"伸长","unit":"m","dimension":"L","shape":"scalar"},
            {"symbol":"b","quantity":"力通道零点偏移","unit":"N","dimension":"M L T^-2","shape":"scalar"}],
        "conditions":["小形变、静态平衡、未越过弹性限度","保留未独立清零的通道偏移"],"approximations":["弹性区间近似线性"],
        "verification":"unverified","sources":[b1["id"]],"domains":["力"],"category_ids":[category]})
    slope=app.gateway.import_knowledge("Formula",{
        "title":"带截距的最小二乘斜率","latex":r"k=\frac{\sum_i(x_i-\bar{x})(F_i-\bar{F})}{\sum_i(x_i-\bar{x})^2}",
        "symbols":[{"symbol":"k","quantity":"回归斜率","unit":"N/m","dimension":"M T^-2","shape":"scalar"},
                   {"symbol":"x_i","quantity":"第 i 次伸长","unit":"m","dimension":"L","shape":"scalar"},
                   {"symbol":"F_i","quantity":"第 i 次力","unit":"N","dimension":"M L T^-2","shape":"scalar"}],
        "conditions":["x 的误差相对可忽略","力的误差近似独立且等方差","x 至少有两个不同值"],"approximations":["线性模型适用"],
        "verification":"unverified","sources":math_sources,"domains":["力"],"category_ids":[category]})
    definition="弹性范围内，弹簧力的大小与相对自然长度的伸长量成正比。"
    point_content={"title":"胡克定律与弹性测量","definition":definition,"conditions":["小形变、未越过弹性限度、静态平衡","零点已校准时可采用过原点约定"],
                   "observables":["力 F / N","伸长 x / m"],"formula_refs":[ref(formula),ref(slope)],"prerequisites":[],"measurement_relations":["成对测量力和伸长"],
                   "error_relations":["零点偏移、未达平衡与迟滞影响模型"],"claims":[{"text":definition,"sources":physics_sources,"status":"source_supported"}],
                   "domains":["力"],"category_ids":[category],"sources":physics_sources,"conflicts":[]}
    point=app.gateway.import_knowledge("SciencePoint",point_content)
    additional=copy.deepcopy(point_content);additional["sources"]=[b1["id"]]
    additional["conditions"]=["小形变、未越过弹性限度、静态平衡","未独立清零时保留截距，不强制过原点"]
    additional["claims"]=[{"text":"零点偏移未经检验时保留截距；不同来源条件差异待使用方选择。","sources":[b1["id"]],"status":"source_supported"}]
    point=app.gateway.import_knowledge("SciencePoint",additional,point["id"])
    math=app.gateway.import_knowledge("MathFoundation",{
        "title":"普通最小二乘的正规方程","definition":"通过最小化残差平方和，求线性模型的斜率和截距。","method":"对斜率和截距分别求偏导并令其为零，联立正规方程。",
        "conditions":["残差平方和可微","自变量不全相等","用于此物理问题时假设力误差独立等方差、伸长误差相对可忽略"],
        "symbols":["x_i 自变量","y_i 因变量","k 斜率","b 截距"],"derivation":["定义残差平方和","求偏导得到正规方程","消去截距求斜率"],"prerequisites":[],
        "claims":[{"text":"最小二乘的极值满足两条正规方程。","sources":math_sources,"status":"source_supported"}],"sources":math_sources})
    use=app.gateway.import_knowledge("MathUse",{
        "title":"用最小二乘从力与伸长求劲度系数","science_ref":ref(point),"math_ref":ref(math),"formula_refs":[ref(formula),ref(slope)],
        "symbol_mapping":[{"math":"x_i","physical":"第 i 次弹簧伸长","unit":"m"},{"math":"y_i","physical":"同一平衡时刻的力","unit":"N"},{"math":"k","physical":"劲度系数","unit":"N/m"},{"math":"b","physical":"力通道零点偏移","unit":"N"}],
        "assumptions":["伸长误差相对可忽略，力误差近似独立等方差","线性弹性区间和静态平衡","截距保留，避免未经检验假定已清零"],
        "steps":["把每个平衡时刻的伸长 x_i 与力 F_i 成对列入数据表，先统一为 m 与 N。",
                 "定义残差为 F_i 减去 k x_i 加 b 的模型预测；残差平方和对 k 和 b 分别可微。",
                 "对截距求偏导并令其为零，得到 b 等于平均力减去 k 乘平均伸长。",
                 "将截距表达式代回关于斜率的正规方程，分子是伸长与力的离均差乘积之和，分母是伸长离均差平方和。",
                 "检查分母非零；分子单位 m N，分母单位 m²，所以斜率单位为 N/m。最后检查残差与零点、迟滞等物理条件。"],"sources":math_sources})
    relation=app.gateway.import_knowledge("KnowledgeRelation",{"title":"最小二乘适用于弹性参数估计","from_ref":ref(math),"relation":"适用于","to_ref":ref(point),"direction":"from_to",
                  "conditions":["线性弹性区间；自变量误差相对可忽略"],"status":"inferred","sources":math_sources+physics_sources})
    exp=app.store.create_experiment("弹簧劲度系数 教学模拟","理解弹性模型，并从成对的力与伸长估计劲度系数",{
        "level":"已学微积分、线性代数与力学；尚不熟悉误差分析","goal":"理解目标量到读数和最小二乘的联系","minutes":15},source_ids[:2])
    dataset=app.materials.associate_dataset(exp["id"],source_ids[2],{"x_m":{"quantity":"伸长","unit":"m"},"F_N":{"quantity":"力","unit":"N"}},True)
    app.gateway.apply('ExperimentDraft',demo_outline(),experiment_id=exp['id'])
    short=app.agent.reference_document(exp["id"],"short")
    deep=app.agent.reference_document(exp["id"],"deep")
    plan=app.gateway.apply("LearningDocument",demo_plan(exp,point,formula,slope,[a1["id"],a2["id"],b1["id"]]),experiment_id=exp["id"])
    program=app.gateway.apply("AnalysisProgram",{
        "title":"已知斜率 100 的模拟数据分析 固定示例","code":DEMO_CODE,
        "method_ids":["ols"],
        "steps":[{"id":"S1","operation":"统一列单位并筛选参数区间","assumptions":["单位已明确确认"],"outputs":["选区数据"]},
                 {"id":"S2","operation":"直接实现带截距的最小二乘及残差","assumptions":["线性弹性教学模型"],"outputs":["k","b","残差","图"]}],
        "formula_map":[{"step_id":"S2","formula_ref":ref(slope),"code_lines":[20,24],"input_columns":["x_m","F_N"],"units":["m","N"]}],
        "inputs":{"dataset_ids":[dataset["id"]]},"parameters":{"xmin":0.01,"xmax":0.05},
        "environment":{"python":"3.13.16","dependencies":{"numpy":"2.3.4","matplotlib":"3.10.7"}},"simulation":True,"sources":math_sources},experiment_id=exp["id"])
    app.store.message(exp["id"],"assistant","这是开发者自编教学模拟。两份材料演示条件差异、知识与数学应用、短／深讲义和实验单；固定样例代码可实际隔离运行。此过程没有调用模型，不能代替真实实验。","demo")
    app.store.execute("INSERT OR REPLACE INTO meta VALUES('demo_experiment',?)",(exp["id"],))
    return {"experiment_id":exp["id"],"source_ids":source_ids,"point_id":point["id"],"math_id":math["id"],"use_id":use["id"],"relation_id":relation["id"],
            "short_id":short["id"],"deep_id":deep["id"],"plan_id":plan["id"],"program_id":program["id"],"existing":False}


def demo_outline():
    """Explicit developer simulation, never a live-experiment default outline."""
    stages=[]
    for key,title,knowledge,question,explanation in [
        ('prestudy','理解弹性模型','定义力与伸长，列弹性区间内的线性关系','模型为什么有适用条件','联系可逆形变与模型，不把外推当实测。'),
        ('procedure','安排加载与读数','按已给教学材料确定零点与逐级加载顺序','为什么要先辨认零点','说明零点与后续模型参数的关系。'),
        ('recording','记录成对数据','记录力和伸长的原始单位、配对与实际异常','为什么必须成对记录','解释配对与自变量、因变量之间的关系。'),
        ('analysis','分析与解释','列线性拟合和残差的含义及使用条件','结果如何回到弹性模型','区分数值拟合结果、模型适用性和实测机制。')]:
        stages.append({'key':key,'title':title,'scope':'developed','points':[{'id':key+'_point','title':title,
            'knowledge':[{'kind':'fact','text':knowledge}],
            'material_plan':'使用自编弹簧教学材料；不是实际课程记录或热导热电答案。',
            'interpretations':[{'question':question,'emphasis':explanation}]}]})
    return {'title':'弹簧教学模拟的实验草稿','purpose':'只验证机制与已保存内容的编排，不代表真实生成质量。',
            'source_strategy':'自编模拟资料，模型解释与事实分开。','stages':stages,
            'request':{'initial':'开发者固定教学模拟，不是真实用户请求。'},'sources':[]}


def demo_plan(exp,point,formula,slope,sources):
    def paragraph(text):
        return {"type":"paragraph","text":text,"sources":sources}
    def table(caption,columns,rows):
        return {"type":"table","caption":caption,"columns":columns,"rows":rows,"sources":sources}
    return {"title":"弹簧劲度系数实验计划 教学模拟","doc_type":"plan","summary":"通过成对测量静态力与伸长，在弹性条件下估计劲度系数。先确认零点、量程和单位，再采样并核对残差；仪器按钮和精度仍须按本课实物确认。",
        "background":exp["background"],"simulation":True,"warnings":["此为教学模拟，未选定真实课程仪器。","仪器精度、量程及按钮步骤待确认，不能报告完整不确定度。","两份材料的清零条件不同；此计划保留截距，正式实验由教师规范决定。"],
        "goals":[{"id":"G1","quantity":"劲度系数 k","unit":"N/m","model":"力 F 与伸长 x 的线性关系，保留零点截距","formula_ref":ref(slope),"conditions":["小形变、弹性限度内、静态平衡","伸长误差相对可忽略；否则另选拟合模型"],"sources":sources,
            "measurements":[{"quantity":"伸长 x 与参考长度","unit":"m","timing":"每次载荷稳定后","condition":"同一参考零点","instrument":"长度读数装置，型号待确认","precision":"待确认"},
                            {"quantity":"力 F 与参考力","unit":"N","timing":"与伸长同一平衡时刻","condition":"通道及零点记录清楚","instrument":"力传感器，型号待确认","precision":"待确认"}]}],
        "steps":[{"id":"S1","action":"确认参考状态与量程","reason":"区分物理恢复力和通道零点偏移，保证读数属于同一参考状态","conditions":"静态装置完整，载荷不越量程","completion":"记录参考长度、参考力、通道、单位和待确认精度","goal_ids":["G1"],"instrument_appendix":"instruments","sources":sources},
                 {"id":"S2","action":"分级采样并保留卸载观察","reason":"成对读数才能求斜率；卸载观察用于识别迟滞或永久形变","conditions":"小形变且每次达到静态平衡","completion":"各载荷有成对 F、x 和状态记录；完成卸载回零检查","goal_ids":["G1"],"instrument_appendix":"instruments","sources":sources},
                 {"id":"S3","action":"核对模型与结果","reason":"斜率数值只有在模型和单位成立时才有物理意义","conditions":"列含义和单位已确认，至少两个不同伸长；残差检查使用更多点","completion":"核对单位 N/m、零点、残差和异常；精度未知时保留不确定度缺项","goal_ids":["G1"],"instrument_appendix":"instruments","sources":sources}],
        "sections":[{"id":"logic","title":"模型与测量逻辑","blocks":[paragraph("目标量 k 不直接从仪器读取，而由力和伸长的关系求出。保留截距使未经独立清零的通道偏移可见；过原点模型需要额外零点证据。"),
            {"type":"equation","latex":formula["content"]["latex"],"label":"1","symbols":"F 为力 N；x 为伸长 m；k 为劲度系数 N/m；b 为力通道零点偏移 N。","formula_ref":ref(formula),"sources":sources},
            {"type":"crossref","text":"打开数据与现象记录附录","target":"records"}]},
            {"id":"completion","title":"质量检查与异常分流","blocks":[paragraph("若残差呈系统弯曲，先核对是否超出弹性范围；若卸载不回参考状态，保留原始观察并检查迟滞或零点变化。缺精度时只能解释当前斜率，不能给出完整不确定度。"),{"type":"crossref","text":"打开误差分析附录","target":"errors"}]}],
        "appendices":[
            {"id":"records","title":"附录 A 数据与现象记录","blocks":[table("空白记录表 不补造读数",["次序与时机","伸长 / m","力 / N","原始观察","模型解释","待核对推断"],[["参考状态","未记录","未记录","未记录","未解释","待核对"],["载荷稳定后","未记录","未记录","未记录","未解释","待核对"],["卸载后","未记录","未记录","未记录","未解释","待核对"]])]},
            {"id":"errors","title":"附录 B 误差分析","blocks":[table("影响与下一项检查",["来源","影响","检查","缺项"],[["长度与力零点","截距／斜率可能偏移","重复参考状态","未提供精度"],["未静态平衡","成对读数失配","等待并观察稳定","稳定判据待仪器确认"],["迟滞／超弹性范围","线性模型失效","卸载检查与残差","不能仅删点改善直线"]])]},
            {"id":"instruments","title":"附录 C 仪器操作细节","blocks":[paragraph("型号、量程、开机、清零和读数按钮：待按本课实物或正式仪器手册确认。本教学材料未提供按钮位置；不要据此虚构操作。确认后在此附录保存修订，保留原版。"),paragraph("读数前核对力通道与长度参考，采样时保持静态平衡。仪器精度必须记录来源，不使用理想模拟数据的整数位数代替仪器分度。") ]},
            {"id":"reading","title":"附录 D 拓展阅读","blocks":[paragraph("先回教材 A 第 1 页核对弹性条件，第 2 页核对最小二乘假设；再对照教材 B 第 1 页的零点约定。网络补充须留下具体网址、访问记录和所支持的论述；当前示例未进行联网检索。")]}],
        "knowledge_refs":[ref(point),ref(formula),ref(slope)],"sources":sources}
