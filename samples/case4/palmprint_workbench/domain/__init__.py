"""Pure domain package.

Submodules are imported explicitly so metadata-only tools can validate the
registry without importing OpenCV or other board image dependencies.
"""

from .admission import candidate_admission, resolve_runtime_model
from .candidates import CandidateManifest
from .registry import CandidateAdmission, CandidateSpec, ModelRegistry, ModelSpec

__all__ = [
    "CandidateAdmission",
    "CandidateManifest",
    "CandidateSpec",
    "ModelRegistry",
    "ModelSpec",
    "candidate_admission",
    "resolve_runtime_model",
]
