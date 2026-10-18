"""重复文件检测：blake2b 内容哈希与分组统计（stdlib，零依赖）。"""
import hashlib
import os

SKIP_HASH = "__SKIP__"            # >MAX_HASH_SIZE 的文件：跳过但标记完成
MAX_HASH_SIZE = 2 * 1024 ** 3     # 2GB
HASH_BLOCK = 1024 * 1024


def blake2b_file(path: str, max_size: int = MAX_HASH_SIZE) -> str | None:
    """返回文件内容哈希（blake2b-128 hexdigest，128 位对去重足够且紧凑）。

    - 文件 > max_size：返回 SKIP_HASH（不读内容）；
    - 读取失败：返回 None（调用方自行决定是否重试）。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > max_size:
        return SKIP_HASH
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(HASH_BLOCK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def duplicate_groups(conn, limit: int = 500) -> list[dict]:
    """按哈希聚合重复文件组（按浪费空间降序）。

    返回 [{"hash", "count", "waste", "files": [文件行 dict...]}, ...]。
    只统计 hash_done=1 且未被跳过的行。
    """
    rows = conn.execute(
        "SELECT content_hash, COUNT(*) AS n, SUM(size) AS w FROM files"
        " WHERE hash_done=1 AND content_hash NOT IN ('', ?)"
        " GROUP BY content_hash HAVING n >= 2"
        " ORDER BY w DESC LIMIT ?",
        (SKIP_HASH, int(limit)),
    ).fetchall()
    groups: list[dict] = []
    for h, n, w in rows:
        frows = conn.execute(
            "SELECT path, name, ext, size, mtime, ctime, parent FROM files"
            " WHERE content_hash=? ORDER BY mtime DESC",
            (h,),
        ).fetchall()
        groups.append({
            "hash": h,
            "count": int(n),
            "waste": int(w or 0),
            "files": [
                {"path": r[0], "name": r[1], "ext": r[2], "size": r[3],
                 "mtime": r[4], "ctime": r[5], "parent": r[6]}
                for r in frows
            ],
        })
    return groups


def total_wasted(groups: list[dict]) -> int:
    """每组 n-1 个文件可视为冗余：可释放空间 = SUM(waste - 最小文件)。"""
    total = 0
    for g in groups:
        sizes = sorted(f["size"] for f in g["files"])
        total += sum(sizes[:-1]) if len(sizes) > 1 else 0
    return total