"""Versioned yes/no decision points with off, shadow and active modes."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from .model import ChatModel


class BooleanJudge(Protocol):
    def answer(self, question: str, state: Dict[str, Any]) -> Optional[bool]:
        ...


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

    def decide(self, point: DecisionPoint, state: Dict[str, Any]) -> bool:
        answer = None
        failure = None
        if self.mode != "off" and self.backend is not None:
            try:
                answer = self.backend.answer(point.question, state)
            except Exception as error:  # A judge outage must not crash the agent.
                failure = type(error).__name__
        outcome = answer if self.mode == "active" and answer is not None else point.fallback
        if self.ledger is not None:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "at": datetime.now(timezone.utc).isoformat(),
                "point": point.id,
                "version": point.version,
                "mode": self.mode,
                "answer": answer,
                "outcome": outcome,
                "source": "judge" if self.mode == "active" and answer is not None else "fallback",
                "failure": failure,
            }
            with self.ledger.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return outcome
