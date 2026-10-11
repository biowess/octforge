"""In-memory LRU cache for rendered OCT B-scans and ground-truth geometry.

Stores full RenderEntry objects keyed by a stable SHA-256 hash of the SceneConfig.
Serves as the authoritative render-reference provider for downstream visualization,
annotation, and measurement endpoints without re-triggering scene regeneration.
"""

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import numpy as np

from backend.app.schema import SceneConfig


@dataclass
class RenderEntry:
    """Cached render artifact storing pixels, ground truth, and acquisition geometry."""
    render_id: str
    scene: SceneConfig
    pixels: np.ndarray                 # uint8 shape (H, W)
    layer_id_map: np.ndarray           # int8 shape (H, W)
    boundary_curves: Dict[str, np.ndarray]  # dict of (W,) float64
    vessel_info: List[Dict[str, Any]]
    image_b64: str                     # base64 data URI (data:image/png;base64,...)
    preview: bool
    width: int
    height: int


class RenderCache:
    """LRU cache for rendered scenes and their geometric ground-truth maps."""

    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self._cache: OrderedDict[str, RenderEntry] = OrderedDict()

    @staticmethod
    def compute_hash(scene: SceneConfig, preview: bool = False) -> str:
        """Compute stable SHA-256 hash key for a SceneConfig and render mode."""
        json_str = scene.model_dump_json()
        payload = f"{json_str}:preview={preview}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[RenderEntry]:
        """Retrieve RenderEntry if cached, moving key to end."""
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, entry: RenderEntry) -> None:
        """Store RenderEntry, popping oldest entry if maxsize is exceeded."""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = entry
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def __len__(self) -> int:
        return len(self._cache)


# Global default cache instance
render_cache = RenderCache()
