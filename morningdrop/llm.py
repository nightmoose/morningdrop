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

    def chat(self, messages: Messages, json_schema: Optional[Dict[str, Any]] = None) -> Any:
        """Return the raw provider response (str for ollama/mock, dict for
        openai). AISchemaGate's extract_json understands both shapes."""
        b = self.cfg.backend
        if b == "mock":
            return (self._mock or default_mock)(messages)
        if b == "ollama":
            body: Dict[str, Any] = {
                "model": self.cfg.model,
                "messages": messages,
                "stream": False,
                "format": json_schema or "json",
                "options": {"temperature": self.cfg.temperature},
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
                "temperature": self.cfg.temperature,
                "response_format": {"type": "json_object"},
            }
            r = httpx.post(f"{self.cfg.base_url.rstrip('/')}/v1/chat/completions", json=body,
                           headers=headers, timeout=self.cfg.timeout_s)
            r.raise_for_status()
            return r.json()
        raise ValueError(f"unknown llm backend: {b!r}")


def default_mock(messages: Messages) -> str:
    """Stand-in for Gemma that reads the facts block and answers in shape."""
    facts = json.loads(messages[-1]["content"].split("FACTS:", 1)[1])
    files = facts["files"]
    rank = {"red": 0, "yellow": 1, "green": 2}
    bad = sorted((f for f in files if f["failed"] or f["error"]), key=lambda f: (rank[f["status"]], -f["failed"]))
    fixes = []
    for f in bad:
        for v in f["top_violations"][:2]:
            fixes.append({"file": f["file"], "field": v["field"], "kind": "fix_data",
                          "suggestion": f"Check upstream {v['field']}: {v['kind']} on {v['count']} row(s)."})
    status = "red" if any(f["status"] == "red" for f in files) else ("yellow" if bad else "green")
    return json.dumps({
        "status": status,
        "line1": f"{len(files)} drops checked overnight, {len(bad)} need attention.",
        "line2": (f"Start with {bad[0]['file']}." if bad else "Nothing to do. Go back to sleep."),
        "fixes": fixes,
    })
