"""RL cut-policy inputs, numpy only (shared by training and the PyTorch-free runtime in lite.py)."""
import numpy as np

CUTS = [1, 2, 3, 5, 10, 20, 50]  # how many reranked candidates the LLM is shown


def cut_features(lg):
    """What the policy sees: how peaked the reranker's distribution is (top probabilities, cumulative mass at
    each cut, gaps between the top logits, entropy). No page- or site-specific input, so it transfers."""
    z = np.full(50, -30.0, dtype=np.float32)
    z[: len(lg)] = lg[:50]
    p = np.exp(z - z.max())
    p /= p.sum()
    cum = np.cumsum(p)
    gaps = z[:6][:-1] - z[1:6]
    ent = -(p * np.log(p + 1e-12)).sum()
    return np.concatenate([p[:10], cum[[c - 1 for c in CUTS]], np.clip(gaps, -10, 10) / 10, [ent / 4, z[0] / 10]]).astype(np.float32)
