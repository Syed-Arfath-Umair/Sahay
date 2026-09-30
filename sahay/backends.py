"""AI backends. All use plain HTTP (requests) - no heavy SDKs, so everything installs
cleanly on Windows ARM64 (Snapdragon).

Each backend implements:  chat(system: str, user: str, image_png: bytes | None) -> str

Cloud backends are self-healing: providers retire model names regularly, so if a model
is unavailable Sahay tries the next preferred one and, if needed, asks the provider which
models the key can use (model discovery). The model that works is remembered.
"""
from __future__ import annotations

import base64
import re

import requests

TIMEOUT_LOCAL = 180
TIMEOUT_CLOUD = 60


class BackendError(RuntimeError):
    pass


class ModelUnavailable(BackendError):
    pass


class AuthError(BackendError):
    pass


class QuotaError(BackendError):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def classify(status: int, text: str) -> type:
    t = (text or "").lower()
    if status == 401 or any(s in t for s in ("api key not valid", "invalid api key", "incorrect api key",
                                             "invalid x-api-key", "api_key_invalid", "invalid authentication",
                                             "unauthenticated")):
        return AuthError
    if status == 429 or "quota" in t or "rate limit" in t or "resource_exhausted" in t or "insufficient_quota" in t:
        return QuotaError
    if status == 404:
        return ModelUnavailable
    if status in (400, 403) and "model" in t and any(s in t for s in (
            "not found", "does not exist", "not exist", "not supported", "no longer", "not available",
            "deprecated", "limited", "access", "unknown", "invalid model", "permission")):
        return ModelUnavailable
    return BackendError


def _check(r: requests.Response, name: str) -> dict:
    if r.status_code >= 400:
        err = classify(r.status_code, r.text)
        msg = r.text[:300]
        try:
            j = r.json()
            e = j.get("error", j)
            msg = (e.get("message") if isinstance(e, dict) else str(e)) or msg
        except Exception:
            pass
        raise err(f"{name}: {msg} (HTTP {r.status_code})")
    return r.json()


def friendly(e: Exception) -> str:
    if isinstance(e, AuthError):
        return "API key was rejected - check it in Settings."
    if isinstance(e, QuotaError):
        return "Cloud quota or rate limit reached."
    if isinstance(e, requests.Timeout):
        return "Cloud request timed out."
    if isinstance(e, requests.ConnectionError):
        return "No internet connection."
    return str(e)[:160]


class Backend:
    name = "base"
    is_local = False
    supports_vision = True

    def chat(self, system: str, user: str, image_png: bytes | None = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def warmup(self) -> None:
        pass


# ------------------------------------------------------------------ local
class OllamaBackend(Backend):
    is_local = True

    def __init__(self, url: str, model: str, vision: bool = True):
        self.url, self.model, self.supports_vision = url.rstrip("/"), model, vision
        self.name = f"Local - Ollama ({model})"

    def chat(self, system, user, image_png=None):
        msg = {"role": "user", "content": user}
        if image_png is not None and self.supports_vision:
            msg["images"] = [_b64(image_png)]
        try:
            r = requests.post(
                f"{self.url}/api/chat",
                json={"model": self.model, "stream": False, "keep_alive": "30m",
                      "messages": [{"role": "system", "content": system}, msg],
                      "options": {"temperature": 0.2}},
                timeout=TIMEOUT_LOCAL,
            )
        except requests.ConnectionError as e:
            raise BackendError("Local AI is not running. Start Ollama (see README).") from e
        if r.status_code == 404:
            raise ModelUnavailable(f"Model '{self.model}' is not downloaded. Run: ollama pull {self.model}")
        return _check(r, "Ollama")["message"]["content"]

    def warmup(self) -> None:
        """Load the model into memory while the user is still speaking (no prompt = load only)."""
        try:
            requests.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": "30m"}, timeout=120)
        except Exception:
            pass


class OpenAICompatBackend(Backend):
    """Local OpenAI-compatible servers: Microsoft Foundry Local (Snapdragon NPU), LM Studio, llama.cpp."""

    def __init__(self, base_url: str, model: str, api_key: str = "", vision: bool = True, label: str = "On-device"):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.model, self.api_key, self.supports_vision = model, api_key, vision
        self.is_local = True
        self.name = f"Local - {label} ({model})"

    def chat(self, system, user, image_png=None):
        content: list | str = user
        if image_png is not None and self.supports_vision:
            content = [{"type": "text", "text": user},
                       {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(image_png)}"}}]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=headers,
                              json={"model": self.model, "temperature": 0.2,
                                    "messages": [{"role": "system", "content": system},
                                                 {"role": "user", "content": content}]},
                              timeout=TIMEOUT_LOCAL)
        except requests.ConnectionError as e:
            raise BackendError(f"Local AI server not reachable at {self.base_url}") from e
        return _check(r, self.name)["choices"][0]["message"]["content"]


# ------------------------------------------------------------------ cloud
_WORKING: dict = {}  # provider -> model that last worked (this session)


class CloudBackend(Backend):
    provider = ""
    label = ""
    DEFAULTS: list = []

    def __init__(self, api_key: str, model: str = ""):
        self.api_key = api_key.strip()
        self.user_model = (model or "").strip()
        self.model = _WORKING.get(self.provider) or self.user_model or self.DEFAULTS[0]
        self.tried: list = []

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"Cloud - {self.label} ({self.model})"

    def _candidates(self) -> list:
        out = []
        for m in [_WORKING.get(self.provider), self.user_model, *self.DEFAULTS]:
            if m and m not in out:
                out.append(m)
        return out

    def chat(self, system, user, image_png=None):
        self.tried = []
        last: Exception | None = None
        for m in self._candidates():
            try:
                return self._ok(m, self._chat_model(m, system, user, image_png))
            except ModelUnavailable as e:
                self.tried.append(m)
                last = e
        try:
            discovered = [m for m in self.discover() if m not in self.tried][:3]
        except BackendError as e:
            raise e if not isinstance(e, ModelUnavailable) else BackendError(str(last))
        for m in discovered:
            try:
                return self._ok(m, self._chat_model(m, system, user, image_png))
            except ModelUnavailable as e:
                last = e
        raise BackendError(f"No usable {self.label} model for this key. Last error: {last}")

    def _ok(self, model: str, text: str) -> str:
        self.model = model
        _WORKING[self.provider] = model
        return text

    def test(self) -> str:
        """Tiny text-only request. Returns the model that answered."""
        self.chat("Reply with just: OK", "ping", None)
        return self.model

    def _chat_model(self, model, system, user, image_png) -> str:  # pragma: no cover
        raise NotImplementedError

    def discover(self) -> list:  # pragma: no cover
        return []


def _version_key(name: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", name)[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


class GeminiBackend(CloudBackend):
    provider, label = "gemini", "Gemini"
    DEFAULTS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite",
                "gemini-flash-latest", "gemini-2.5-flash"]
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def _chat_model(self, model, system, user, image_png):
        parts: list = [{"text": user}]
        if image_png is not None:
            parts.insert(0, {"inline_data": {"mime_type": "image/png", "data": _b64(image_png)}})
        body = {"system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}]}
        if model.startswith("gemini-3"):
            body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": "low"}}  # faster replies
        r = requests.post(f"{self.BASE}/models/{model}:generateContent",
                          headers={"x-goog-api-key": self.api_key}, json=body, timeout=TIMEOUT_CLOUD)
        if r.status_code == 400 and "thinking" in r.text.lower():
            body.pop("generationConfig", None)
            r = requests.post(f"{self.BASE}/models/{model}:generateContent",
                              headers={"x-goog-api-key": self.api_key}, json=body, timeout=TIMEOUT_CLOUD)
        data = _check(r, "Gemini")
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
                           if not p.get("thought"))
        except (KeyError, IndexError) as e:
            raise BackendError(f"Gemini returned no answer: {str(data)[:200]}") from e

    def discover(self) -> list:
        r = requests.get(f"{self.BASE}/models?pageSize=200", headers={"x-goog-api-key": self.api_key},
                         timeout=TIMEOUT_CLOUD)
        data = _check(r, "Gemini")
        names = []
        for m in data.get("models", []):
            n = m.get("name", "").split("/")[-1]
            meth = m.get("supportedGenerationMethods", [])
            if "generateContent" not in meth or "flash" not in n:
                continue
            if any(x in n for x in ("tts", "image", "embedding", "live", "audio", "thinking", "exp")):
                continue
            names.append(n)
        # newest first, prefer non-lite, non-preview
        names.sort(key=lambda n: (_version_key(n), "lite" not in n, "preview" not in n), reverse=True)
        return names


class OpenAIBackend(CloudBackend):
    provider, label = "openai", "OpenAI"
    DEFAULTS = ["gpt-6-luna", "gpt-6.1-sol", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"]
    BASE = "https://api.openai.com/v1"

    def _chat_model(self, model, system, user, image_png):
        content: list | str = user
        if image_png is not None:
            content = [{"type": "text", "text": user},
                       {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(image_png)}"}}]
        # No temperature: newer reasoning models reject non-default values.
        body = {"model": model, "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": content}]}
        if not model.startswith(("gpt-4", "gpt-3")):
            body["reasoning_effort"] = "low"  # snappier spoken answers
        headers = {"Authorization": f"Bearer {self.api_key}"}
        r = requests.post(f"{self.BASE}/chat/completions", headers=headers, json=body, timeout=TIMEOUT_CLOUD)
        if r.status_code == 400 and "reasoning" in r.text.lower():
            body.pop("reasoning_effort", None)
            r = requests.post(f"{self.BASE}/chat/completions", headers=headers, json=body, timeout=TIMEOUT_CLOUD)
        return _check(r, "OpenAI")["choices"][0]["message"]["content"] or ""

    def discover(self) -> list:
        r = requests.get(f"{self.BASE}/models", headers={"Authorization": f"Bearer {self.api_key}"},
                         timeout=TIMEOUT_CLOUD)
        ids = [m.get("id", "") for m in _check(r, "OpenAI").get("data", [])]
        bad = ("audio", "realtime", "tts", "transcribe", "image", "embedding", "search", "moderation",
               "codex", "instruct", "whisper", "dall-e", "davinci", "babbage")
        ids = [i for i in ids if i.startswith("gpt-") and not any(b in i for b in bad)]
        ids.sort(key=lambda i: (any(s in i for s in ("luna", "mini")), _version_key(i)), reverse=True)
        return ids


class AnthropicBackend(CloudBackend):
    provider, label = "anthropic", "Anthropic"
    DEFAULTS = ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-haiku-4-5"]
    BASE = "https://api.anthropic.com/v1"

    def _headers(self):
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}

    def _chat_model(self, model, system, user, image_png):
        content: list = []
        if image_png is not None:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": _b64(image_png)}})
        content.append({"type": "text", "text": user})
        r = requests.post(f"{self.BASE}/messages", headers=self._headers(),
                          json={"model": model, "max_tokens": 1024, "system": system,
                                "messages": [{"role": "user", "content": content}]},
                          timeout=TIMEOUT_CLOUD)
        data = _check(r, "Anthropic")
        return "".join(b.get("text", "") for b in data.get("content", []))

    def discover(self) -> list:
        r = requests.get(f"{self.BASE}/models?limit=100", headers=self._headers(), timeout=TIMEOUT_CLOUD)
        ids = [m.get("id", "") for m in _check(r, "Anthropic").get("data", [])]
        ids.sort(key=lambda i: ("haiku" in i, "sonnet" in i), reverse=True)
        return ids


def make_local(settings) -> Backend:
    if settings.local_engine == "openai_compat":
        return OpenAICompatBackend(settings.local_url, settings.local_model, vision=settings.local_model_has_vision,
                                   label="Foundry Local" if "foundry" in settings.local_url.lower()
                                   or settings.local_model.lower().endswith(("npu", "qnn")) else "On-device")
    return OllamaBackend(settings.local_url, settings.local_model, vision=settings.local_model_has_vision)


def make_cloud(settings, api_key: str) -> CloudBackend:
    cls = {"anthropic": AnthropicBackend, "openai": OpenAIBackend}.get(settings.cloud_provider, GeminiBackend)
    return cls(api_key, settings.cloud_model)
