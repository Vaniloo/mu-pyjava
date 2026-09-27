"""Versioned yes/no decision points with off, shadow and active modes."""

import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol
from urllib.parse import urlparse

from .model import ChatModel


class BooleanJudge(Protocol):
    def answer(self, question: str, state: Dict[str, Any]) -> Optional[bool]:
        ...


@dataclass(frozen=True)
class BooleanJudgment:
    answer: Optional[bool]
    probability: Optional[float] = None


def _laya_judgment(value: Any) -> BooleanJudgment:
    probability = float(value)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("Laya returned an invalid probability")
    answer = True if probability >= 0.8 else False if probability <= 0.2 else None
    return BooleanJudgment(answer, probability)


class ModelBooleanJudge:
    def __init__(self, model: ChatModel):
        self.model = model

    def answer(self, question: str, state: Dict[str, Any]) -> Optional[bool]:
        message = self.model.complete(
            [
                {"role": "developer", "content": "Answer only YES or NO. If uncertain, answer UNKNOWN."},
                {"role": "user", "content": question + "\nState: " + json.dumps(state, ensure_ascii=False)},
            ],
            [],
        )
        text = str(message.get("content") or "").strip().upper()
        return True if text == "YES" else False if text == "NO" else None


class LayaBooleanJudge:
    """Use a locally available, fine-tuned Laya checkpoint for tool.intent."""

    CRITERIA = {
        "false": "The action is unrelated, forbidden, or goes beyond the requested task.",
        "true": "The action is needed to carry out the user's request.",
    }

    def __init__(self, checkpoint: str, device: Optional[str] = None, agent: Any = None):
        if agent is None:
            import laya  # Optional training/inference dependency.
            agent = laya.load(checkpoint, device=device)
        self.agent = agent

    def evaluate(self, question: str, state: Dict[str, Any]) -> BooleanJudgment:
        result = self.agent.predict(state, {
            "intent": {"type": "noul", "instructions": question, "criteria": self.CRITERIA}
        })
        return _laya_judgment(result["answers"]["intent"]["noul"])

    def answer(self, question: str, state: Dict[str, Any]) -> Optional[bool]:
        return self.evaluate(question, state).answer


class LayaHttpBooleanJudge:
    """Connect to a loopback-only Laya service through an SSH tunnel."""

    def __init__(self, base_url: str, timeout: float = 15.0):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The Laya service URL must use local HTTP through an SSH tunnel")
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("The Laya service URL must contain only the local host and port")
        self.url = base_url.rstrip("/") + "/judge"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def evaluate(self, question: str, state: Dict[str, Any]) -> BooleanJudgment:
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"question": question, "state": state}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            result = json.load(response)
        return _laya_judgment(result["probability"])

    def answer(self, question: str, state: Dict[str, Any]) -> Optional[bool]:
        return self.evaluate(question, state).answer


@dataclass(frozen=True)
class DecisionPoint:
    id: str
    version: int
    question: str
    fallback: bool


class DecisionEngine:
    def __init__(self, mode: str = "off", backend: Optional[BooleanJudge] = None, ledger: Optional[Path] = None):
        if mode not in {"off", "shadow", "active"}:
            raise ValueError("Judge mode must be off, shadow or active")
        self.mode = mode
        self.backend = backend
        self.ledger = ledger
        self.last_record: Optional[Dict[str, Any]] = None

    def decide(self, point: DecisionPoint, state: Dict[str, Any]) -> bool:
        answer = None
        probability = None
        failure = None
        started = time.perf_counter()
        if self.mode != "off" and self.backend is not None:
            try:
                if hasattr(self.backend, "evaluate"):
                    judgment = self.backend.evaluate(point.question, state)
                    answer, probability = judgment.answer, judgment.probability
                else:
                    answer = self.backend.answer(point.question, state)
            except Exception as error:  # A judge outage must not crash the agent.
                failure = type(error).__name__
        outcome = answer if self.mode == "active" and answer is not None else point.fallback
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "point": point.id,
            "version": point.version,
            "tool": state.get("tool"),
            "mode": self.mode,
            "answer": answer,
            "probability": probability,
            "outcome": outcome,
            "source": "judge" if self.mode == "active" and answer is not None else "fallback",
            "failure": failure,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self.last_record = record
        if self.ledger is not None:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return outcome
