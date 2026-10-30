"""PyTorch-free runtime for shipping: onnxruntime + tokenizers + numpy, reading the bundle made by export_onnx.

Same ranking as the training code: fusion of the retriever (CLS embedding, cosine) and the reranker over the
retriever's top-k (z-scored, 1:1), and the RL cut policy (a 2-layer MLP) choosing how many candidates to show.
"""
import json
import os
from pathlib import Path

import numpy as np

from .cutfeat import CUTS, cut_features


def bundle_dir():
    """ELEM_RANK_LITE, else the bundle shipped inside the package, else the repo's dist/ folder."""
    for p in (os.environ.get("ELEM_RANK_LITE"), Path(__file__).parent / "models-lite",
              Path(__file__).resolve().parent.parent / "dist" / "models-lite"):
        if p and (Path(p) / "lite.json").exists():
            return Path(p)
    return None


class LiteRanker:
    name = "fusion-lite"

    def __init__(self, home=None):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.home = Path(home or bundle_dir())
        self.cfg = json.loads((self.home / "lite.json").read_text())
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        self.bi = ort.InferenceSession(str(self.home / "bi.onnx"), so, providers=["CPUExecutionProvider"])
        self.ce = ort.InferenceSession(str(self.home / "ce.onnx"), so, providers=["CPUExecutionProvider"])
        self.tb = Tokenizer.from_file(str(self.home / "bi_tokenizer.json"))
        self.tc = Tokenizer.from_file(str(self.home / "ce_tokenizer.json"))
        for t, n in ((self.tb, self.cfg["bi_max_len"]), (self.tc, self.cfg["ce_max_len"])):
            t.enable_truncation(max_length=n)
            t.enable_padding(pad_id=0, pad_token="[PAD]")
        self.k, self.cache, self.policies = self.cfg["k"], {}, {}

    def _run(self, sess, encs):
        feed = {"input_ids": np.array([e.ids for e in encs], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encs], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encs], dtype=np.int64)}
        return sess.run(None, feed)[0]

    def embed(self, texts, bs=64):
        out = []
        for i in range(0, len(texts), bs):
            v = self._run(self.bi, self.tb.encode_batch(texts[i:i + bs]))
            out.append(v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-12))
        return np.concatenate(out) if out else np.zeros((0, 384), np.float32)

    def first(self, query, docs, key):
        if key not in self.cache:
            self.cache = dict(list(self.cache.items())[-64:])
            self.cache[key] = self.embed(docs)
        q = self.embed([self.cfg["query_prefix"] + query])[0]
        return self.cache[key] @ q

    def logits(self, query, docs, bs=64):
        return np.concatenate([self._run(self.ce, self.tc.encode_batch([(query, d) for d in docs[i:i + bs]]))
                               for i in range(0, len(docs), bs)])

    def rank(self, query, docs, key):
        """Fusion scores for every doc (outside the retriever's top-k: below, in retriever order) and the reranker
        logits over the top-k, for the cut policy."""
        s1 = self.first(query, docs, key)
        top = np.argsort(-s1)[: self.k]
        lg = self.logits(query, [docs[i] for i in top])
        z = lambda x: (x - x.mean()) / (x.std() + 1e-6)
        out = s1 - s1.max() - 1000.0
        out[top] = z(lg) + z(s1[top])
        return out, lg

    def cut(self, lg, lam):
        if lam not in self.policies:
            f = self.home / f"cut_{lam}.npz"
            if not f.exists():
                return None
            self.policies[lam] = dict(np.load(f))
        w = self.policies[lam]
        x = cut_features(np.sort(lg)[::-1])
        h = np.maximum(0, w["net_0_weight"] @ x + w["net_0_bias"])
        return CUTS[int(np.argmax(w["net_2_weight"] @ h + w["net_2_bias"]))]
