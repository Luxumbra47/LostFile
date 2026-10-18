"""本地语义向量索引：后台编码文件名、memmap 存储、余弦相似度 Top-K。

数据文件（均在应用数据目录）：
  vectors.bin   N×dim int8 矩阵（文件名去扩展名后的向量，量化存储）
  vec_ids.bin   N int32（对齐的 files.id）
  vec_meta.json {"count", "dim", "capacity", "model"}
构建可断点续跑（vec_done 标记）；查询用分块浮点余弦，不把整个矩阵载入内存。
"""
import heapq
import json
import os

from .workers import BaseBackfill
from . import vecmodel

VEC_BIN = "vectors.bin"
VEC_IDS = "vec_ids.bin"
VEC_META = "vec_meta.json"
TOP_K = 200
_CHUNK = 4096


def vec_paths(cfg) -> dict[str, str]:
    d = cfg.data_dir
    return {"bin": os.path.join(d, VEC_BIN), "ids": os.path.join(d, VEC_IDS),
            "meta": os.path.join(d, VEC_META)}


def _read_meta(cfg) -> dict:
    try:
        with open(vec_paths(cfg)["meta"], "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def _new_meta(cfg, dim: int, model: str = vecmodel.MODEL_NAME,
              capacity: int = 0) -> dict:
    paths = vec_paths(cfg)
    for p in paths.values():
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    return {"count": 0, "dim": int(dim), "capacity": int(capacity),
            "model": model}


class VectorBackfill(BaseBackfill):
    """用 embedding 模型编码文件名（去扩展名），写入 memmap 矩阵。"""

    name = "vec-backfill"
    chunk = 256          # 批量编码，比逐行快且不占内存

    def __init__(self, db, cfg, embedder: vecmodel.Embedder | None = None,
                 top_k: int = TOP_K):
        super().__init__(db)
        self.cfg = cfg
        self._embedder = embedder
        self._mm = None
        self._ids_mm = None
        self._meta = None

    # ---- 前置：加载模型与矩阵 ----

    def start(self) -> None:
        if self._embedder is None:
            self._embedder = vecmodel.get_embedder(self.cfg)
        if self._embedder is None:
            return               # 模型未下载：由设置页引导下载后再启动
        super().start()

    @staticmethod
    def prepare(conn) -> bool:
        return True

    def _ensure_matrix(self):
        cfg = self.cfg
        np = self._embedder._np
        meta = _read_meta(cfg)
        dim = int(self._embedder.dim)
        if not meta or int(meta.get("dim", 0)) != dim:
            total = self._db.count_files()
            capacity = int(total * 1.1) + 1024
            meta = _new_meta(cfg, dim, capacity=capacity)
            with open(vec_paths(cfg)["meta"], "w", encoding="utf-8") as f:
                json.dump(meta, f)
        capacity = max(int(meta.get("capacity", 0)), 1)
        paths = vec_paths(cfg)
        self._meta = meta
        # np.memmap 的 "r+" 不会创建文件：文件不存在/尺寸不足时用 "w+" 建立
        bin_ok = (os.path.isfile(paths["bin"])
                  and os.path.getsize(paths["bin"]) >= capacity * dim)
        self._mm = np.memmap(
            paths["bin"], mode="r+" if bin_ok else "w+", dtype=np.int8,
            shape=(capacity, dim))
        ids_ok = (os.path.isfile(paths["ids"])
                  and os.path.getsize(paths["ids"]) >= capacity * 4)
        self._ids_mm = np.memmap(
            paths["ids"], mode="r+" if ids_ok else "w+", dtype=np.int32,
            shape=(capacity,))

    # ---- 生产者 ----

    def select_pending(self, conn) -> list:
        return conn.execute(
            "SELECT id, name FROM files WHERE vec_done=0"
            " ORDER BY id LIMIT ?",
            (self.chunk,),
        ).fetchall()

    def process(self, conn, rows: list) -> None:
        if self._mm is None:
            self._ensure_matrix()
        np = self._embedder._np
        meta = self._meta
        pos = int(meta.get("count", 0))
        stems = [os.path.splitext(name or "")[0] for _fid, name in rows]
        vecs_f = self._embedder.encode(stems)
        vecs = np.clip(np.rint(vecs_f * 32.0), -128, 127).astype(np.int8)
        for i, (fid, _name) in enumerate(rows):
            if pos + i >= int(meta.get("capacity", 0)):
                break
            self._mm[pos + i] = vecs[i]
            self._ids_mm[pos + i] = int(fid)
        meta["count"] = pos + len(vecs)
        with open(vec_paths(self.cfg)["meta"], "w", encoding="utf-8") as f:
            json.dump(meta, f)
        self._meta = meta

    def mark_done(self, conn, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE files SET vec_done=1 WHERE id IN ({marks})", ids
        )

    # ---- 查询侧 ----

    def semantic_search(self, text: str, k: int = TOP_K) -> list[tuple[int, float]]:
        """按余弦相似度返回 Top-K [(file_id, sim)...]。"""
        np = self._embedder._np
        meta = _read_meta(self.cfg)
        count = int(meta.get("count", 0))
        dim = int(meta.get("dim", 0))
        if count <= 0 or dim <= 0:
            return []
        q = self._embedder.encode([text])[0].astype(np.float32)
        qn = float(np.linalg.norm(q))
        if qn < 1e-9:
            return []
        q = q / qn
        paths = vec_paths(self.cfg)
        mm = np.memmap(
            paths["bin"], mode="r", dtype=np.int8, shape=(count, dim))
        ids = np.memmap(
            paths["ids"], mode="r", dtype=np.int32, shape=(count,))
        heap: list[tuple[float, int]] = []
        for s in range(0, count, _CHUNK):
            e = min(count, s + _CHUNK)
            block = mm[s:e].astype(np.float32)
            dots = block @ q
            norms = np.linalg.norm(block, axis=1)
            sims = dots / np.maximum(norms * qn, 1e-9)
            local = sims.tolist()
            for j, sim in enumerate(local):
                item = (sim, int(ids[s + j]))
                if len(heap) < k:
                    heapq.heappush(heap, item)
                elif sim > heap[0][0]:
                    heapq.heapreplace(heap, item)
        return sorted(((fid, float(sim)) for sim, fid in heap if sim > 0.0),
                      key=lambda x: -x[1])