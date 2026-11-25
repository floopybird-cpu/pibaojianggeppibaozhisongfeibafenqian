"""Windows AppContainer, zero network capabilities; no host-token fallback.

Only copies of selected input data are exposed to a run.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
import time
import uuid


class SandboxUnavailable(RuntimeError):
    pass


class RuntimePreparationUnavailable(SandboxUnavailable):
    error_code = "runtime_not_prepared"
    phase = "runtime_preparation"
    execution_started = False
    retryable = False


def package_read_rule(path: Path, *, inherit=False):
    """Read our package RX rule without requiring permission to rewrite it.

    This checks the installed rule, not an effective-token AccessCheck. The
    actual AppContainer probe/execution remains the end-to-end access test.
    """
    if os.name != "nt":
        return False
    adv = C.WinDLL("advapi32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    ptr = C.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [W.LPCWSTR, C.c_int, W.DWORD,
                                        ptr, ptr, C.POINTER(ptr), ptr, C.POINTER(ptr)]
    adv.GetNamedSecurityInfoW.restype = W.DWORD
    adv.GetAce.argtypes = [ptr, W.DWORD, C.POINTER(ptr)]
    adv.GetAce.restype = W.BOOL
    adv.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, C.POINTER(ptr)]
    adv.EqualSid.argtypes = [ptr, ptr]
    adv.EqualSid.restype = W.BOOL
    kernel.LocalFree.argtypes = [ptr]
    dacl, descriptor, sid = ptr(), ptr(), ptr()
    try:
        error = adv.GetNamedSecurityInfoW(str(path), 1, 4, None, None,
                                         C.byref(dacl), None, C.byref(descriptor))
        if error:
            raise C.WinError(error)
        if not dacl:
            return False
        if not adv.ConvertStringSidToSidW("S-1-15-2-1", C.byref(sid)):
            raise C.WinError(C.get_last_error())
        # ACL header: revision, reserved, byte size, ACE count, reserved.
        count = C.cast(dacl, C.POINTER(W.WORD))[2]
        allowed = 0
        for index in range(count):
            entry = ptr()
            if not adv.GetAce(dacl, index, C.byref(entry)):
                raise C.WinError(C.get_last_error())
            kind = C.c_ubyte.from_address(entry.value).value
            flags = C.c_ubyte.from_address(entry.value + 1).value
            if kind not in (0, 1) or not adv.EqualSid(ptr(entry.value + 8), sid):
                continue
            mask = W.DWORD.from_address(entry.value + 4).value
            if kind == 1 and mask & 0x1200A9:
                return False
            if flags & 0x08:  # INHERIT_ONLY does not grant this object access.
                continue
            if kind == 0 and (not inherit or flags & 0x03 == 0x03):
                allowed |= mask
        return allowed & 0x1200A9 == 0x1200A9  # FILE_GENERIC_READ | EXECUTE
    finally:
        if sid:
            kernel.LocalFree(sid)
        if descriptor:
            kernel.LocalFree(descriptor)


def output_files(root: Path):
    pending = [root]
    files = []
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or entry.is_symlink():
                    raise ValueError("输出包含重解析点，拒绝跟随")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    files.append((Path(entry.path), info.st_size))
    return files


def acl(path: Path, *arguments):
    result = subprocess.run(["icacls", str(path), *arguments], capture_output=True, creationflags=0x08000000)
    if result.returncode:
        raise SandboxUnavailable("ACL 设置失败: " + result.stdout.decode("mbcs", errors="replace") + result.stderr.decode("mbcs", errors="replace"))


def prepare_runtime(runtime: Path):
    if os.name != "nt":
        raise SandboxUnavailable("当前仅支持 Windows AppContainer 后端")
    executable = runtime / "python.exe"
    if not executable.exists():
        raise RuntimePreparationUnavailable("缺少计算运行组件，请修复应用安装；分析脚本尚未启动。")
    try:
        if package_read_rule(runtime, inherit=True) and package_read_rule(executable):
            return {"state": "already_prepared", "principal": "S-1-15-2-1", "rights": "RX"}
        acl(runtime, "/grant", "*S-1-15-2-1:(OI)(CI)(RX)")
        if not package_read_rule(runtime, inherit=True) or not package_read_rule(executable):
            raise SandboxUnavailable("运行组件的只读执行规则未生效")
        return {"state": "prepared", "principal": "S-1-15-2-1", "rights": "RX"}
    except (OSError, SandboxUnavailable) as exc:
        raise RuntimePreparationUnavailable(
            "计算运行组件尚未准备好，请修复应用安装；分析脚本尚未启动。详情：" + str(exc)) from exc


class STARTUPINFO(C.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
                ("lpReserved2", C.POINTER(C.c_ubyte)), ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]


class STARTUPINFOEX(C.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", C.c_void_p)]


class PROCESSINFO(C.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


class SECURITY_CAPABILITIES(C.Structure):
    _fields_ = [("AppContainerSid", C.c_void_p), ("Capabilities", C.c_void_p), ("CapabilityCount", W.DWORD), ("Reserved", W.DWORD)]


class BASIC_LIMIT(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_longlong), ("PerJobUserTimeLimit", C.c_longlong),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", C.c_size_t), ("MaximumWorkingSetSize", C.c_size_t),
                ("ActiveProcessLimit", W.DWORD), ("Affinity", C.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class EXTENDED_LIMIT(C.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", C.c_size_t), ("JobMemoryLimit", C.c_size_t),
                ("PeakProcessMemoryUsed", C.c_size_t), ("PeakJobMemoryUsed", C.c_size_t)]


BOOTSTRAP = '''import os, sys, json, runpy, traceback
from pathlib import Path
out = Path.cwd()
sys.stdout = open(out / "stdout.txt", "w", encoding="utf-8", buffering=1)
sys.stderr = open(out / "stderr.txt", "w", encoding="utf-8", buffering=1)
try:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError("Cannot verify sandbox token")
    value, size = wintypes.DWORD(), wintypes.DWORD()
    if not adv.GetTokenInformation(token, 29, ctypes.byref(value), 4, ctypes.byref(size)) or value.value != 1:
        raise RuntimeError("Refusing to execute outside AppContainer")
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(token)
    print("PH_SANDBOX AppContainer=true capabilities=0")
    sys.path.insert(0,str(Path(__file__).parent))
    sys.argv = [str(Path(__file__).with_name("analysis.py")), str(Path(__file__).with_name("inputs.json")), str(Path(__file__).with_name("parameters.json")), str(out)]
    runpy.run_path(sys.argv[0], run_name="__main__")
except BaseException:
    traceback.print_exc()
    sys.stdout.flush(); sys.stderr.flush()
    sys.exit(1)
'''


class WindowsSandbox:
    def __init__(self, runtime: Path, staging: Path):
        self.runtime = runtime.resolve()
        self.staging = staging.resolve()
        self._ready = False
        self._lock = threading.Lock()

    def execute(self, code: str, inputs: dict, parameters: dict, *, timeout=0, cancel=None, run_id=None, extras=None, memory_mb=0, output_mb=0):
        if os.name != "nt":
            raise SandboxUnavailable("此设备无 Windows AppContainer；分析已禁用")
        with self._lock:
            if not self._ready:
                prepare_runtime(self.runtime)
                self._ready = True
        run_root = self.staging / (run_id or ("sandbox-" + uuid.uuid4().hex))
        input_dir, output_dir = run_root / "input", run_root / "output"
        input_dir.mkdir(parents=True)
        output_dir.mkdir()
        (input_dir / "analysis.py").write_text(code, encoding="utf-8")
        (input_dir / "bootstrap.py").write_text(BOOTSTRAP, encoding="utf-8")
        for name, value in (("inputs.json", inputs), ("parameters.json", parameters)):
            (input_dir / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        for name,data in (extras or {}).items():
            if name!="analysis_methods.py":raise ValueError("未声明的分析辅助模块")
            (input_dir/name).write_bytes(data)
        kernel = C.WinDLL("kernel32", use_last_error=True)
        userenv = C.WinDLL("userenv", use_last_error=True)
        adv = C.WinDLL("advapi32", use_last_error=True)
        kernel.CloseHandle.argtypes = [W.HANDLE]
        kernel.LocalFree.argtypes = [C.c_void_p]
        kernel.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]
        kernel.CreateJobObjectW.restype = W.HANDLE
        kernel.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        kernel.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
        kernel.ResumeThread.argtypes = [W.HANDLE]
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
        kernel.InitializeProcThreadAttributeList.argtypes = [C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)]
        kernel.UpdateProcThreadAttribute.argtypes = [C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p]
        kernel.DeleteProcThreadAttributeList.argtypes = [C.c_void_p]
        kernel.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR, C.c_void_p, C.POINTER(PROCESSINFO)]
        userenv.CreateAppContainerProfile.argtypes = [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)]
        userenv.CreateAppContainerProfile.restype = C.c_long
        userenv.DeleteAppContainerProfile.argtypes = [W.LPCWSTR]
        adv.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
        adv.FreeSid.argtypes = [C.c_void_p]
        profile = "PhysicsHarness.Run." + uuid.uuid4().hex
        sid = C.c_void_p()
        attribute_buffer = None
        job = None
        process = PROCESSINFO()
        started = time.monotonic()
        status = "failed"
        exit_code = None
        try:
            hr = userenv.CreateAppContainerProfile(profile, profile, "Ephemeral no-network physics calculation", None, 0, C.byref(sid))
            if hr < 0:
                raise SandboxUnavailable(f"CreateAppContainerProfile 失败: 0x{hr & 0xffffffff:08x}；未降级执行")
            sid_text = W.LPWSTR()
            if not adv.ConvertSidToStringSidW(sid, C.byref(sid_text)):
                raise C.WinError(C.get_last_error())
            sid_string = sid_text.value
            kernel.LocalFree(C.cast(sid_text, C.c_void_p))
            # Python makedirs(exist_ok=True) stats the immediate parent even
            # for an existing output folder. Expose only this run's container
            # directory metadata; do not grant ancestors or sibling runs.
            acl(run_root, "/grant", f"*{sid_string}:(RX)")
            acl(input_dir, "/grant", f"*{sid_string}:(OI)(CI)(RX)")
            acl(output_dir, "/grant", f"*{sid_string}:(OI)(CI)(M)")
            owner_name = subprocess.check_output(["whoami"], creationflags=0x08000000).decode("mbcs").strip()
            acl(output_dir, "/grant", f"{owner_name}:(OI)(CI)(F)")
            acl(output_dir, "/setintegritylevel", "(OI)(CI)L")
            capabilities = SECURITY_CAPABILITIES(sid, None, 0, 0)
            length = C.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 1, 0, C.byref(length))
            attribute_buffer = C.create_string_buffer(length.value)
            if not kernel.InitializeProcThreadAttributeList(attribute_buffer, 1, 0, C.byref(length)):
                raise C.WinError(C.get_last_error())
            if not kernel.UpdateProcThreadAttribute(attribute_buffer, 0, 0x00020009, C.byref(capabilities), C.sizeof(capabilities), None, None):
                raise C.WinError(C.get_last_error())
            info = STARTUPINFOEX()
            info.StartupInfo.cb = C.sizeof(info)
            info.lpAttributeList = C.cast(attribute_buffer, C.c_void_p)
            executable = self.runtime / "python.exe"
            command = C.create_unicode_buffer(subprocess.list2cmdline([str(executable), "-I", "-B", "-u", str(input_dir / "bootstrap.py")]))
            # A fresh, minimal environment. No inherited handles and no API keys.
            env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
                   "PATH": str(self.runtime), "TEMP": str(output_dir), "TMP": str(output_dir),
                   "MPLCONFIGDIR": str(output_dir / "mpl-cache"), "MPLBACKEND": "Agg", "PYTHONUTF8": "1"}
            # Windows resolves the lowbox profile using these path variables.
            # They disclose no file contents; the token still denies access.
            for name in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "SystemDrive"):
                if name in os.environ:
                    env[name] = os.environ[name]
            environment = C.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0\0")
            job = kernel.CreateJobObjectW(None, None)
            if not job:
                raise C.WinError(C.get_last_error())
            limits = EXTENDED_LIMIT()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # kill the complete process tree on close
            if memory_mb:
                limits.BasicLimitInformation.LimitFlags |= 0x100 | 0x200
                limits.ProcessMemoryLimit = memory_mb * 1024 * 1024
                limits.JobMemoryLimit = memory_mb * 1024 * 1024
            if not kernel.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)):
                raise C.WinError(C.get_last_error())
            if not kernel.CreateProcessW(str(executable), command, None, None, False,
                                         0x00080000 | 0x00000400 | 0x08000000 | 0x00000004,
                                         environment, str(output_dir), C.byref(info), C.byref(process)):
                raise C.WinError(C.get_last_error())
            if not kernel.AssignProcessToJobObject(job, process.hProcess):
                kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
                kernel.TerminateProcess(process.hProcess, 1)
                raise SandboxUnavailable("无法加入 Job Object，拒绝运行")
            kernel.ResumeThread(process.hThread)
            while kernel.WaitForSingleObject(process.hProcess, 100) == 258:
                if cancel and cancel():
                    status = "cancelled"
                    kernel.TerminateJobObject(job, 1)
                    break
                if timeout and time.monotonic() - started > timeout:
                    status = "timeout"
                    kernel.TerminateJobObject(job, 1)
                    break
                try:
                    total = sum(size for _, size in output_files(output_dir))
                except ValueError:
                    status = "output_rejected"
                    kernel.TerminateJobObject(job, 1)
                    break
                if output_mb and total > output_mb * 1024 * 1024:
                    status = "output_limit"
                    kernel.TerminateJobObject(job, 1)
                    break
            kernel.WaitForSingleObject(process.hProcess, 5000)
            result_code = W.DWORD()
            kernel.GetExitCodeProcess(process.hProcess, C.byref(result_code))
            exit_code = result_code.value
            if status == "failed":
                status = "succeeded" if exit_code == 0 else "failed"
        finally:
            if job:
                kernel.CloseHandle(job)
            for handle in (process.hThread, process.hProcess):
                if handle:
                    kernel.CloseHandle(handle)
            if attribute_buffer:
                kernel.DeleteProcThreadAttributeList(attribute_buffer)
            if sid:
                adv.FreeSid(sid)
                cleanup_hr = userenv.DeleteAppContainerProfile(profile)
        stdout = (output_dir / "stdout.txt").read_text(encoding="utf-8", errors="replace") if (output_dir / "stdout.txt").exists() else ""
        stderr = (output_dir / "stderr.txt").read_text(encoding="utf-8", errors="replace") if (output_dir / "stderr.txt").exists() else ""
        if "PH_SANDBOX AppContainer=true" not in stdout:
            status = "failed"
            stderr += "\n隔离自检未通过或 Python 未能启动；没有使用非隔离后端。"
        return {"status": status, "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
                "elapsed_seconds": round(time.monotonic() - started, 3), "output": str(output_dir),
                "backend": "Windows AppContainer + no capabilities + Job Object", "profile_deleted": cleanup_hr == 0}
