"""Schema-aware boundary handling for provider function arguments."""
import copy
import json
import re


def project_empty_extras(value,schema):
    """Project empty undeclared placeholders from closed objects; retain the raw value."""
    paths=[]
    def visit(part,spec,path):
        if not isinstance(spec,dict):return copy.deepcopy(part)
        if isinstance(part,list):
            return [visit(item,spec.get("items",{}),path+"/"+str(i)) for i,item in enumerate(part)]
        if not isinstance(part,dict):return copy.deepcopy(part)
        properties=spec.get("properties",{});result={}
        for key,item in part.items():
            at=path+"/"+str(key).replace("~","~0").replace("/","~1")
            empty=item is None or isinstance(item,(str,list,dict)) and len(item)==0
            if key not in properties and spec.get("additionalProperties") is False and empty:
                paths.append(at);continue
            child=properties.get(key,spec.get("additionalProperties",{}))
            result[key]=visit(item,child,at)
        return result
    return visit(value,schema,""),paths


def strict_declarations(tools):
    def normalize(schema):
        result={k:copy.deepcopy(v) for k,v in schema.items() if k not in ("minLength","maxLength","minItems","maxItems")}
        if result.get("type")=="object":
            result["properties"]={k:normalize(v) for k,v in result.get("properties",{}).items()}
            result["required"]=list(result["properties"])
            result["additionalProperties"]=False
        if result.get("type")=="array":result["items"]=normalize(result["items"])
        for key in ("anyOf","oneOf"):
            if key in result:result[key]=[normalize(v) for v in result[key]]
        return result
    output=copy.deepcopy(tools)
    for item in output:
        item["function"]["strict"]=True
        item["function"]["parameters"]=normalize(item["function"]["parameters"])
    return output


def named_parameters(value,schema):
    """Recover complete named siblings accidentally embedded in one string; never infer content."""
    if isinstance(value,list):
        return [named_parameters(v,schema.get("items",{})) for v in value]
    if not isinstance(value,dict):return value
    result=copy.deepcopy(value)
    properties=schema.get("properties",{})
    for name,part in list(value.items()):
        if name not in properties:continue
        if isinstance(part,str):
            marker=re.search(r"</(?:parameter|"+re.escape(name)+r")>\s*(?=<parameter\s+name=)",part)
            if marker:
                tail=part[marker.end():]
                tags=list(re.finditer(r'<parameter\s+name=["\']([A-Za-z_]\w*)["\']\s*>',tail))
                additions={}
                for i,tag in enumerate(tags):
                    key=tag[1]
                    if key not in properties:raise ValueError("回执含契约未声明的字段 "+key)
                    end=tags[i+1].start() if i+1<len(tags) else len(tail)
                    body=re.sub(r"</parameter>\s*$","",tail[tag.end():end]).strip()
                    parsed=body if properties[key].get("type")=="string" else json.loads(body)
                    if key in result and result[key]!=parsed or key in additions and additions[key]!=parsed:
                        raise ValueError("回执对字段 "+key+" 给出了冲突值")
                    additions[key]=parsed
                if additions:
                    result[name]=part[:marker.start()]
                    result.update(additions)
    for name,part in list(result.items()):
        if name in properties:result[name]=named_parameters(part,properties[name])
    return result
