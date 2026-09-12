"""Canonical rule set identity.

RULE_HASH pins the exact rule text evaluated by the gateway. Any rule edit
changes the hash, so decisions remain attributable to the code that produced
them during replay and audit.
"""

from __future__ import annotations

import hashlib
import json

RULE_VERSION = "0.2.0"

_HUMAN_REVIEW_ACTIONS: tuple[str, ...] = ("publish", "train_export", "bulk_export")

_RULE_DOCUMENT: dict[str, object] = {
    "version": RULE_VERSION,
    "order": [
        "unknown_asset",
        "withdrawn",
        "mandate_signature",
        "tenant_isolation",
        "validity_window",
        "prompt_injection",
        "privilege_escalation",
        "action_scope",
        "consent_gate",
        "human_review_actions",
        "default_allow",
    ],
    "human_review_actions": list(_HUMAN_REVIEW_ACTIONS),
    "fail_closed": True,
}


def rule_hash() -> str:
    canonical = json.dumps(_RULE_DOCUMENT, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


RULE_HASH = rule_hash()

HIGH_RISK_ACTIONS: tuple[str, ...] = _HUMAN_REVIEW_ACTIONS
