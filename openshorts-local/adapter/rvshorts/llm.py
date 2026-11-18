"""Cliente mínimo para un servidor compatible con OpenAI (Ollama, LM Studio...).

Solo biblioteca estándar: funciona con cualquier Python 3.10+ de Windows sin
instalar nada. Pide JSON y, si el servidor no acepta ``response_format``,
repite la petición sin él (misma escalera que usa OpenShorts en
``llm_backend.py``).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Optional


class LLMError(RuntimeError):
    pass


def _post(url: str, body: dict, timeout: float, api_key: str) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise LLMError(f"No se pudo contactar con el LLM en {url}: {e}") from e


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_text(text: str) -> Any:
    """JSON tal cual, o el primer objeto {...} dentro del texto (quita ```)."""
    text = _FENCE.sub("", (text or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


class LLMClient:
    def __init__(self, base_url: str, model: str, timeout: float = 900.0,
                 api_key: str = "ollama", temperature: float = 0.4):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.api_key = api_key or "ollama"
        self.temperature = temperature

    def ping(self) -> list[str]:
        """Modelos que anuncia el servidor (GET /models)."""
        req = urllib.request.Request(f"{self.base_url}/models",
                                     headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - se informa al usuario tal cual
            raise LLMError(f"Ollama no responde en {self.base_url}: {e}") from e
        return [m.get("id", "") for m in data.get("data", [])]

    def chat_json(self, system: str, prompt: str,
                  validate: Optional[Callable[[Any], Any]] = None,
                  retries: int = 2) -> Any:
        """Una respuesta JSON validada. Reintenta si el modelo devuelve basura."""
        url = f"{self.base_url}/chat/completions"
        last_error: Optional[str] = None
        for attempt in range(retries + 1):
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": prompt}]
            if last_error:
                messages.append({"role": "user", "content":
                                 "Tu respuesta anterior no era válida: " + last_error +
                                 ". Responde SOLO con el objeto JSON corregido."})
            text = self._complete(url, messages)
            try:
                parsed = parse_json_text(text)
                return validate(parsed) if validate else parsed
            except (ValueError, KeyError, TypeError) as e:
                last_error = str(e)[:300]
        raise LLMError(f"El modelo {self.model} no devolvió JSON válido tras "
                       f"{retries + 1} intentos: {last_error}")

    def _complete(self, url: str, messages: list) -> str:
        for fmt in ({"type": "json_object"}, None):
            body = {"model": self.model, "messages": messages,
                    "temperature": self.temperature, "stream": False}
            if fmt:
                body["response_format"] = fmt
            status, text = _post(url, body, self.timeout, self.api_key)
            if fmt and status in (400, 422) and "format" in text.lower():
                continue  # el servidor no conoce response_format: sin él
            if status >= 400:
                raise LLMError(f"LLM HTTP {status}: {text[:300]}")
            data = json.loads(text)
            choices = data.get("choices") or []
            if not choices:
                raise LLMError("Respuesta del LLM sin 'choices'")
            content = (choices[0].get("message") or {}).get("content") or ""
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            return content
        raise LLMError("El servidor rechazó todas las variantes de response_format")
