"""Engine memory — recognize a mark the engine has already solved.

Why
---
The engine currently re-derives everything from pixels on every run. That is
wasteful and, worse, non-deterministic in a way that matters commercially: the
same customer logo arriving twice, at two different resolutions from two
different sources, can reconstruct two slightly different ways. A print shop
noticing that the logo on this week's BOL is a hair different from last week's
is a real problem, and no amount of per-run tuning fixes it.

So remember. Each reconstruction stores a perceptual descriptor of the *input*
alongside the SVG that was produced and how good it scored. When a new input
comes in close enough to something already solved, and the stored answer scored
better than what we would produce now, reuse it. Same mark in, same artwork out.

Why Qdrant
----------
This is a nearest-neighbour lookup over small vectors, which is exactly what a
vector store is for, and `qdrant-client` runs embedded — an in-process local
path or pure memory, no server, no container. That keeps it in the same
fail-open class as every other optional stage here: absent client, absent
storage directory, or a corrupt collection all degrade to "no memory", never to
a failed run.

Cognee was considered for this and left out. It is an agent memory framework
with a knowledge-graph model and its own extraction pipeline; what the engine
needs is a keyed similarity lookup over descriptors it already computes. The
rest would be weight without lift.

Recall is deliberately conservative
-----------------------------------
A false recall is far worse than a miss: it would emit the *wrong company's
logo*. So the similarity bar is high, the stored ideality must beat what is on
offer now, and the descriptor is computed on the prepared ink silhouette rather
than raw pixels so that resolution and compression do not dominate it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "tools" / "logo_vectorizer" / ".cache" / "memory"
COLLECTION = "logo_reconstructions"

# Descriptor: a 16x16 ink occupancy grid plus aspect and colour summary.
GRID = 16
VECTOR_SIZE = GRID * GRID + 4

# A recall must be at least this similar. Deliberately strict — emitting
# another company's logo is unrecoverable.
#
# The margin here is genuinely narrow and should not be loosened without
# re-measuring. Against a stored `swift_orange`, the nearest WRONG answer is its
# own sibling variant `swift_orange_solid` at cosine 0.9823 — the two lockups
# share a silhouette and differ mainly in the shadow. Everything unrelated sits
# far below (gcm 0.4773, arc 0.5067, propak 0.7535, trialta 0.7967). So 0.985
# clears the true confusable by about 0.003, and dropping the bar to 0.98 would
# start returning the wrong variant of our own logo.
MIN_SIMILARITY = 0.985


@dataclass
class Recollection:
    svg: str
    ideality: float
    similarity: float
    source: str
    meta: dict = field(default_factory=dict)


def descriptor(arr: np.ndarray) -> np.ndarray | None:
    """Resolution-tolerant fingerprint of a logo's ink and palette."""
    if arr.ndim != 3 or arr.shape[2] < 4:
        return None
    ink = arr[:, :, 3] >= 128
    if ink.sum() < 64:
        return None
    ys, xs = np.where(ink)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    crop = ink[y0 : y1 + 1, x0 : x1 + 1]
    grid = np.asarray(
        Image.fromarray((crop.astype(np.uint8) * 255), "L").resize(
            (GRID, GRID), Image.Resampling.BILINEAR
        ),
        dtype=np.float32,
    ) / 255.0

    rgb = arr[:, :, :3][ink].astype(np.float32)
    mean = rgb.mean(axis=0) / 255.0
    aspect = (x1 - x0 + 1) / float(y1 - y0 + 1)
    vec = np.concatenate(
        [grid.ravel(), mean, np.array([min(aspect, 8.0) / 8.0], dtype=np.float32)]
    ).astype(np.float32)
    n = float(np.linalg.norm(vec))
    return vec / n if n > 1e-6 else None


def _client(path: Path | None = None):
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
    except Exception:
        return None
    dest = path or STORE
    try:
        dest.mkdir(parents=True, exist_ok=True)
        c = QdrantClient(path=str(dest))
        names = {x.name for x in c.get_collections().collections}
        if COLLECTION not in names:
            c.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        return c
    except Exception:
        return None


def remember(
    arr: np.ndarray,
    svg: str,
    ideality: float,
    *,
    source: str = "",
    meta: dict | None = None,
    path: Path | None = None,
) -> bool:
    """Store a reconstruction. Returns False (never raises) if unavailable."""
    vec = descriptor(arr)
    if vec is None or not svg:
        return False
    c = _client(path)
    if c is None:
        return False
    try:
        from qdrant_client.models import PointStruct

        # Stable id from the artwork itself, so re-storing the same mark
        # updates the entry instead of piling up near-duplicates.
        digest = hashlib.sha256(vec.tobytes()).hexdigest()[:16]
        pid = int(digest, 16) % (2**63)
        c.upsert(
            COLLECTION,
            [
                PointStruct(
                    id=pid,
                    vector=vec.tolist(),
                    payload={
                        "svg": svg,
                        "ideality": float(ideality),
                        "source": source,
                        "meta": json.dumps(meta or {}),
                    },
                )
            ],
        )
        return True
    except Exception:
        return False


def recall(
    arr: np.ndarray,
    *,
    min_similarity: float = MIN_SIMILARITY,
    better_than: float = 0.0,
    path: Path | None = None,
) -> Recollection | None:
    """Best stored reconstruction for this input, or None.

    `better_than` is the ideality the engine would produce right now; a stored
    answer is only worth reusing if it beats that. Recall never lowers quality.
    """
    vec = descriptor(arr)
    if vec is None:
        return None
    c = _client(path)
    if c is None:
        return None
    try:
        res = c.query_points(COLLECTION, query=vec.tolist(), limit=3).points
    except Exception:
        return None
    for p in res or []:
        score = float(getattr(p, "score", 0.0) or 0.0)
        if score < min_similarity:
            continue
        payload = getattr(p, "payload", None) or {}
        svg = payload.get("svg")
        ideality = float(payload.get("ideality") or 0.0)
        if not svg or ideality <= better_than:
            continue
        try:
            meta = json.loads(payload.get("meta") or "{}")
        except Exception:
            meta = {}
        return Recollection(
            svg=svg,
            ideality=ideality,
            similarity=score,
            source=payload.get("source") or "",
            meta=meta,
        )
    return None


def stats(path: Path | None = None) -> dict:
    c = _client(path)
    if c is None:
        return {"available": False, "count": 0}
    try:
        return {"available": True, "count": int(c.count(COLLECTION).count)}
    except Exception:
        return {"available": True, "count": 0}


__all__ = ["Recollection", "descriptor", "remember", "recall", "stats", "STORE"]
