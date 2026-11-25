"""Zero-model check of the same isolated backend used by real analysis."""
from pathlib import Path
import json
import uuid

from .sandbox import WindowsSandbox


PROBE = r'''import json, os, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
parameters=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
outside=Path(parameters["outside"])
checks={}
for name,action in [("outside_read_denied",lambda:outside.read_text()),
                    ("outside_write_denied",lambda:outside.with_suffix(".write-test").write_text("probe")),
                    ("runtime_write_denied",lambda:Path(sys.executable).with_name("probe-write-test.tmp").write_text("probe"))]:
    try:action();checks[name]=False
    except PermissionError:checks[name]=True
checks["no_key_environment"]=not any("API_KEY" in key.upper() or "APIKEY" in key.upper() for key in os.environ)
x=np.array([0.,1.,2.]);y=np.array([1.,3.,5.]);slope,intercept=np.polyfit(x,y,1)
checks["numerical_fit"]=bool(np.isclose(slope,2) and np.isclose(intercept,1))
plt.plot(x,y,"o-");plt.xlabel("x");plt.ylabel("y");plt.savefig("probe.png");plt.close()
Path("result.json").write_text(json.dumps({"checks":checks,"slope":float(slope),"intercept":float(intercept)}),encoding="utf-8")
if not all(checks.values()):raise RuntimeError("Isolated environment check failed")
'''


def check_environment(runtime, directory):
    root=Path(directory)/("environment-"+uuid.uuid4().hex)
    root.mkdir(parents=True)
    outside=root/'host-only-canary.txt'
    outside.write_text('Harmless local environment probe; no user information.',encoding='utf-8')
    result=WindowsSandbox(Path(runtime),root/'runs').execute(PROBE,{}, {'outside':str(outside)},timeout=60)
    output=Path(result['output']);data=output/'result.json'
    details=json.loads(data.read_text('utf-8')) if data.is_file() else {}
    report={**result,'probe':details,'model_requests':0,
            'passed':result['status']=='succeeded' and bool(details) and all(details['checks'].values()) and (output/'probe.png').is_file()}
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not report['passed']:raise RuntimeError('计算环境检查未通过，记录：'+str(root/'report.json')+'\n'+result['stderr'])
    return {**report,'report_file':str(root/'report.json')}
