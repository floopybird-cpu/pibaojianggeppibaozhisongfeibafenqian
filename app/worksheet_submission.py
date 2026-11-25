"""Submit one structured stage and preserve failed receipts for continuation."""
import copy

from .format_program import check_schema
from .storage import canonical
from .tool_receipts import named_parameters


class SubmissionIncomplete(ValueError):
    """The model returned, but this stage has no valid structured result."""


def submit_record(service, job, tool, messages, options, step, saved, keep, normalize=None, accept=None, continuation=None):
    from .worksheets import decode_json

    runtime = service.agent.runtime
    schema = tool["function"]["parameters"]
    name = tool["function"]["name"]
    label = {"outline": "实验草稿", "source_relations": "讲义核对结果",
             "interaction": "本次答复", "interaction_answer": "本次答复", "method": "方法修改"}.get(step, "测量页")
    if step.startswith("interaction"):label = "本次答复"
    checkpoint = saved(step + ":submission")
    conversation = copy.deepcopy(messages)
    reply = None
    if checkpoint:
        conversation = runtime.read_blob(checkpoint["messages"])
        reply = runtime.read_blob(checkpoint["reply"])

    # One automatic format correction per invocation. An explicit continuation
    # starts from the last saved receipt, so completed stages are not regenerated.
    for attempt in range(2):
        if job is not None and service.store.one("SELECT cancel FROM jobs WHERE id=?", (job,))["cancel"]:
            raise InterruptedError("已取消")
        if reply is None:
            reply, usage = service.agent.client.complete(
                conversation, tools=[tool],
                options={**options, "native_search": False, "_stream": False},
            )
            checkpoint = {
                "messages": runtime.blob(conversation),
                "reply": runtime.blob(reply),
                "usage": usage,
            }
            keep(step + ":submission", checkpoint)
        calls = reply.get("tool_calls") or []
        error = None
        try:
            matching = [call for call in calls if call.get("function", {}).get("name") == name]
            if calls and (len(calls) != 1 or len(matching) != 1):
                raise ValueError("本阶段须提交一次 " + name)
            text = matching[0]["function"]["arguments"] if matching else reply.get("content") or ""
            value = named_parameters(decode_json(text), schema)
            if normalize is not None:
                value = normalize(value)
            check_schema(value, schema, None, label)
            if accept is not None:
                accept(value)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            error = str(exc)
        if error is None:
            keep(step, value)
            runtime.event(job, "worksheet_submission_accepted", {
                "step": step, "receipt": checkpoint["reply"], "after_format_feedback": bool(attempt),
            })
            return value

        runtime.event(job, "worksheet_submission_invalid", {
            "step": step, "receipt": checkpoint["reply"], "error": error,
            "automatic_correction": attempt == 0,
        })
        if attempt == 1:
            if step.startswith("interaction") or step=="method":
                raise SubmissionIncomplete(label+"仍未提交有效的工具参数，原始响应已保存。这次操作没有执行，可以重试这次提问或操作。")
            raise SubmissionIncomplete(
                label + "的提交格式仍不完整，响应和已完成步骤已保存。"
                "可以在当前实验说“继续生成”接续。"
            )
        # Preserve the provider's real assistant turn, including its native
        # reasoning fields and call identifiers, then return a tool error.
        conversation.append(copy.deepcopy(reply))
        feedback = canonical({
            "status": "invalid_arguments",
            "error": error,
            "required_top_level_fields": schema.get("required", []),
            "action": "沿用已有材料和判断，补齐本次工具参数后重新提交。保留有效内容。",
        })
        if calls:
            for call in calls:
                conversation.append({"role": "tool", "tool_call_id": call["id"], "content": feedback})
        else:
            conversation.append({"role": "user", "content": "【宿主提交格式反馈】" + feedback})
        if continuation and checkpoint and not any(m.get("role")=="user" and m.get("content")==continuation for m in conversation):
            conversation.append({"role":"user","content":continuation})
        reply = None
