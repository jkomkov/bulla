"""Ergonomic receipt emission around a consequential action.

``build_action_receipt`` and friends take a fully-assembled ``RecourseEnvelope``
— a six-symbol nested tree (Authority, Bounds, Forum, Recourse, Remedy,
RecourseEnvelope) with required Forum fields. That is the right shape for a
protocol object and the wrong shape for a first receipt. ``wrap_action`` closes
that gap: one call emits a verifiable receipt around a boundary, with sane
recourse defaults, usable as both a context manager and a decorator. The full
envelope path stays available for anyone who needs it.

    # context manager — set a result, add evidence, get the receipt back
    with wrap_action("payments.charge", {"amount": 200, "currency": "USD"},
                     principal="did:web:acme#agent") as act:
        act.set_result("sha256:...")
        act.add_evidence("counterparty_ack", "sha256:...", "counterparty_signed")
    receipt = act.receipt            # dict; verify_receipt(receipt).ok is True

    # decorator — one receipt per call
    @wrap_action("tool.call", {"tool": "github.create_file"})
    def create_file(...):
        ...

A receipt is emitted even when the wrapped body raises: the outcome is marked
``error`` and the exception is re-raised, never swallowed. Signing is optional
(unsigned verifies to the digest rung, matching the CLI default).
"""

from __future__ import annotations

import copy
import functools
import re
from contextlib import ContextDecorator
from typing import Any, Callable

from bulla.action_receipt import build_action_receipt, sign_action_receipt, verify_receipt
from bulla.envelope import (
    Authority,
    Bounds,
    Forum,
    Recourse,
    RecourseEnvelope,
    Remedy,
)

# Honest placeholder defaults: recompute is always an available remedy, and the
# forum fields are present-but-unset so the receipt verifies to the digest rung
# without implying a witnessed forum that does not exist yet. These are the one
# source the CLI (`bulla receipt create`) and the Python path share, so a first
# receipt looks the same either way.
PLACEHOLDER_FORUM_ENDPOINT = "local://recourse-unset"
PLACEHOLDER_FORUM_ROOT = "unanchored:set-a-real-trusted-root"
# Back-compat aliases (previously underscore-private).
_DEFAULT_FORUM_ENDPOINT = PLACEHOLDER_FORUM_ENDPOINT
_DEFAULT_FORUM_ROOT = PLACEHOLDER_FORUM_ROOT
_DEFAULT_CHALLENGE_WINDOW = "P7D"
_WRAPPER_OUTCOME_KEY = "outcome"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def operational_envelope(
    *,
    principal: str | None = None,
    policy: str | None = None,
    scope: str | dict = "unstated",
    rollback_window: str | None = "P7D",
    forum_endpoint: str = _DEFAULT_FORUM_ENDPOINT,
    forum_root: str = _DEFAULT_FORUM_ROOT,
    challenge_window: str = _DEFAULT_CHALLENGE_WINDOW,
    remedies: tuple[Remedy, ...] | None = None,
    retention_class: str = "operational",
    disclosure_class: str = "party",
) -> RecourseEnvelope:
    """A ready-to-use recourse envelope with sane defaults.

    A ``recompute`` remedy is always attached (it is always available and needs
    no external service). Supply ``principal``/``policy`` to bind authority, or
    omit them for an unauthenticated first receipt.
    """
    if remedies is None:
        remedies = (
            Remedy(rung="recompute", verifier="bulla receipt verify", anchor="the receipt"),
        )
    authority = Authority(principal=principal, policy=policy or "policy://unstated") if principal else None
    return RecourseEnvelope(
        authority=authority,
        bounds=Bounds(scope=scope, rollback_window=rollback_window),
        recourse=Recourse(
            challenge_window=challenge_window,
            forum=Forum(log_endpoint=forum_endpoint, trusted_root_ref=forum_root),
            remedies=remedies,
        ),
        retention_class=retention_class,
        disclosure_class=disclosure_class,
        # Executable bounds are a deed-schema v0.3 feature. The wrapper accepts
        # both prose and structured scopes, so select the declared semantics
        # explicitly instead of constructing an invalid v0.2 envelope.
        deed_schema="0.3" if isinstance(scope, dict) else "0.2",
    )


class wrap_action(ContextDecorator):  # noqa: N801 — used as a verb, not a class
    """Emit a receipt around one consequential action.

    Usable as a context manager (``with wrap_action(...) as act:``) or a
    decorator (``@wrap_action(...)``). As a decorator each call gets a fresh
    scope and its own receipt, retrievable via the returned object's
    ``last_receipt`` after the call.
    """

    def __init__(
        self,
        action_type: str,
        subject: dict | None = None,
        *,
        principal: str | None = None,
        policy: str | None = None,
        scope: str | dict = "unstated",
        envelope: RecourseEnvelope | None = None,
        diagnostic_ref: dict | None = None,
        anchor_ref: dict | None = None,
        evidence_refs: tuple[dict, ...] | list[dict] = (),
        signer: Any | None = None,
        producer: dict | None = None,
    ) -> None:
        self.action_type = action_type
        self.subject = dict(subject or {})
        self._envelope = envelope or operational_envelope(
            principal=principal, policy=policy, scope=scope
        )
        self.diagnostic_ref = diagnostic_ref or {"status": "reference", "ref": "self"}
        self.anchor_ref = anchor_ref
        self._base_evidence = list(evidence_refs)
        self.signer = signer
        self._base_producer = dict(producer or {})
        # per-scope mutable state (reset in __enter__)
        self.evidence: list[dict] = []
        self.result_hash: str | None = None
        self.outcome: str = "ok"
        self.receipt: dict | None = None
        self.last_receipt: dict | None = None

    # fresh scope per decorated call
    def _recreate_cm(self) -> "wrap_action":
        fresh = copy.copy(self)
        fresh.evidence = []
        fresh.result_hash = None
        fresh.outcome = "ok"
        fresh.receipt = None
        fresh.last_receipt = None
        return fresh

    def __call__(self, func: Callable) -> Callable:
        """Decorator form: each call runs in a fresh scope; the resulting
        receipt is surfaced on this object's ``last_receipt``."""
        @functools.wraps(func)
        def inner(*args: Any, **kwargs: Any) -> Any:
            scope = self._recreate_cm()
            try:
                with scope:
                    return func(*args, **kwargs)
            finally:
                # The receipt exists after ``with`` exits, including when the
                # body raised. Surface it on both the original scope and the
                # decorated callable; the exception itself is never swallowed.
                self.last_receipt = scope.receipt
                setattr(inner, "last_receipt", scope.receipt)

        setattr(inner, "last_receipt", None)
        return inner

    # -- caller handle -----------------------------------------------------

    def set_result(self, result_hash: str) -> None:
        if not isinstance(result_hash, str) or _SHA256_RE.fullmatch(result_hash) is None:
            raise ValueError("result_hash must be a lowercase sha256:<64 hex> digest")
        self.result_hash = result_hash

    def add_evidence(self, name: str, digest: str, grounding: str = "self_asserted") -> None:
        self.evidence.append({"name": name, "hash": digest, "grounding": grounding})

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "wrap_action":
        self.evidence = []
        self.result_hash = None
        self.outcome = "ok"
        self.receipt = None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.outcome = "error"
        self.receipt = self._emit(error=exc)
        self.last_receipt = self.receipt
        return False  # never suppress the wrapped exception

    def _emit(self, *, error: BaseException | None) -> dict:
        # Outcome and result belong to the recomputable claim, not ``producer``.
        # Producer is mutable provenance and is intentionally excluded from the
        # ActionReceipt content preimage. Binding these only there would let a
        # served receipt change success to failure (or replace the result) while
        # every stored hash continued to verify.
        outcome: dict[str, str] = {"status": self.outcome}
        if self.result_hash is not None:
            outcome["result_hash"] = self.result_hash
        if error is not None:
            outcome["error_type"] = type(error).__name__
        producer = dict(self._base_producer)
        producer["outcome"] = self.outcome
        if error is not None:
            # Do not serialize exception messages by default: they routinely
            # contain credentials, paths, or customer data. The bound error type
            # above preserves the machine-readable outcome without leaking it.
            producer["error_type"] = type(error).__name__
        receipt = build_action_receipt(
            action={
                "type": self.action_type,
                "subject": dict(self.subject),
                _WRAPPER_OUTCOME_KEY: outcome,
            },
            diagnostic_ref=self.diagnostic_ref,
            envelope=self._envelope,
            anchor_ref=self.anchor_ref,
            evidence_refs=tuple(self._base_evidence + self.evidence),
            producer=producer,
        )
        if self.signer is not None:
            receipt = sign_action_receipt(receipt, self.signer)
        return receipt.to_dict()


def receipt_for(action_type: str, subject: dict | None = None, **kwargs: Any) -> dict:
    """One-call convenience: emit and return a receipt dict for a completed
    action, with no body to wrap. Equivalent to opening and closing a
    ``wrap_action`` scope immediately."""
    with wrap_action(action_type, subject, **kwargs) as act:
        pass
    assert act.receipt is not None
    return act.receipt


__all__ = ["wrap_action", "operational_envelope", "receipt_for", "verify_receipt"]
