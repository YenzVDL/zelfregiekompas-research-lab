"""Local-only provider boundary. No cloud endpoints, redirects or environment proxies."""
import asyncio
import ipaddress
import json
import os
import time
import re
import threading
import logging
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse
import httpx
from . import model_policy

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    def __init__(self, message, kind="provider_error", partial="", http_status=None, diagnostics=None):
        super().__init__(message)
        self.kind, self.partial = kind, partial
        self.http_status, self.diagnostics = http_status, diagnostics or {}
        self.request = None


class Cancelled(ProviderError):
    def __init__(self, partial=""):
        super().__init__("Cancelled by researcher", "cancelled", partial)


class Provider(Protocol):
    def models(self) -> list[dict]: ...
    def generate(self, model, system, prompt, settings, cancel, json_mode=False, response_schema=None) -> dict: ...


class Ollama:
    def __init__(self, url=None):
        self.operation_lock = threading.Lock()
        self.configure(url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))

    def configure(self, url):
        url = str(url).strip().rstrip("/")
        p = urlparse(url)
        try:
            address = ipaddress.ip_address(p.hostname or "")
            local = address.is_loopback or (address.is_private and p.hostname in os.getenv("OLLAMA_ALLOWED_HOSTS", "").split(","))
        except ValueError:
            local = p.hostname in {"localhost", "host.docker.internal", *os.getenv("OLLAMA_ALLOWED_HOSTS", "").split(",")}
        if p.scheme != "http" or not local or p.username or p.password or p.query or p.fragment or p.path:
            raise ValueError("Ollama address must be a local HTTP origin (loopback, host.docker.internal, or OLLAMA_ALLOWED_HOSTS). No paths or credentials.")
        try:
            if p.port is not None and not 1 <= p.port <= 65535:
                raise ValueError()
        except ValueError:
            raise ValueError("Invalid Ollama port") from None
        self.url = url

    @staticmethod
    def model_name(name):
        # Require an explicit tag; no implicit latest, registry URL, or cloud alias.
        if not isinstance(name, str) or len(name) > 200 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*:[A-Za-z0-9][A-Za-z0-9_.-]*", name) or ".." in name or "cloud" in name.lower():
            raise ValueError("Enter an exact local model name and tag, e.g. model-name:tag; cloud models are disabled")
        return name

    @staticmethod
    def _local(show, name):
        if not isinstance(show, dict):
            raise ValueError("Invalid model detail response from Ollama")
        return not (show.get("remote_host") or show.get("remote_model") or "cloud" in name.lower())

    @staticmethod
    def _detail(error):
        if isinstance(error, httpx.HTTPStatusError):
            try:
                data = error.response.json()
                return f"HTTP {error.response.status_code}: {data.get('error', error.response.text) if isinstance(data, dict) else data}"
            except ValueError:
                return f"HTTP {error.response.status_code}: {error.response.text[:500]}"
        return str(error) or type(error).__name__

    @classmethod
    def _error(cls, error, partial="", diagnostics=None):
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else (diagnostics or {}).get("http_status")
        detail = cls._detail(error)
        if isinstance(error, httpx.TimeoutException):
            kind = "timeout"
        elif isinstance(error, httpx.ConnectError):
            kind = "connection_failure"
        elif isinstance(error, (ValueError, TypeError, KeyError)):
            kind = "malformed_response"
        elif any(term in detail.lower() for term in ["memory", "failed to load", "error loading", "unable to load", "runner process"]):
            kind = "model_load_failure"
        else:
            kind = "provider_error"
        return ProviderError(detail, kind, partial, status, diagnostics)

    def connection_test(self):
        try:
            with httpx.Client(trust_env=False, timeout=5, follow_redirects=False) as c:
                r = c.get(self.url + "/api/version")
                r.raise_for_status()
                return {"available": True, "address": self.url, "version": r.json()["version"]}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            error = self._error(e)
            error.args = (f"Cannot connect to Ollama at {self.url}: {error}",)
            raise error from e

    def pull(self, name):
        self.model_name(name)
        try:
            with httpx.Client(trust_env=False, timeout=httpx.Timeout(60, connect=5), follow_redirects=False) as c:
                with c.stream("POST", self.url + "/api/pull", json={"model": name, "stream": True}) as r:
                    if not r.is_success:
                        r.read()
                    r.raise_for_status()
                    success = False
                    for line in r.iter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError("Invalid download progress response from Ollama")
                        if event.get("error"):
                            raise ProviderError(f"Install {name}: {event['error']}")
                        success = event.get("status") == "success"
                        yield {k: event[k] for k in ["status", "digest", "total", "completed"] if k in event}
                    if not success:
                        raise ProviderError("Download stream ended without success; retry Install to resume")
            installed = next((m for m in self.models() if m["name"] == name), None)
            if not installed:
                raise ProviderError("Download finished but the exact model could not be verified as installed locally")
            yield {"done": True, "status": "Installed and verified locally", "model": installed}
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            raise ProviderError(f"Install {name} at {self.url}: {self._detail(e)}") from e

    def models(self):
        try:
            with httpx.Client(trust_env=False, timeout=4, follow_redirects=False) as c:
                r = c.get(self.url + "/api/tags")
                r.raise_for_status()
                # Ollama cloud models must never receive research records.
                models = []
                listing = r.json()
                if not isinstance(listing, dict) or not isinstance(listing.get("models"), list):
                    raise ValueError("Invalid installed-model list response from Ollama")
                for m in listing["models"]:
                    if not isinstance(m, dict) or not isinstance(m.get("name"), str):
                        continue
                    try:
                        detail = c.post(self.url + "/api/show", json={"model": m["name"]})
                        detail.raise_for_status()
                        show = detail.json()
                    except (httpx.HTTPError, ValueError):
                        # Fail closed when locality cannot be verified.
                        continue
                    try:
                        if not self._local(show, m["name"]):
                            continue
                    except ValueError:
                        continue
                    models.append({**m, "model_info": show.get("model_info", {}), "capabilities": show.get("capabilities", []), "thinking": show.get("thinking"),
                                   "identity": model_policy.describe({**m, **show}),
                                   "parameter_provenance": "Ollama /api/tags details.parameter_size", "locality": "local /api/show; no remote target"})
                return models
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            error = self._error(e)
            error.args = (f"Local Ollama unavailable at {self.url}: {error}",)
            raise error from e

    def generate(self, model, system, prompt, settings, cancel, json_mode=False, response_schema=None):
        wait_started = time.perf_counter()
        while not self.operation_lock.acquire(timeout=0.1):
            if settings.get("_fail_if_busy"):
                raise ProviderError("Ollama is busy installing or running inference; try again when it finishes", "busy")
            if cancel():
                raise Cancelled()
            if time.perf_counter() - wait_started > settings.get("timeout_seconds", 120):
                raise ProviderError("Timed out waiting for the local inference lock", "timeout")
        try:
            if settings.get("_ollama_url", self.url) != self.url:
                raise ProviderError("Ollama connection changed since this job was queued; restore the original address or create a new job")
            identity = next((m for m in self.models() if m["name"] == model), None)
            if not identity:
                raise ProviderError("Selected model is not installed and verified local; install it explicitly in Models", "model_unavailable")
            if settings.get("_model_digest") and identity.get("digest") != settings["_model_digest"]:
                raise ProviderError("Model digest changed since this job was queued; restore the original model or create a new job")
            result = asyncio.run(self._generate(model, system, prompt, settings, cancel, json_mode, response_schema))
            after = next((m for m in self.models() if m["name"] == model), None)
            if not after or after.get("digest") != identity.get("digest"):
                raise ProviderError("Model digest changed during inference; result discarded")
            result["model_metadata"] = identity
            return result
        except ProviderError as e:
            log.exception("Ollama generation failed address=%s model=%s kind=%s http_status=%s diagnostics=%s", self.url, model, e.kind, e.http_status, e.diagnostics)
            raise
        finally:
            self.operation_lock.release()

    async def _generate(self, model, system, prompt, settings, cancel, json_mode, response_schema=None):
        # Verify local model at inference time too, even for resumed jobs.
        async with httpx.AsyncClient(trust_env=False, timeout=5, follow_redirects=False) as c:
            try:
                show = await c.post(self.url + "/api/show", json={"model": model})
                show.raise_for_status()
                info = show.json()
                if not self._local(info, model):
                    raise ProviderError("Remote/cloud Ollama models are disabled")
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
                raise self._error(e, diagnostics={"stage": "locality_verification", "address": self.url}) from e
        options = {k: settings[k] for k in ["temperature", "seed", "num_predict", "num_ctx", "top_p"] if k in settings and settings[k] is not None}
        stream = settings.get("_stream", True)
        payload = {"model": model, "system": system, "prompt": prompt, "options": options, "stream": stream, "keep_alive": "5m"}
        thinking = info.get("thinking") or {}
        if not isinstance(thinking, dict):
            raise ProviderError("Invalid thinking-control metadata from Ollama", "malformed_response")
        if settings.get("_short_test"):
            values = thinking.get("values")
            if (isinstance(values, list) and False in values) or (values is None and "thinking" in info.get("capabilities", [])):
                payload["think"] = False
        elif settings.get("think") is not None:
            payload["think"] = settings["think"]
        try:
            model_policy.validate_control({**info, "name": model}, payload.get("think"))
        except ValueError as exc:
            raise ProviderError(str(exc), "incompatible_thinking_mode", diagnostics={"model_identity": model_policy.describe(info), "requested_settings": settings}) from exc
        if response_schema is not None:
            if not isinstance(response_schema, dict) or response_schema.get("type") != "object":
                raise ValueError("response_schema must describe a JSON object")
            payload["format"] = response_schema
        elif json_mode:
            payload["format"] = "json"
        effective = {"options": options, "stream": stream, "think": payload.get("think"), "thinking_default": thinking.get("default"), "thinking_controls": thinking.get("values"), "model_identity": model_policy.describe(info), "requested_settings": {k: v for k,v in settings.items() if not k.startswith('_')}, "format": payload.get("format"), "keep_alive": "5m", "timeout_seconds": settings.get("timeout_seconds", 120)}
        diagnostics = {"address": self.url, "model": model, "effective_settings": effective}
        if settings.get("_short_test"):
            log.info("Ollama short inference request %s", {**diagnostics, "payload": payload})
        chunks, final = [], {}
        captured_request = None
        thinking_chars = 0
        answer_chars, answer_excerpt = 0, ''
        started = time.perf_counter()

        def accept(obj):
            nonlocal thinking_chars, answer_chars, answer_excerpt
            if not isinstance(obj, dict):
                raise ValueError("Expected an Ollama JSON object")
            if obj.get("error"):
                raise self._error(RuntimeError(str(obj["error"])), "".join(chunks), diagnostics)
            if not isinstance(obj.get("response", ""), str) or not isinstance(obj.get("thinking", ""), str):
                raise ValueError("Ollama response/thinking fields must be text")
            fragment = obj.get('response','')
            if fragment:
                chunks.append(fragment)
                answer_chars += len(fragment)
                answer_excerpt = (answer_excerpt + fragment)[:2000]
            thinking_chars += len(obj.get("thinking", ""))
            diagnostics.update(thinking_characters=thinking_chars, final_answer_characters=answer_chars,
                               final_answer_excerpt=answer_excerpt, done_reason=obj.get('done_reason'),
                               runtime={key:value for key,value in obj.items() if key not in ['response','thinking','context']})
            if obj.get("done") is True:
                final.update(obj)

        async def consume():
            nonlocal captured_request
            async with httpx.AsyncClient(trust_env=False, timeout=None, follow_redirects=False) as c:
                request = c.build_request("POST", self.url + "/api/generate", json=payload)
                captured_request = {"address": str(request.url), "payload": json.loads(request.content),
                                    "captured_at": datetime.now(timezone.utc).isoformat(), "capture_source": "httpx request immediately before send"}
                r = await c.send(request, stream=True)
                try:
                    if not r.is_success:
                        await r.aread()
                    r.raise_for_status()
                    diagnostics["http_status"] = r.status_code
                    if stream:
                        async for line in r.aiter_lines():
                            if line:
                                accept(json.loads(line))
                    else:
                        await r.aread()
                        if settings.get("_short_test"):
                            # Fixed diagnostic prompt only; never log research response text here.
                            diagnostics["response_excerpt"] = r.text[:2000]
                        accept(r.json())
                finally:
                    await r.aclose()
        task = asyncio.create_task(consume())
        try:
            deadline = started + settings.get("timeout_seconds", 120)
            while not task.done():
                if cancel():
                    raise Cancelled("".join(chunks))
                if time.perf_counter() > deadline:
                    raise ProviderError("Local inference timed out", "timeout", "".join(chunks), diagnostics.get("http_status"), diagnostics)
                await asyncio.wait({task}, timeout=0.1)
            await task
            if not final:
                raise ProviderError("Ollama response ended without done: true", "malformed_response", "".join(chunks), diagnostics.get("http_status"), diagnostics)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            error = self._error(e, "".join(chunks), diagnostics)
            error.request = captured_request
            raise error from e
        except ProviderError as e:
            e.request = captured_request
            raise
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        runtime = {k: v for k, v in final.items() if k not in ["response", "thinking", "context"]}
        diagnostics.update(thinking_characters=thinking_chars, final_answer_characters=len("".join(chunks)), final_answer_excerpt="".join(chunks)[:2000], done_reason=final.get("done_reason"), runtime=runtime)
        if settings.get("_short_test") and not "".join(chunks).strip():
            detail = f"Ollama completed with no answer text (done_reason={final.get('done_reason', 'unknown')}, thinking_characters={thinking_chars})."
            if thinking_chars:
                detail += " The output budget was spent on thinking; this model may not permit disabling it." if final.get("done_reason") == "length" else " Thinking was returned without answer text."
            raise ProviderError(detail, "empty_output", http_status=diagnostics.get("http_status"), diagnostics=diagnostics)
        return {"text": "".join(chunks).strip(), "latency_seconds": time.perf_counter() - started,
                "runtime": runtime, "effective_settings": effective, "diagnostics": diagnostics, "request": captured_request}
