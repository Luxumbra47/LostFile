"""bge-small-zh-v1.5 embedding 模型：下载、WordPiece 分词与 ONNX 推理。

onnxruntime 与 numpy 都是懒加载：未启用语义搜索时不增加启动成本。
量化模型输出 int8 + scale，推理侧还原为 float 后做 mean-pooling。
"""
import json
import os
import re

MODEL_NAME = "bge-small-zh-v1.5"
MAX_LEN = 512

# 下载清单：量化模型 + 词表（tokenizer.json 为慢速 tokenizer，本项目用
# vocab.txt 自实现 WordPiece，不下载）。FP32 为量化模型不可用时的回退。
DOWNLOAD_FILES = [
    ("model_quantized.onnx", 24 * 1024 * 1024),
    ("vocab.txt", 110 * 1024),
]
FALLBACK_FILES = [
    ("model.onnx", 95 * 1024 * 1024),
    ("vocab.txt", 110 * 1024),
]

MIRRORS = [
    "https://modelscope.cn/api/v1/models/BAAI/bge-small-zh-v1.5/repo"
    "?Revision=master&FilePath={name}",
    "https://hf-mirror.com/BAAI/bge-small-zh-v1.5/resolve/main/{name}",
    "https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/{name}",
]


def model_dir(cfg) -> str:
    return os.path.join(cfg.data_dir, "models", MODEL_NAME)


def model_present(cfg) -> bool:
    d = model_dir(cfg)
    return (os.path.isfile(os.path.join(d, "model_quantized.onnx"))
            or os.path.isfile(os.path.join(d, "model.onnx"))) \
        and os.path.isfile(os.path.join(d, "vocab.txt"))


def download_model(cfg, progress_cb=None, mirror_index: int = 0) -> str | None:
    """下载模型文件到应用数据目录 models/。失败返回错误文本。

    progress_cb(done, total) 用于界面进度；文件先落 .part，全部成功才就位；
    任一步失败清理 .part 并返回错误（下次可重试）。
    """
    try:
        import httpx
    except ImportError:
        return "缺少 httpx 依赖"
    d = model_dir(cfg)
    os.makedirs(d, exist_ok=True)
    for fname, _size in DOWNLOAD_FILES:
        dest = os.path.join(d, fname)
        if os.path.isfile(dest):
            continue
        part = dest + ".part"
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
        last_err = "未知错误"
        for mirror in MIRRORS[mirror_index:]:
            url = mirror.format(name=fname)
            try:
                with httpx.stream("GET", url, timeout=120.0,
                                  follow_redirects=True) as r:
                    if r.status_code != 200:
                        last_err = f"HTTP {r.status_code}"
                        continue
                    with open(part, "wb") as f:
                        for chunk in r.iter_bytes(65536):
                            f.write(chunk)
                            if progress_cb:
                                progress_cb(fname, len(chunk))
                os.replace(part, dest)
                last_err = ""
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
        if last_err:
            try:
                os.remove(part)
            except OSError:
                pass
            return f"下载 {fname} 失败：{last_err}"
    return None


# ---------- WordPiece 分词（基于 vocab.txt 的最小实现） ----------

def load_vocab(path: str) -> dict[str, int]:
    with open(path, "r", encoding="utf-8") as f:
        return {line.rstrip("\n"): i for i, line in enumerate(f)}


_ASCII = set(chr(c) for c in list(range(ord("a"), ord("z") + 1))
             + list(range(ord("0"), ord("9") + 1)))


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF)


def wordpiece_tokenize(text: str, vocab: dict[str, int],
                       max_len: int = MAX_LEN) -> list[int]:
    """BERT 基本分词 + WordPiece。返回模型输入 id 列表（不含特殊符）。"""
    text = (text or "").lower()
    out: list[str] = []
    for token in re.split(r"\s+", text.strip()):
        if not token:
            continue
        buf = ""
        for ch in token:
            if _is_cjk(ch):
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(ch)
            elif ch in _ASCII or ch in "_.-+#":
                buf += ch
            elif buf:
                out.append(buf)
                buf = ""
        if buf:
            out.append(buf)
    ids: list[int] = []
    for t in out[:max_len]:
        if t in vocab:
            ids.append(vocab[t])
            continue
        pieces: list[str] = []
        i = 0
        while i < len(t):
            found = None
            for j in range(len(t), i, -1):
                cand = t[i:j] if i == 0 else "##" + t[i:j]
                if cand in vocab:
                    found = cand
                    break
            if found is None:
                pieces.append(vocab.get("[UNK]", 100))
                break
            pieces.append(vocab[found])
            i = j
        ids.extend(pieces)
        if len(ids) >= max_len - 2:
            break
    return ids[: max_len - 2]


# ---------- 推理 ----------

class Embedder:
    """加载 vocab + ONNX 会话；encode(list[str]) -> float32 (n, dim)。"""

    def __init__(self, base_dir: str):
        import numpy as np
        import onnxruntime as ort
        self._np = np
        self._ort = ort
        self.base_dir = base_dir
        self.vocab = load_vocab(os.path.join(base_dir, "vocab.txt"))
        quant_path = os.path.join(base_dir, "model_quantized.onnx")
        model_path = quant_path if os.path.isfile(quant_path) \
            else os.path.join(base_dir, "model.onnx")
        try:
            self.sess = ort.InferenceSession(
                model_path, providers=["CPUExecutionProvider"])
        except Exception as e:
            raise RuntimeError(f"加载向量模型失败：{e}") from e
        self.input_names = [i.name for i in self.sess.get_inputs()]
        names = [o.name for o in self.sess.get_outputs()]
        self.emb_name = next((n for n in names if n in
                              ("sentence_embedding", "last_hidden_state")), None)
        if self.emb_name is None:
            self.emb_name = names[0] if names else None
        self.scale_name = next(
            (n for n in names if "scale" in n.lower()), None)
        self._dim: int = 0

    @property
    def dim(self) -> int:
        if not self._dim:
            emb = self.sess.run(None, self._feed(["测试"], [self.input_names[0]]))[0]
            self._dim = int(emb.shape[-1])
        return self._dim

    def _feed(self, texts: list[str], names: list[str]) -> dict:
        np = self._np
        rows = []
        for t in texts:
            ids = [self.vocab.get("[CLS]", 101)] + \
                wordpiece_tokenize(t, self.vocab) + \
                [self.vocab.get("[SEP]", 102)]
            rows.append(ids)
        L = max(len(r) for r in rows)
        ids_arr = np.zeros((len(rows), L), dtype=np.int64)
        mask = np.zeros((len(rows), L), dtype=np.int64)
        for i, r in enumerate(rows):
            ids_arr[i, :len(r)] = r
            mask[i, :len(r)] = 1
        out = {}
        for name in names:
            if "attention_mask" in name or "mask" in name:
                out[name] = mask
            elif "token_type" in name or "segment" in name:
                out[name] = np.zeros_like(ids_arr)
            else:
                out[name] = ids_arr
        return out

    def encode(self, texts: list[str]) -> "np.ndarray":
        np = self._np
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        results = self.sess.run(None, self._feed(texts, self.input_names))
        out_names = [o.name for o in self.sess.get_outputs()]
        idx = out_names.index(self.emb_name) if self.emb_name in out_names else 0
        emb = results[idx]
        if emb.dtype == np.int8 and self.scale_name is not None \
                and self.scale_name in out_names:
            scale = results[out_names.index(self.scale_name)]
            emb = emb.astype(np.float32) * np.asarray(scale, dtype=np.float32)
        arr = np.asarray(emb, dtype=np.float32)
        if arr.ndim == 3:          # last_hidden_state：按 mask 均值池化
            mask_arr = None
            for name, val in self._feed(texts, self.input_names).items():
                if val.shape == arr.shape[:2]:
                    mask_arr = np.asarray(val, dtype=np.float32)
                    break
            if mask_arr is not None:
                arr = (arr * mask_arr[:, :, None]).sum(axis=1) \
                    / mask_arr.sum(axis=1, keepdims=True).clip(min=1.0)
            else:
                arr = arr.mean(axis=1)
        return arr


_inst: Embedder | None = None


def get_embedder(cfg) -> Embedder | None:
    """进程内单例；模型缺失返回 None。"""
    global _inst
    if _inst is not None:
        return _inst
    if not model_present(cfg):
        return None
    try:
        _inst = Embedder(model_dir(cfg))
    except Exception:
        _inst = None
    return _inst


def reset_embedder() -> None:
    """删除向量数据后调用：丢弃缓存实例。"""
    global _inst
    _inst = None