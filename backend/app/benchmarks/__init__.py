"""Stage 4C — advocate model benchmarking.

Evaluation infrastructure only. Nothing in this package is imported by the
production request path: the benchmark drives the *existing* agents, provider
seam, and claim verifier, and never the other way around.

Design constraints:

  * Model choice is configuration, never code. Adding Tencent / DeepSeek /
    Hunyuan means adding a :class:`BenchmarkModelConfig` entry, not editing
    benchmark logic.
  * No plaintext secrets. A config names the environment variable to read; it
    never carries a key.
  * Provider failure and model-output failure are separate metrics. A 503 is
    not a hallucination, and a hallucination is not provider downtime.
"""

from app.benchmarks.models import (
    MODEL_CATALOGUE,
    BenchmarkModelConfig,
    resolve_model_configs,
)

__all__ = [
    "MODEL_CATALOGUE",
    "BenchmarkModelConfig",
    "resolve_model_configs",
]
