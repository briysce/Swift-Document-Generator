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

# Descriptor: a 32x32 ink grid, row/column ink profiles, a coarse colour
# histogram, and structural counts.
#
# The first version was a 16x16 grid plus mean colour and aspect, and it was not
# remotely discriminative enough. Under heavy degradation every lockup collapses
# toward a similar horizontal blob, and those descriptors converged: gcm vs
# swift_orange measured cosine 0.9900 and propak vs swift_orange 0.9915, both
# above the 0.985 bar. In a full-corpus sweep that produced real cross-brand
# false recalls — gcm and swift_orange each returned PROPAK's artwork. Emitting
# another company's logo is the one failure this module must never produce, so
# the descriptor now carries marginals, colour distribution and component
# structure, which degradation blurs far less uniformly than a silhouette.
GRID = 32
COLOUR_BINS = 3  # per channel -> 27 cells
VECTOR_SIZE = GRID * GRID + 2 * GRID + COLOUR_BINS**3 + 4

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
MIN_SIMILARITY = 0.995


@dataclass
class Recollection:
    svg: str
    ideality: float
    similarity: float
    source: str
    meta: dict = field(default_factory=dict)


def descriptor(arr: np.ndarray) -> np.ndarray | None:
    """Discriminative, degradation-tolerant fingerprint of a logo.

    Four families of evidence, because a silhouette alone is not enough once
    the source is badly degraded:

      * a 32x32 ink occupancy grid — overall shape;
      * row and column ink profiles — where mass sits along each axis, which
        separates a wide wordmark from a stacked lockup even when both blur to
        similar blobs;
      * a 3x3x3 colour histogram over ink pixels — brand palette, which survives
        compression far better than geometry and differs sharply between brands;
      * structural counts — component count and ink density.
    """
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

    # Axis profiles, normalized so they describe distribution, not size.
    rows = grid.mean(axis=1)
    cols = grid.mean(axis=0)
    rows = rows / (np.linalg.norm(rows) or 1.0)
    cols = cols / (np.linalg.norm(cols) or 1.0)

    # Brand palette: a coarse joint histogram of the ink colours.
    rgb = arr[:, :, :3][ink].astype(np.int32)
    idx = np.minimum(rgb * COLOUR_BINS // 256, COLOUR_BINS - 1)
    flat = idx[:, 0] * COLOUR_BINS**2 + idx[:, 1] * COLOUR_BINS + idx[:, 2]
    hist = np.bincount(flat, minlength=COLOUR_BINS**3).astype(np.float32)
    hist = hist / (hist.sum() or 1.0)

    # Structure: how many separate pieces, and how densely filled.
    try:
        from scipy import ndimage

        _lab, ncomp = ndimage.label(crop, structure=np.ones((3, 3), dtype=int))
    except Exception:
        ncomp = 1
    density = float(crop.mean())
    aspect = (x1 - x0 + 1) / float(y1 - y0 + 1)
    struct = np.array(
        [
            min(ncomp, 64) / 64.0,
            density,
            min(aspect, 8.0) / 8.0,
            min(float(ink.sum()) / float(arr.shape[0] * arr.shape[1]), 1.0),
        ],
        dtype=np.float32,
    )

    # Weight the families so shape cannot drown out palette and structure —
    # shape is exactly what degradation destroys.
    vec = np.concatenate(
        [
            grid.ravel() * 0.6,
            rows * 1.0,
            cols * 1.0,
            hist * 3.0,
            struct * 2.0,
        ]
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
    require_margin: float = 0.004,
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
        res = c.query_points(COLLECTION, query=vec.tolist(), limit=5).points
    except Exception:
        return None
    scored = [(float(getattr(p, "score", 0.0) or 0.0), p) for p in (res or [])]
    scored.sort(key=lambda t: -t[0])
    # Ambiguity guard: if the runner-up is nearly as close, we cannot tell the
    # two apart and must not guess. This is what a plain threshold missed — the
    # false recalls had several near-identical neighbours.
    if len(scored) > 1 and (scored[0][0] - scored[1][0]) < require_margin:
        return None
    for score, p in scored:
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
