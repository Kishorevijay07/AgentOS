from __future__ import annotations

import json
import logging
import re
from typing import Any

from reflection.models import ProposedTask, ReflectionDecision, ReflectionVerdict

logger = logging.getLogger("agentos.reflection")

# Matches a ```json … ``` or ``` … ``` fenced block; group 1 is the inner body.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class ReflectionParser:
    """
    Parses raw reflector output into a :class:`ReflectionDecision`.

    Reuses the tolerant JSON-envelope extraction the planner uses (fenced blocks,
    surrounding prose), but with one crucial difference in failure policy:
    reflection is **advisory**, so anything unparseable degrades to an
    ``ACCEPT`` decision rather than raising. A malformed reflection must never
    break a run — the worst case is "we didn't replan this time".
    """

    def parse(self, raw: str) -> ReflectionDecision:
        try:
            payload = self._extract_json(raw)
            return self._build(payload)
        except Exception as exc:  # noqa: BLE001 — fail open, never crash the loop
            logger.warning("Reflection output unparseable (%s); accepting.", exc)
            return ReflectionDecision.accept(reason="unparseable reflection output")

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _extract_json(self, raw: str) -> Any:
        if raw is None or not raw.strip():
            raise ValueError("empty reflection output")
        candidate = raw.strip()
        fenced = _FENCE_RE.search(candidate)
        if fenced:
            candidate = fenced.group(1).strip()
        else:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start != -1 and end > start:
                candidate = candidate[start : end + 1]
        return json.loads(candidate)

    def _build(self, payload: Any) -> ReflectionDecision:
        if not isinstance(payload, dict):
            raise ValueError("reflection payload is not a JSON object")

        verdict_raw = str(payload.get("verdict", "accept")).lower().strip()
        verdict = (
            ReflectionVerdict.REPLAN
            if verdict_raw == "replan"
            else ReflectionVerdict.ACCEPT
        )
        reason = str(payload.get("reason", ""))

        new_tasks = []
        if verdict == ReflectionVerdict.REPLAN:
            for item in payload.get("new_tasks", []) or []:
                if isinstance(item, str):
                    new_tasks.append(ProposedTask(description=item))
                elif isinstance(item, dict):
                    desc = item.get("description") or item.get("task")
                    if not desc or not str(desc).strip():
                        continue
                    new_tasks.append(
                        ProposedTask(
                            description=str(desc).strip(),
                            capabilities=list(item.get("capabilities", []) or []),
                        )
                    )
            # A REPLAN with no actionable follow-ups is just an ACCEPT.
            if not new_tasks:
                return ReflectionDecision.accept(reason=reason or "no follow-ups proposed")

        return ReflectionDecision(verdict=verdict, reason=reason, new_tasks=new_tasks)
