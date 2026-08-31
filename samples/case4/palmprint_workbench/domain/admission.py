"""Single production-admission resolver shared by runtime consumers."""

from __future__ import annotations

from .registry import CandidateAdmission, ModelRegistry, ModelSpec


def resolve_runtime_model(
    model_id: str,
    *,
    registry: ModelRegistry | None = None,
    verify_assets: bool = True,
    include_manual_test: bool = False,
) -> ModelSpec:
    """Resolve one admitted runtime model or raise with its gate reason.

    Static production entries are validated by ``runtime_asset_status``.
    Candidate IDs must additionally pass the registry's complete admission
    evidence before they can resolve to a runtime model.
    """

    models = registry or ModelRegistry()
    try:
        spec = models.get_runtime(model_id, include_manual_test=include_manual_test)
    except KeyError as exc:
        decision = models.candidate_admission(model_id, verify_assets=verify_assets)
        if decision.known:
            detail = "; ".join(decision.reasons) or "candidate is not admitted"
            raise PermissionError(f"Model {model_id!r} is not admitted: {detail}") from exc
        raise KeyError(f"Unknown production model: {model_id}") from exc

    # Keep the resolved spec instance for manual-test candidates.  Re-resolving
    # by ID would drop ``include_manual_test`` and make a valid candidate look
    # like an unknown production model on the request path.
    status = models.runtime_asset_status(spec)
    if not status.get("ok", False):
        raise RuntimeError(f"Model {model_id!r} assets are not ready: {status}")
    return spec


def candidate_admission(
    candidate_id: str,
    *,
    registry: ModelRegistry | None = None,
    verify_assets: bool = True,
) -> CandidateAdmission:
    """Return the structured admission result for an audited candidate."""

    return (registry or ModelRegistry()).candidate_admission(
        candidate_id,
        verify_assets=verify_assets,
    )


__all__ = ["CandidateAdmission", "candidate_admission", "resolve_runtime_model"]
