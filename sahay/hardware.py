"""Hardware awareness for Snapdragon-powered Windows PCs.

* Detects Snapdragon (Windows on ARM64) and the chip name.
* Detects whether Python runs NATIVELY on ARM64 or under x64 emulation (Prism) - native is
  much faster and is required for the Qualcomm QNN (Hexagon NPU) runtime.
* Picks the best ONNX Runtime execution provider: QNN (NPU) > DirectML (GPU) > CPU.
* Reads power state so Sahay can be lighter on battery saver.
* Finds Microsoft Foundry Local, which serves NPU-optimised (QNN) language models on Snapdragon.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from dataclasses import dataclass, field

IS_WIN = platform.system() == "Windows"
IMAGE_FILE_MACHINE_ARM64 = 0xAA64


@dataclass
class HardwareProfile:
    os: str
    arch: str                 # architecture Python runs as (ARM64 / AMD64)
    native_arch: str          # architecture of the machine
    cpu_name: str
    is_snapdragon: bool
    python_native: bool       # False = x64 Python emulated on ARM64
    providers_available: list = field(default_factory=list)
    best_provider: str = "CPUExecutionProvider"
    accelerator: str = "CPU"

    def summary(self) -> str:
        chip = "Snapdragon" if self.is_snapdragon else "non-Snapdragon"
        return f"{self.cpu_name} ({self.native_arch}, {chip})"

    def details(self) -> list[tuple[str, str, bool]]:
        """(label, value, good) rows for the settings page."""
        rows = [("Processor", self.cpu_name, True),
                ("Platform", f"Windows on {self.native_arch}" if IS_WIN else self.os, True)]
        if self.native_arch == "ARM64":
            rows.append(("Python", "Native ARM64" if self.python_native else
                         "x64 under emulation - install ARM64 Python for full speed", self.python_native))
        rows.append(("AI runtime", self.accelerator, self.best_provider != "CPUExecutionProvider"
                     or not self.is_snapdragon))
        return rows


def _cpu_name() -> str:
    if IS_WIN:
        try:
            import winreg  # type: ignore

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return re.sub(r"\s+", " ", str(name)).strip()
        except Exception:
            pass
    return platform.processor() or platform.machine() or "unknown"


def _native_arch() -> str:
    """Real machine architecture even when Python itself is emulated."""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            proc, native = wintypes.USHORT(), wintypes.USHORT()
            fn = ctypes.windll.kernel32.IsWow64Process2
            if fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(proc), ctypes.byref(native)):
                if native.value == IMAGE_FILE_MACHINE_ARM64:
                    return "ARM64"
                if native.value == 0x8664:
                    return "AMD64"
        except Exception:
            pass
        pa = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")
        if pa:
            return pa.upper()
    return platform.machine().upper()


def _ort_providers() -> list:
    try:
        import onnxruntime as ort  # type: ignore

        return list(ort.get_available_providers())
    except Exception:
        return []


def detect() -> HardwareProfile:
    arch = platform.machine().upper()
    native = _native_arch()
    cpu = _cpu_name()
    forced = os.environ.get("SAHAY_FORCE_SNAPDRAGON") == "1"
    is_sd = forced or "SNAPDRAGON" in cpu.upper() or "QUALCOMM" in cpu.upper() or native == "ARM64"
    providers = _ort_providers()
    best, accel = "CPUExecutionProvider", "CPU (ONNX Runtime not installed)" if not providers else "CPU"
    if "QNNExecutionProvider" in providers:
        best, accel = "QNNExecutionProvider", "Hexagon NPU via Qualcomm QNN"
    elif "DmlExecutionProvider" in providers:
        best, accel = "DmlExecutionProvider", "GPU via DirectML"
    return HardwareProfile(os=f"{platform.system()} {platform.release()}", arch=arch, native_arch=native,
                           cpu_name=cpu, is_snapdragon=is_sd, python_native=(arch == native),
                           providers_available=providers, best_provider=best, accelerator=accel)


def ort_session_options(provider: str) -> tuple[list, list]:
    """Return (providers, provider_options) for onnxruntime.InferenceSession."""
    if provider == "QNNExecutionProvider":
        # HTP = Hexagon Tensor Processor (the NPU). burst = lowest latency.
        return (["QNNExecutionProvider", "CPUExecutionProvider"],
                [{"backend_path": "QnnHtp.dll", "htp_performance_mode": "burst"}, {}])
    if provider == "DmlExecutionProvider":
        return (["DmlExecutionProvider", "CPUExecutionProvider"], [{}, {}])
    return (["CPUExecutionProvider"], [{}])


# ------------------------------------------------------------------ power
def power_state() -> dict:
    """{'on_battery': bool, 'saver': bool, 'percent': int|None}"""
    out = {"on_battery": False, "saver": False, "percent": None}
    if not IS_WIN:
        return out
    try:
        import ctypes

        class SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        s = SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            out["on_battery"] = s.ACLineStatus == 0
            out["saver"] = s.SystemStatusFlag == 1
            out["percent"] = None if s.BatteryLifePercent == 255 else int(s.BatteryLifePercent)
    except Exception:
        pass
    return out


def use_efficiency_qos() -> None:
    """Ask Windows to schedule Sahay's idle background thread on efficiency cores (EcoQoS)."""
    if not IS_WIN:
        return
    try:
        import ctypes

        class PPTS(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        # ProcessPowerThrottling = 4, PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 1
        st = PPTS(1, 1, 1)
        ctypes.windll.kernel32.SetProcessInformation(ctypes.windll.kernel32.GetCurrentProcess(), 4,
                                                     ctypes.byref(st), ctypes.sizeof(st))
    except Exception:
        pass


def use_high_qos() -> None:
    """Undo EcoQoS while actively answering so the pipeline runs on performance cores."""
    if not IS_WIN:
        return
    try:
        import ctypes

        class PPTS(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        st = PPTS(1, 1, 0)
        ctypes.windll.kernel32.SetProcessInformation(ctypes.windll.kernel32.GetCurrentProcess(), 4,
                                                     ctypes.byref(st), ctypes.sizeof(st))
    except Exception:
        pass


# ------------------------------------------------------------------ Foundry Local (NPU LLMs)
def detect_foundry_local(timeout: float = 12.0) -> dict:
    """Find a running Microsoft Foundry Local service and its models.
    Returns {'ok': bool, 'url': str, 'models': [ids], 'message': str}."""
    import requests

    res = {"ok": False, "url": "", "models": [], "message": ""}
    try:
        p = subprocess.run(["foundry", "service", "status"], capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        res["message"] = "Foundry Local is not installed (winget install Microsoft.FoundryLocal)."
        return res
    except Exception as e:
        res["message"] = f"Could not query Foundry Local: {e}"
        return res
    m = re.search(r"https?://(?:127\.0\.0\.1|localhost):\d+", text)
    if not m:
        res["message"] = "Foundry Local service isn't running. Run: foundry model run phi-4-mini"
        return res
    base = m.group(0) + "/v1"
    res["url"] = base
    try:
        r = requests.get(base + "/models", timeout=5)
        ids = [d.get("id", "") for d in r.json().get("data", [])]
    except Exception:
        ids = []
    ids.sort(key=lambda i: ("npu" in i.lower() or "qnn" in i.lower()), reverse=True)
    res["models"] = ids
    res["ok"] = bool(ids)
    res["message"] = (f"Found {len(ids)} model(s)" + (" - NPU model selected" if ids and
                      ("npu" in ids[0].lower() or "qnn" in ids[0].lower()) else "")) if ids else \
        "Foundry Local is running but has no models loaded. Run: foundry model run phi-4-mini"
    return res
