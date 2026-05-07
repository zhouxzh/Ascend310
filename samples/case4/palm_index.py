"""
Case 4: Smart Palmprint Recognition — FAISS-backed enrollment & verification.

Each enrolled user contributes one or more 1280-dim L2-normalised embedding
vectors.  At verification time the query embedding is compared against the
FAISS index (inner product = cosine similarity), top-k results are
majority-voted, and the match is accepted when the average similarity exceeds
the configured threshold.
"""

import json
import os
import time

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None

from config import (
    FEATURE_DIM,
    FAISS_INDEX_PATH,
    METADATA_PATH,
    TOP_K_RESULTS,
    VERIFICATION_THRESHOLD,
)


class PalmIndex:
    """Manages a FAISS-backed index of enrolled palmprint embeddings."""

    def __init__(self, palm_extractor, dim=None):
        if faiss is None:
            raise ImportError("faiss-cpu is required: pip install faiss-cpu")

        dim = dim or FEATURE_DIM
        self._extractor = palm_extractor
        self._dim = dim
        self._metadata = []  # one dict per FAISS row
        self._index = faiss.IndexFlatIP(dim)

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def enroll(self, image_bgr, user_id, user_name=""):
        """Enroll a single palmprint image for *user_id*.

        Returns True on success, False when ROI extraction fails.
        """
        vec = self._extractor.extract(image_bgr)
        if vec is None:
            return False

        self._index.add(np.expand_dims(vec, axis=0))
        self._metadata.append({
            "user_id": str(user_id),
            "user_name": user_name or str(user_id),
            "enrolled_at": time.time(),
        })
        return True

    def enroll_multiple(self, images_bgr_list, user_id, user_name=""):
        """Enroll several samples for the same user.  Returns count of
        successfully enrolled samples."""
        ok = 0
        for img in images_bgr_list:
            if self.enroll(img, user_id, user_name):
                ok += 1
        return ok

    # ------------------------------------------------------------------
    # Verification / Identification
    # ------------------------------------------------------------------

    def verify(self, image_bgr, k=None):
        """1:N verification against all enrolled users.

        Returns a dict:
            verified      — bool
            user_id       — best-match user (or None)
            user_name     — best-match user name (or None)
            score         — average similarity of the winning user
            top_matches   — list of {user_id, user_name, score} for top-k
            below_threshold — True when a match exists but score is too low
        """
        k = k or TOP_K_RESULTS
        result = {
            "verified": False,
            "user_id": None,
            "user_name": None,
            "score": 0.0,
            "top_matches": [],
            "below_threshold": False,
        }

        if self._index.ntotal == 0:
            return result

        vec = self._extractor.extract(image_bgr)
        if vec is None:
            return result

        query = np.expand_dims(vec, axis=0)
        scores, indices = self._index.search(query, min(k, self._index.ntotal))

        # Build top-k match list
        top_matches = []
        user_scores = {}  # user_id → [scores]
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._metadata):
                continue
            meta = self._metadata[idx]
            uid = meta["user_id"]
            user_scores.setdefault(uid, []).append(float(score))
            top_matches.append({
                "user_id": uid,
                "user_name": meta.get("user_name", uid),
                "score": float(score),
            })
        result["top_matches"] = top_matches

        if not top_matches:
            return result

        # Majority voting: pick the user with the highest mean similarity
        best_uid = max(user_scores, key=lambda u: np.mean(user_scores[u]))
        best_score = np.mean(user_scores[best_uid])

        result["user_id"] = best_uid
        result["score"] = float(best_score)
        # Find user_name for best_uid
        for m in top_matches:
            if m["user_id"] == best_uid:
                result["user_name"] = m["user_name"]
                break

        if best_score >= VERIFICATION_THRESHOLD:
            result["verified"] = True
        else:
            result["below_threshold"] = True

        return result

    def identify(self, image_bgr, k=None):
        """1:N identification — always returns the best match regardless
        of threshold.  Same return shape as verify()."""
        return self.verify(image_bgr, k)

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def remove_user(self, user_id):
        """Remove all embeddings belonging to *user_id*.

        Rebuilds the FAISS index from scratch (O(N) cost, acceptable for
        edge-scale deployments).
        """
        user_id = str(user_id)
        keep = [m for m in self._metadata if m["user_id"] != user_id]
        if len(keep) == len(self._metadata):
            return False

        # Re-extract embeddings for the remaining entries — but we only
        # have metadata, not the original images.  So instead we collect
        # the vectors we want to keep by rebuilding from every enrolled
        # image.  Since we don't store raw images, we rebuild from stored
        # embeddings via FAISS reconstruction.
        #
        # IndexFlatIP stores vectors directly → we can reconstruct.
        kept_vectors = np.zeros((len(keep), self._dim), dtype=np.float32)
        kept_idx = 0
        for i, meta in enumerate(self._metadata):
            if meta["user_id"] == user_id:
                continue
            kept_vectors[kept_idx] = self._index.reconstruct(i)
            kept_idx += 1

        self._index = faiss.IndexFlatIP(self._dim)
        if len(keep) > 0:
            self._index.add(kept_vectors)
        self._metadata = keep
        return True

    def get_users(self):
        """Return deduplicated list of enrolled users."""
        seen = {}
        for m in self._metadata:
            uid = m["user_id"]
            if uid not in seen:
                seen[uid] = {
                    "user_id": uid,
                    "user_name": m.get("user_name", uid),
                    "num_samples": 0,
                }
            seen[uid]["num_samples"] += 1
        return list(seen.values())

    def is_enrolled(self, user_id):
        return any(m["user_id"] == str(user_id) for m in self._metadata)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, index_path=None, metadata_path=None):
        index_path = index_path or FAISS_INDEX_PATH
        metadata_path = metadata_path or METADATA_PATH

        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        faiss.write_index(self._index, index_path)

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(self._metadata, f, ensure_ascii=False, indent=2)

    def load(self, index_path=None, metadata_path=None):
        index_path = index_path or FAISS_INDEX_PATH
        metadata_path = metadata_path or METADATA_PATH

        if os.path.exists(index_path):
            self._index = faiss.read_index(index_path)
        if os.path.exists(metadata_path):
            with open(metadata_path, encoding="utf-8") as f:
                self._metadata = json.load(f)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self):
        users = self.get_users()
        return {
            "total_users": len(users),
            "total_embeddings": self._index.ntotal,
            "feature_dim": self._dim,
        }
