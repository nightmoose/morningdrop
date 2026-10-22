"""Talk to a local model. Ollama by default; any OpenAI-compatible endpoint
(llama.cpp server, vLLM, LM Studio) works too. A deterministic mock backend
keeps tests and CI fully offline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

Messages = List[Dict[str, str]]


@dataclass
class LLMConfig:
    backend: str = "ollama"          # ollama | openai | mock
    base_url: str = "http://localhost:11434"
    model: str = "gemma3:4b"
    temperature: float = 0.2
    timeout_s: float = 300.0
    api_key: Optional[str] = None    # only for openai-compatible servers that want one


class LLM:
    def __init__(self, cfg: LLMConfig, mock: Optional[Callable[[Messages], str]] = None):
        self.cfg = cfg
        self._mock = mock

    def chat(self, messages: Messages, json_schema: Optional[Dict[str, Any]] = None,
             temperature: Optional[float] = None) -> Any:
        """Return the raw provider response (str for ollama/mock, dict for
        openai). AISchemaGate's extract_json understands both shapes."""
        b = self.cfg.backend
        temp = self.cfg.temperature if temperature is None else temperature
        if b == "mock":
            return (self._mock or default_mock)(messages)
        if b == "ollama":
            body: Dict[str, Any] = {
                "model": self.cfg.model,
                "messages": messages,
                "stream": False,
                "format": json_schema or "json",
                "options": {"temperature": temp},
            }
            r = httpx.post(f"{self.cfg.base_url.rstrip('/')}/api/chat", json=body,
                           timeout=self.cfg.timeout_s)
            r.raise_for_status()
            return r.json()["message"]["content"]
        if b == "openai":
            headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
            body = {
                "model": self.cfg.model,
                "messages": messages,
                "temperature": temp,
                "response_format": {"type": "json_object"},
            }
            r = httpx.post(f"{self.cfg.base_url.rstrip('/')}/v1/chat/completions", json=body,
                           headers=headers, timeout=self.cfg.timeout_s)
            r.raise_for_status()
            return r.json()
        raise ValueError(f"unknown llm backend: {b!r}")


def default_mock(messages: Messages) -> str:
    """Stand-in for Gemma that reads the FACTS block and answers in shape.
    Files in FACTS arrive worst-first."""
    facts = json.loads(messages[-1]["content"].split("FACTS:", 1)[1])
    bad = [f for f in facts["files"] if f["failed"] or f["error"]]
    fixes = [{"file": f["file"], "field": v["field"], "kind": "fix_data",
              "suggestion": f"Check upstream {v['field']}: {v['kind']} on {v['count']} row(s)."}
             for f in bad for v in f["top_violations"][:2]]
    if bad:
        w = bad[0]
        line1 = f"{w['file']}: {w['failed']} of {w['rows']} rows failed their contract overnight."
        line2 = f"Start with {w['top_violations'][0]['field']} in {w['file']}." if w["top_violations"] \
            else f"Start with {w['file']}: {w['error']}"
    else:
        line1, line2 = "Every drop passed its contract overnight.", "Nothing to do. Go back to sleep."
    return json.dumps({"status": facts["worst_status"], "line1": line1[:150], "line2": line2[:150],
                       "fixes": fixes})
