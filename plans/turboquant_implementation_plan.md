# TurboQuant Implementation Plan

**Document Version:** 1.0
**Date:** 2026-03-29
**Status:** Planning

---

## 1. Executive Summary

**TurboQuant** is a novel vector quantization algorithm developed by Google Research, presented at ICLR 2026. It achieves near-optimal distortion rates for quantizing high-dimensional vectors while maintaining geometric structure integrity. The algorithm can compress LLM KV caches by **6x with zero accuracy loss** and provides **8x speedup** in attention computation on H100 GPUs.

### Key Capabilities
- **KV Cache Compression:** 3.5 bits per channel (vs 16 bits FP16) = 4.5x+ compression
- **Zero Quality Loss:** Achieves absolute quality neutrality at 3.5 bits/channel
- **Data-Oblivious:** No training/calibration required - works out of the box
- **Near-Optimal:** Within 2.7x factor of theoretical lower bounds

---

## 2. TurboQuant Technical Overview

### 2.1 Core Algorithm

TurboQuant uses a **two-stage approach**:

```
Stage 1 (MSE Optimization):
  Input Vector → Random Rotation → Beta Distribution → Lloyd-Max Scalar Quantization

Stage 2 (Inner Product Correction):
  Residual → QJL Transform (1-bit) → Unbiased Inner Product Estimation
```

### 2.2 Mathematical Foundation

**Stage 1 - MSE Optimal Quantization:**
1. Apply random rotation matrix Π to input vector x
2. Each coordinate follows Beta distribution → converges to N(0, 1/d) in high dimensions
3. Coordinates become nearly independent → apply optimal scalar quantizer per coordinate
4. Pre-computed Lloyd-Max codebooks for bit-widths b = 1, 2, 3, 4

**Stage 2 - QJL Residual Correction:**
1. Compute residual r = x - Q_mse(x)
2. Apply sign(S · r) where S is random Gaussian matrix
3. Dequantization: (√π/2)/d · S^T · sign(S · r)

### 2.3 Performance Metrics

| Bit-width | MSE Distortion | Inner Product Error |
|-----------|---------------|---------------------|
| 1-bit     | 0.36          | 1.57/d              |
| 2-bit     | 0.117         | 0.56/d              |
| 3-bit     | 0.03          | 0.18/d              |
| 4-bit     | 0.009         | 0.047/d             |

---

## 3. Codebase Analysis

### 3.1 Current Architecture

The **MyClaw** agent framework has the following memory and caching components:

```
myclaw/
├── memory.py              # SQLite-backed conversation memory
├── semantic_cache.py      # Embedding-based response caching
├── semantic_memory.py     # User preference learning
├── context_window.py      # Token management (128k+ support)
├── knowledge/
│   ├── db.py             # SQLite FTS5 knowledge storage
│   ├── storage.py        # File-based note storage
│   ├── graph.py          # Knowledge graph traversal
│   └── parser.py         # Note/entity parsing
├── multimodal.py          # Image/video processing
└── agent.py              # Main agent orchestration
```

### 3.2 Vector Storage Locations

| Component | Data Type | Current Storage | TurboQuant Opportunity |
|-----------|-----------|-----------------|------------------------|
| `semantic_cache.py` | Query embeddings (384-1536 dim) | NumPy arrays in memory | **HIGH PRIORITY** |
| `knowledge/db.py` | Entity embeddings | Not implemented | Medium priority |
| `context_window.py` | Message tokens | Text, no vectorization | Low priority |
| `knowledge/parser.py` | Note content | Text extraction | Low priority |

---

## 4. Implementation Strategy

### 4.1 Recommended Implementation Areas

#### Priority 1: Semantic Cache Enhancement
**File:** `myclaw/semantic_cache.py`

**Current State:**
- Stores embeddings as `np.ndarray` (typically float32 = 4 bytes per value)
- Example: 384-dim embedding = 1,536 bytes per entry
- At 256 max entries = 384 KB for embeddings alone

**TurboQuant Integration:**
```python
# New module: myclaw/vector_quantizer.py
class TurboQuant:
    """TurboQuant vector quantization for embeddings."""

    def __init__(self, dimension: int, bits_per_channel: int = 4):
        # Initialize random rotation matrix
        # Load pre-computed Lloyd-Max codebooks
        # Setup QJL projection matrix

    def quantize(self, vectors: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Compress vectors to low-bit representation."""
        # Stage 1: Random rotation + scalar quantization
        # Returns: quantized indices + residual for QJL

    def dequantize(self, indices: np.ndarray, residual: np.ndarray,
                    norms: np.ndarray) -> np.ndarray:
        """Reconstruct vectors from compressed form."""
        # Lookup centroids + QJL correction + denormalize
```

**Memory Savings:**
- 4-bit quantization: 4x reduction (float32 → 4-bit indices)
- Example: 384-dim embedding: 1,536 bytes → 192 bytes (embedded)
- At 256 entries: 384 KB → 48 KB

#### Priority 2: Knowledge Base Embedding Support
**File:** `myclaw/knowledge/db.py`

**Enhancement:** Add optional vector quantization for future embedding-based search

#### Priority 3: Context Compression
**File:** `myclaw/context_window.py`

**Enhancement:** Apply TurboQuant principles for token-aware context summarization

---

## 5. Affected Files

### 5.1 New Files to Create

| File Path | Purpose | Priority |
|-----------|---------|----------|
| `myclaw/vector_quantizer.py` | TurboQuant core implementation | HIGH |
| `myclaw/quantized_cache.py` | Quantized semantic cache wrapper | HIGH |
| `tests/test_vector_quantizer.py` | Unit tests for quantization | HIGH |
| `tests/test_quantized_cache.py` | Integration tests | MEDIUM |

### 5.2 Files to Modify

| File | Changes | Priority |
|------|---------|----------|
| `myclaw/semantic_cache.py` | Add TurboQuant compression option | HIGH |
| `myclaw/__init__.py` | Export new quantizer classes | MEDIUM |
| `myclaw/config.py` | Add quantization config options | MEDIUM |
| `requirements.txt` | Add numpy dependency (if not present) | LOW |

### 5.3 Configuration Changes

**New config options (myclaw/config.py):**
```python
class QuantizationConfig:
    enabled: bool = False
    bits_per_channel: int = 4
    dimension: int = 384  # or "auto"
    apply_to_cache: bool = True
    apply_to_knowledge: bool = False
```

---

## 6. Implementation Phases

### Phase 1: Core Implementation (Week 1)

**Goal:** Implement TurboQuant algorithm

1. Create `vector_quantizer.py` with:
   - `TurboQuantMSE` class for MSE-optimal quantization
   - `TurboQuantProd` class for inner-product optimal quantization
   - Pre-computed Lloyd-Max codebooks for b = 1, 2, 3, 4 bits
   - Random rotation matrix generation via QR decomposition
   - QJL transform implementation

2. Implement key functions:
   - `quantize(vectors)` → compressed representation
   - `dequantize(indices, residual, norms)` → reconstructed vectors
   - `compute_inner_product(quantized_a, quantized_b)` → unbiased estimation

### Phase 2: Cache Integration (Week 2)

**Goal:** Integrate TurboQuant into semantic cache

1. Create `quantized_cache.py` wrapper class
2. Modify `semantic_cache.py`:
   - Add optional `use_quantization: bool` parameter
   - Add `TurboQuant` instance when enabled
   - Compress stored embeddings transparently

3. Maintain backwards compatibility:
   - Default behavior unchanged
   - Quantization opt-in only

### Phase 3: Testing & Optimization (Week 3)

**Goal:** Validate implementation and optimize

1. Unit tests for quantization/dequantization accuracy
2. Memory usage benchmarks
3. Similarity preservation tests
4. Performance profiling

### Phase 4: Knowledge Base Extension (Week 4+)

**Goal:** Add embedding support to knowledge system

1. Add vector columns to knowledge DB
2. Implement embedding generation for notes
3. Add similarity search capability

---

## 7. Technical Specifications

### 7.1 TurboQuant Parameters

```python
TURBOQUANT_PARAMS = {
    # Lloyd-Max codebooks (pre-computed)
    "codebooks": {
        1: [...],  # 2 centroids
        2: [...],  # 4 centroids
        3: [...],  # 8 centroids
        4: [...],  # 16 centroids
    },
    # Beta distribution parameters for high-dim convergence
    "beta_approx": {
        "mean": 0,
        "std": lambda d: 1 / sqrt(d)
    }
}
```

### 7.2 Data Structures

**Quantized Embedding:**
```python
@dataclass
class QuantizedEmbedding:
    indices: np.ndarray          # Shape: (d,) dtype: uint8
    residual_signs: np.ndarray   # Shape: (d,) dtype: int8
    norm: float                  # Original L2 norm
    rotation_seed: int          # For reproducibility
```

**Compression Ratio:**
- Original: d * 4 bytes (float32)
- Quantized: d * b bits + d bits (residual) + 4 bytes (norm)
- Example (d=384, b=4): 1,536 bytes → 224 bytes (6.8x compression)

### 7.3 API Design

```python
class TurboQuantVectorStore:
    """High-level interface for quantized vector operations."""

    def __init__(self, dimension: int = 384, bits: int = 4):
        self.quantizer = TurboQuant(dimension, bits)

    def add(self, vectors: np.ndarray) -> List[str]:
        """Add vectors, returns IDs."""

    def search(self, query: np.ndarray, k: int = 5) -> List[Tuple[str, float]]:
        """Search by inner product similarity."""

    def get(self, id: str) -> np.ndarray:
        """Retrieve and reconstruct vector."""

    def save(self, path: Path):
        """Persist to disk."""

    @classmethod
    def load(cls, path: Path) -> "TurboQuantVectorStore":
        """Load from disk."""
```

---

## 8. Dependencies

### Required
- `numpy>=1.21.0` - Numerical operations

### Optional
- `sentence-transformers` - For embedding generation (already in semantic_cache.py)

### Dev
- `pytest>=7.0` - Testing

---

## 9. Validation Checklist

### Quantization Correctness
- [ ] MSE between original and reconstructed vectors within theoretical bounds
- [ ] Inner product bias < 0.01
- [ ] Cosine similarity preservation > 0.99

### Memory Efficiency
- [ ] Cache memory reduction ≥ 4x at 4-bit
- [ ] No memory leaks in long-running sessions

### Performance
- [ ] Quantization latency < 1ms per embedding
- [ ] Dequantization latency < 0.5ms per embedding
- [ ] Similarity search throughput ≥ 1000 queries/sec

### Backwards Compatibility
- [ ] Existing semantic cache functionality unchanged when quantization disabled
- [ ] No breaking changes to public API

---

## 10. References

- [TurboQuant Paper (arXiv:2504.19874)](https://arxiv.org/abs/2504.19874)
- [Google Research Blog Announcement](https://research.google/blog/)
- [QJL Transform (arXiv:2406.03482)](https://arxiv.org/abs/2406.03482)
- [Lloyd-Max Quantization](https://en.wikipedia.org/wiki/Lloyd_max_algorithm)

---

## 11. Appendix: TurboQuant Algorithm Pseudocode

```
ALGORITHM TurboQuant_MSE(x, Π, C):
    # x: d-dimensional unit vector
    # Π: random rotation matrix (d×d)
    # C: codebook with 2^b centroids

    y ← Π · x                          # Random rotation
    for j in [d]:                      # Per-coordinate
        idx_j ← argmin_k |y_j - C_k|   # Find nearest centroid
        indices_j ← idx_j               # Store b-bit index
    return indices

ALGORITHM TurboQuant_Prod(x, Π, C, S, b):
    # Stage 1: MSE quantize with (b-1) bits
    indices ← TurboQuant_MSE(x, Π, C_{b-1})

    # Dequantize and compute residual
    x̂ ← Π^T · C(indices)              # Reconstruct
    r ← x - x̂                         # Residual

    # Stage 2: QJL on residual
    signs ← sign(S · r)                # d 1-bit values
    norm ← ||r||_2                     # Residual norm

    return (indices, signs, norm)
```
