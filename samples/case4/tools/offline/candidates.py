"""Offline CLI wrapper for the shared candidate-manifest domain module.

Candidate metadata is a domain contract used by the production admission
resolver and by offline validation.  The implementation lives in
``palmprint_workbench.domain.candidates``; this module keeps the documented
offline command and compatibility imports without making the production API
depend on the offline tool namespace.
"""

from __future__ import annotations

from palmprint_workbench.domain.candidates import (  # noqa: F401
    CANDIDATE_MANIFEST_PATH,
    CandidateManifest,
    CandidateSpec,
    main,
    validate_candidate_manifest,
    validate_candidate_manifest_payload,
)

__all__ = [
    "CANDIDATE_MANIFEST_PATH",
    "CandidateManifest",
    "CandidateSpec",
    "main",
    "validate_candidate_manifest",
    "validate_candidate_manifest_payload",
]


if __name__ == "__main__":
    raise SystemExit(main())
