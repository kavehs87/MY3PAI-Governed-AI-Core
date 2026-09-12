"""Prompt-injection screen applied at the gateway boundary.

Operates on raw prompt text only; it never sees model output and cannot be
reached from agent code. Patterns are deliberately narrow literals to avoid
false positives on benign content while catching canonical bypass phrasings.
"""

from __future__ import annotations

import re

_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+)?(previous|prior|earlier)\s+instructions",
        r"disregard\s+.*(policy|policies|guardrail|restriction|instruction)",
        r"bypass\s+.*(policy|gateway|filter|guardrail|check)",
        r"override\s+.*(safety|policy|guardrail|restriction)",
        r"reveal\s+.*(system\s+prompt|secret|api\s*key)",
        r"exfiltrate",
        r"jailbreak",
        r"\bdo\s+anything\s+now\b",
        r"pretend\s+you\s+have\s+no\s+restriction",
        r"developer\s+mode\s+enabled",
        r"ignore\s+.*safety",
    )
)


def contains_injection(prompt_text: str) -> bool:
    if not prompt_text:
        return False
    clipped = prompt_text[:8192]
    return any(p.search(clipped) is not None for p in _PATTERNS)
