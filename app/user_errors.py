"""Keep detailed errors in the journal and explain model failures to the reader."""
import re

def task_failure_message(detail):
    if re.search(r'maximum context|context[_ ]length|上下文.{0,12}(超出|超限)|超过服务端容量',detail,re.I):
        reason='本次生成未完成：单次请求超出了模型服务的容量。'
    elif re.search(r'HTTP\s+(401|403)\b',detail):
        reason='模型服务未通过验证，请在设置中检查地址、模型名和密钥。'
    elif re.search(r'HTTP\s+429\b',detail):
        reason='模型服务暂时不接受请求，请检查服务额度或稍后继续。'
    elif re.search(r'连接暂时失败|连接中断|模型连接',detail):
        reason='模型连接中断，本次任务没有完成。'
    elif re.search(r'模型 HTTP\s+\d+',detail):
        reason='模型服务未能完成本次请求。'
    else:return detail
    return reason+'资料和已生成的成果仍保留。可在当前实验继续，或到资料库的任务列表查看详情与接续入口。'
