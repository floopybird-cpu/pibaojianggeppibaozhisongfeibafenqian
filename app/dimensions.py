"""SI dimensional algebra. No imports/eval from a supplied expression."""
import ast
import math

BASE=('kg','m','s','A','K','mol','cd')
ZERO=(0.,)*7
def combine(a,b,sign=1):return tuple(x+sign*y for x,y in zip(a,b))
def scaled(a,k):return tuple(x*k for x in a)
def equal(a,b):return all(math.isclose(x,y,abs_tol=1e-10) for x,y in zip(a,b))
UNITS={name:tuple(float(i==j) for i in range(7)) for j,name in enumerate(BASE)}
UNITS.update({'J':(1,2,-2,0,0,0,0),'W':(1,2,-3,0,0,0,0),
              'N':(1,1,-2,0,0,0,0),'Pa':(1,-1,-2,0,0,0,0),
              'V':(1,2,-3,-1,0,0,0),'ohm':(1,2,-3,-2,0,0,0),
              'C':(0,0,1,1,0,0,0),'F':(-1,-2,4,2,0,0,0),
              'Hz':(0,0,-1,0,0,0,0),'rad':ZERO})
UNITS['Ω']=UNITS['ohm']

def constant(node):
    if isinstance(node,ast.Constant) and type(node.value) in (int,float):return float(node.value)
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        return constant(node.operand)*(-1 if isinstance(node.op,ast.USub) else 1)
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Div):return constant(node.left)/constant(node.right)
    raise ValueError('量纲指数须为常数或常数分数')

def dimension(node,names):
    if isinstance(node,ast.Constant) and type(node.value) in (int,float):return ZERO
    if isinstance(node,ast.Name):
        if node.id in ('pi','e'):return ZERO
        if node.id not in names:raise ValueError('未声明量纲：'+node.id)
        return names[node.id]
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        return dimension(node.operand,names)
    if isinstance(node,ast.BinOp):
        a=dimension(node.left,names)
        if isinstance(node.op,ast.Pow):return scaled(a,constant(node.right))
        b=dimension(node.right,names)
        if isinstance(node.op,(ast.Add,ast.Sub)):
            if not equal(a,b):raise ArithmeticError('加减两项的量纲不同')
            return a
        if isinstance(node.op,ast.Mult):return combine(a,b)
        if isinstance(node.op,ast.Div):return combine(a,b,-1)
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and len(node.args)==1 and not node.keywords:
        d=dimension(node.args[0],names);f=node.func.id
        if f=='sqrt':return scaled(d,.5)
        if f=='abs':return d
        if f in ('exp','log','log10','sin','cos','tan'):
            if not equal(d,ZERO):raise ArithmeticError(f+'的输入须无量纲')
            return ZERO
    raise ValueError('只支持基本量纲代数与常用单参数学函数')

def unit(value):
    value=value.replace('·','*').replace('^','**')
    return dimension(ast.parse(value,mode='eval').body,UNITS)

def check_dimensions(items):
    results=[]
    for item in items:
        try:
            variables={k:unit(v) for k,v in item['variable_units'].items()}
            actual=dimension(ast.parse(item['expression'],mode='eval').body,variables)
            target=unit(item['unit']);compatible=equal(actual,target)
            result={**item,'status':'compatible' if compatible else 'mismatch',
                    'actual_base_powers':dict(zip(BASE,actual)),
                    'expected_base_powers':dict(zip(BASE,target))}
        except ArithmeticError as exc:result={**item,'status':'mismatch','reason':str(exc)}
        except (ValueError,SyntaxError,TypeError) as exc:result={**item,'status':'not_checked','reason':str(exc)}
        results.append(result)
    return {'results':results,'scope':'仅量纲一致性；不证明物理模型、数值换算、摄氏零点或项的命名正确。数字常数按无量纲处理，物理量请用有单位的变量。'}
