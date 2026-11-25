from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


class DATA_BLOB(C.Structure):
    _fields_ = [("cbData", W.DWORD), ("pbData", C.POINTER(C.c_ubyte))]


def dpapi(data: bytes, decrypt=False):
    if os.name != "nt":
        raise ValueError("密钥存储需要 Windows DPAPI；不退回明文")
    buffer = (C.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = DATA_BLOB(len(data), buffer), DATA_BLOB()
    crypt = C.WinDLL("crypt32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [C.c_void_p]
    if decrypt:
        ok = crypt.CryptUnprotectData(C.byref(source), None, None, None, None, 1, C.byref(target))
    else:
        ok = crypt.CryptProtectData(C.byref(source), "Physics Harness model credential", None, None, None, 1, C.byref(target))
    if not ok:
        raise C.WinError(C.get_last_error())
    try:
        return C.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


class Settings:
    def __init__(self, authority: Path):
        self.path = authority / "settings.json"
        self.secret_path = authority / "model-key.dpapi"

    def read(self):
        value = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        return {"base_url": value.get("base_url", "https://api.deepseek.com"), "model": value.get("model", "deepseek-flash"),
                "font": value.get("font", ""), "language": value.get("language","zh-CN"), "course_rules": value.get("course_rules", ""),
                "configured": self.secret_path.exists() and bool(value.get("model")), "timeout": value.get("timeout", 0),
                "analysis_timeout":value.get("analysis_timeout",0),"max_output_tokens":value.get("max_output_tokens",0),
                "analysis_memory_mb":value.get("analysis_memory_mb",0),"analysis_output_mb":value.get("analysis_output_mb",0),
                "protocol":value.get("protocol","chat"),"thinking_depth":value.get("thinking_depth","high"),
                "native_search":value.get("native_search",True),"native_vision":value.get("native_vision",True),
                "native_search_transport":value.get("native_search_transport","provider"), "products_directory":value.get("products_directory", "")}

    def update(self, value):
        old = self.read()
        base_url = str(value.get("base_url", old["base_url"])).rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型地址须为不含密钥或查询参数的 HTTPS API 根地址，例如 https://api.example.com/v1")
        model = str(value.get("model", old["model"])).strip()
        result = {"base_url": base_url, "model": model, "course_rules": str(value.get("course_rules", old["course_rules"])),
                  "font": str(value.get("font", old["font"])), "language":str(value.get("language",old["language"])).strip() or old["language"], "timeout": max(0,int(value.get("timeout", old["timeout"]))),
                  "analysis_timeout":max(0,int(value.get("analysis_timeout",old["analysis_timeout"]))),
                  "analysis_memory_mb":max(0,int(value.get("analysis_memory_mb",old["analysis_memory_mb"]))),
                  "analysis_output_mb":max(0,int(value.get("analysis_output_mb",old["analysis_output_mb"]))),
                  "max_output_tokens":max(0,int(value.get("max_output_tokens",old["max_output_tokens"])))}
        result.update({k:value.get(k,old[k]) for k in ("protocol","thinking_depth","native_search","native_vision","native_search_transport")})
        result["products_directory"] = str(value.get("products_directory", old["products_directory"])).strip()
        if result["protocol"] not in ("chat","responses") or result["thinking_depth"] not in ("off","low","high","max"):
            raise ValueError("不支持的模型协议或思考档位")
        if value.get("clear_key"):
            self.secret_path.unlink(missing_ok=True)
        if value.get("api_key"):
            self.secret_path.write_bytes(dpapi(str(value["api_key"]).strip().encode()))
        self.path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.read()

    def key(self):
        return dpapi(self.secret_path.read_bytes(), decrypt=True).decode() if self.secret_path.exists() else ""

    def redact(self, text):
        key = self.key()
        return str(text).replace(key, "[REDACTED]") if key else str(text)
