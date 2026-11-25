"""Durable, first-class questions. A real UI/chat answer resumes the same job."""
from __future__ import annotations
import json
import re
from .storage import canonical, now, uid


class Questions:
    def __init__(self,store):
        self.store=store;self.on_answer=None
        store.execute("CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY,job_id TEXT NOT NULL,experiment_id TEXT,title TEXT NOT NULL,questions TEXT NOT NULL,answer TEXT,status TEXT NOT NULL,context TEXT,created TEXT NOT NULL,updated TEXT NOT NULL)")

    def ask(self,job_id,experiment_id,title,questions):
        if not isinstance(questions,list) or not questions:raise ValueError("请说明需要用户确认的实际问题")
        known=set();clean=[]
        for item in questions:
            if not isinstance(item,dict) or not str(item.get("question","")).strip():raise ValueError("问题须有完整、可理解的文字")
            identity=item.get("id") or uid("item")
            if identity in known:raise ValueError("同批问题标识重复")
            known.add(identity)
            options=item.get("options",[])
            if not isinstance(options,list) or any(not isinstance(s,str) or not s.strip() for s in options):raise ValueError("选项须为文字，也允许用户自由回答")
            target=item.get("target_object_id");issue=item.get("issue_id")
            if target:
                obj=self.store.object(target)
                if obj["experiment_id"] and obj["experiment_id"]!=experiment_id:raise ValueError("提问不能指挥其他实验")
                if issue and not any(q["id"]==issue for q in obj["content"].get("issues",[])):raise ValueError("待确认事项不存在，请先保存真实问题")
            if item.get('material_basis') is not None:
                from .material_context import validate_basis
                item={**item,'material_basis':validate_basis(self.store,item['material_basis'],experiment_id)}
            clean.append({**item,"id":identity,"question":str(item["question"]).strip(),"options":options})
        identity=uid("question")
        self.store.execute("INSERT INTO questions VALUES(?,?,?,?,?,NULL,'pending',NULL,?,?)",(identity,job_id,experiment_id,str(title).strip() or "需要确认",canonical(clean),now(),now()))
        if experiment_id:
            self.store.message(experiment_id,"assistant",str(title)+"\n\n"+"\n\n".join(str(i+1)+". "+q["question"] for i,q in enumerate(clean))+"\n\n可以直接在聊天中回答，也可以点“回答问题”；未记录的部分请直说未记录。","question")
        return {"question_id":identity,"status":"waiting_user","title":title,"questions":clean,"notice":"必须等待真实用户答复，不能自行选择答案或继续依赖这些事实的操作"}

    def read(self,identity):
        row=self.store.one("SELECT * FROM questions WHERE id=?",(identity,))
        if not row:raise ValueError("问题不存在")
        row["questions"]=json.loads(row["questions"])
        row["context"]=json.loads(row["context"]) if row["context"] else None
        return row

    def pending(self,experiment_id=None,include_all=False):
        clause="" if include_all else " AND experiment_id IS NULL" if experiment_id is None else " AND experiment_id=?"
        args=() if include_all or experiment_id is None else (experiment_id,)
        return [self.read(r["id"]) for r in self.store.all("SELECT id FROM questions WHERE status='pending'"+clause+" ORDER BY created",args)]

    def checkpoint(self,identity,context):
        self.store.execute("UPDATE questions SET context=?,updated=? WHERE id=? AND status='pending'",(canonical(context),now(),identity))

    def answer(self,identity,text,item_answers=None):
        if not isinstance(text,str) or not text.strip():raise ValueError("请回答需要确认的问题；也可以说明未记录或暂时不知道")
        row=self.read(identity)
        if row["status"]!="pending":raise ValueError("这个问题已答复或取消，不会重复提交")
        if not row["context"]:raise ValueError("助手正在保存提问，请稍后再提交")
        known={q["id"] for q in row["questions"]}
        if item_answers is not None:
            if not isinstance(item_answers,dict) or set(item_answers)-known or any(not isinstance(v,str) or not v.strip() for v in item_answers.values()):raise ValueError("逐题答复须对应当前真实问题")
            assigned=dict(item_answers)
        elif len(known)==1:assigned={row["questions"][0]["id"]:text.strip()}
        else:
            assigned={}
            numbered=list(re.finditer(r"(?:^|\n)\s*(?:第)?([1-9一二三四五六七八九])(?:个|题|条)?\s*[.、：:）)]",text))
            digits="一二三四五六七八九"
            for i,match in enumerate(numbered):
                index=int(match.group(1))-1 if match.group(1).isdigit() else digits.index(match.group(1))
                if index<len(row["questions"]):assigned[row["questions"][index]["id"]]=text[match.end():numbered[i+1].start() if i+1<len(numbered) else len(text)].strip()
            if not assigned and re.search(r"(?:这些|全部|所有|先|继续).{0,20}(?:待确认|未记录|不知道|未答复)",text):
                assigned={q["id"]:text.strip() for q in row["questions"]}
        context=row["context"];context["submitted_item_answers"]=assigned
        with self.store.transaction() as conn:
            changed=conn.execute("UPDATE questions SET answer=?,context=?,status='answered',updated=? WHERE id=? AND status='pending'",(text.strip(),canonical(context),now(),identity)).rowcount
            if not changed:raise ValueError("答复已提交，不会重复执行")
            if row["experiment_id"]:
                message_id=uid("msg")
                conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)",(message_id,row["experiment_id"],"user",text.strip(),"model",now()))
                job=conn.execute("SELECT payload FROM jobs WHERE id=?",(row["job_id"],)).fetchone()
                payload=json.loads(job[0]);payload["user_message_id"]=message_id
                conn.execute("UPDATE jobs SET payload=? WHERE id=?",(canonical(payload),row["job_id"]))
        self.store.audit("user_answer",{"question_id":identity,"job_id":row["job_id"],"answer":text.strip(),"origin":"native_ui_or_current_user_chat"})
        if self.on_answer:self.on_answer(identity)
        return {"id":row["job_id"],"question_id":identity,"status":"answer_saved","message":"答复已保存，接续同一任务"}

    def answer_lookup(self,identity):
        row=self.read(identity)
        assigned=(row.get("context") or {}).get("submitted_item_answers",{})
        answers=[{"target_object_id":q.get("target_object_id"),"issue_id":q.get("issue_id"),"answer":assigned[q["id"]]} for q in row["questions"] if q.get("target_object_id") and q["id"] in assigned]
        items=[{'id':q['id'],'question':q['question'],'target_object_id':q.get('target_object_id'),'issue_id':q.get('issue_id'),'material_basis':q.get('material_basis'),'answer':assigned.get(q['id'],row['answer'] if len(row['questions'])==1 else None)} for q in row['questions']]
        return {"items":items,'unassigned_answer':row['answer'] if not assigned else None,"status":row["status"],"answer":row["answer"],"targets":[{"target_object_id":q.get("target_object_id"),"issue_id":q.get("issue_id")} for q in row["questions"] if q.get("target_object_id")],"answers":answers}

    def record_explicit_answer(self,job_id,experiment_id,target_object_id,issue_id,quote):
        """Record an already explicit chat answer, without asking it twice."""
        job=self.store.one("SELECT payload FROM jobs WHERE id=?",(job_id,));payload=json.loads(job["payload"])
        user=self.store.one("SELECT * FROM messages WHERE id=?",(payload.get("user_message_id",""),))
        if not user or user["role"]!="user" or user["experiment_id"]!=experiment_id or not quote or quote not in user["content"]:raise ValueError("须逐字引用当前真实用户明确答复，不能引用教材或模型文字")
        obj=self.store.object(target_object_id);issue=next((q for q in obj["content"].get("issues",[]) if q["id"]==issue_id),None)
        if not issue:raise ValueError("对应的原问题不存在")
        identity=uid("question");question={"id":issue_id,"question":issue["question"],"target_object_id":target_object_id,"issue_id":issue_id,"options":[]}
        self.store.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",(identity,job_id,experiment_id,"用户已明确答复",canonical([question]),quote,"answered",canonical({"submitted_item_answers":{issue_id:quote}}),now(),now()))
        self.store.audit("explicit_user_answer",{"question_id":identity,"message_id":user["id"],"target_object_id":target_object_id,"issue_id":issue_id})
        return identity

    def cancel_job(self,job_id):
        self.store.execute("UPDATE questions SET status='cancelled',updated=? WHERE job_id=? AND status='pending'",(now(),job_id))
        self.store.execute("UPDATE jobs SET cancel=1,status='cancelled',updated=? WHERE id=? AND status='waiting_user'",(now(),job_id))
