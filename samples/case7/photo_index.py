"""
Case 7: Smart Album — FAISS photo index with face detection.
"""

import glob
import json
import os
import time

import cv2
import numpy as np

try:
    import faiss
except ImportError:
    faiss = None

from config import (
    FEATURE_DIM,
    TOP_K_RESULTS,
    FAISS_INDEX_PATH,
    METADATA_PATH,
    HAAR_SCALE_FACTOR,
    HAAR_MIN_NEIGHBORS,
)


class PhotoIndex:
    """Manages a FAISS-backed vector index of photo features.

    Each photo is represented by:
      - A 2048-dim L2-normalized feature vector (from ResNet50)
      - Metadata: filename, filepath, face_count, indexed_at
    """

    def __init__(self, feature_extractor, dim=None):
        dim = dim or FEATURE_DIM
        self._extractor = feature_extractor
        self._dim = dim
        self._metadata = []
        self._index = None
        self._face_cascade = None
        self._init_index()
        self._init_face_detector()

    def _init_index(self):
        if faiss is None:
            raise ImportError("faiss-cpu is required: pip install faiss-cpu")
        self._index = faiss.IndexFlatIP(self._dim)

    def _init_face_detector(self):
        cascade_path = (cv2.data.haarcascades
                        + "haarcascade_frontalface_default.xml")
        if os.path.exists(cascade_path):
            self._face_cascade = cv2.CascadeClassifier(cascade_path)
        else:
            print("[PhotoIndex] WARNING: Haar cascade not found, face "
                  "detection disabled")

    # -- indexing -----------------------------------------------------------

    def index_photos(self, photo_dir, progress_callback=None):
        """Scan photo_dir, extract features, build FAISS index.

        Args:
            photo_dir: path to directory containing image files
            progress_callback: callable(current, total) for progress updates

        Returns:
            (indexed_count, skipped_count, elapsed_seconds)
        """
        extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        files = []
        for ext in extensions:
            files.extend(glob.glob(os.path.join(photo_dir, "*" + ext)))
            files.extend(glob.glob(os.path.join(photo_dir, "*" + ext.upper())))
        files = sorted(set(files))

        if not files:
            print(f"[PhotoIndex] No images found in {photo_dir}")
            return 0, 0, 0.0

        t0 = time.time()
        indexed = 0
        skipped = 0

        for i, filepath in enumerate(files):
            try:
                img = cv2.imread(filepath)
                if img is None:
                    skipped += 1
                    continue

                # Detect faces
                face_count = self._detect_faces(img)

                # Extract feature
                vec = self._extractor.extract(img)

                # Add to index
                self._index.add(np.expand_dims(vec, axis=0))

                # Store metadata
                self._metadata.append({
                    "filename": os.path.basename(filepath),
                    "filepath": filepath,
                    "face_count": face_count,
                    "indexed_at": time.time(),
                })

                indexed += 1

            except Exception as exc:
                print(f"[PhotoIndex] Skipping {filepath}: {exc}")
                skipped += 1

            if progress_callback:
                progress_callback(i + 1, len(files))

        elapsed = time.time() - t0
        print(f"[PhotoIndex] Indexed {indexed} photos, skipped {skipped}, "
              f"took {elapsed:.1f}s")
        return indexed, skipped, elapsed

    def _detect_faces(self, image_bgr):
        """Count faces using OpenCV Haar Cascade."""
        if self._face_cascade is None:
            return 0
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        faces = self._face_cascade.detectMultiScale(
            gray, HAAR_SCALE_FACTOR, HAAR_MIN_NEIGHBORS
        )
        return len(faces)

    # -- search --------------------------------------------------------------

    def search(self, query_image_bgr, k=None):
        """Find k most visually similar photos.

        Args:
            query_image_bgr: BGR numpy array (H,W,3)
            k: number of results (default TOP_K_RESULTS)

        Returns:
            list of dicts: [{filename, filepath, face_count, score}, ...]
        """
        k = k or TOP_K_RESULTS
        if self._index.ntotal == 0:
            return []

        query_vec = self._extractor.extract(query_image_bgr)
        query_vec = np.expand_dims(query_vec, axis=0)

        scores, indices = self._index.search(query_vec, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._metadata):
                continue
            meta = self._metadata[idx]
            results.append({
                "filename": meta["filename"],
                "filepath": meta["filepath"],
                "face_count": meta["face_count"],
                "score": float(score),
            })
        return results

    # -- filtering -----------------------------------------------------------

    def get_all_photos(self):
        """Return all indexed photo metadata sorted by filename."""
        return sorted(self._metadata, key=lambda m: m.get("filename", ""))

    def get_photos_by_face_count(self, min_faces=1, max_faces=None):
        """Filter photos by face count range."""
        results = []
        for m in self._metadata:
            fc = m.get("face_count", 0)
            if fc >= min_faces and (max_faces is None or fc <= max_faces):
                results.append(m)
        return results

    # -- persistence ---------------------------------------------------------

    def save(self, index_path=None, metadata_path=None):
        index_path = index_path or FAISS_INDEX_PATH
        metadata_path = metadata_path or METADATA_PATH

        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        if faiss is not None:
            faiss.write_index(self._index, index_path)

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(self._metadata, f, ensure_ascii=False, indent=2)

    def load(self, index_path=None, metadata_path=None):
        index_path = index_path or FAISS_INDEX_PATH
        metadata_path = metadata_path or METADATA_PATH

        if os.path.exists(index_path) and faiss is not None:
            self._index = faiss.read_index(index_path)
        if os.path.exists(metadata_path):
            with open(metadata_path, encoding="utf-8") as f:
                self._metadata = json.load(f)

    # -- stats ---------------------------------------------------------------

    @property
    def size(self):
        return self._index.ntotal if self._index else 0

    def stats(self):
        photos_with_faces = sum(
            1 for m in self._metadata if m.get("face_count", 0) > 0
        )
        return {
            "total_photos": self.size,
            "feature_dim": self._dim,
            "photos_with_faces": photos_with_faces,
        }
