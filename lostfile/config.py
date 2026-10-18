"""应用配置：JSON 配置加载/保存、数据目录解析。

数据目录解析顺序：
1. 环境变量 LOSTFILE_DATA_DIR（测试用）；
2. 打包运行时：exe 旁存在 portable.txt → exe 旁 data/（便携模式，默认交付形态）；
3. 打包运行时无 portable.txt → %APPDATA%\\LostFile；
4. 开发模式（源码运行）→ 项目根目录 data/。
"""
import json
import os
import sys

from . import APP_NAME


class AppConfig:
    DEFAULTS = {
        "version": 1,
        "theme": "system",            # light | dark | system
        "keep_index_on_exit": True,   # 退出时是否保留索引
        "recent_import_enabled": False,  # 最近打开导入（默认关闭）
        "exclude_exts": [],           # 索引时排除的扩展名（小写、无点）
        "exclude_dirs": [],           # 索引时排除的目录绝对路径
        "wizard_completed": False,
        "max_results": 500,
        # 显示数量：500/2000/0(不限)，由结果区下拉框设置
        "result_limit": 500,
        # 可选联网 GitHub 搜索（默认关闭；首次开启需用户确认）
        "github_search": False,
        "github_consent": False,
        # 关键词 FTS 加速（默认开启；trigram 索引占用磁盘较大，可在维护页关闭/删除）
        "fts_enabled": True,
        # 正文内容检索（默认关闭；仅本地提取纯文本，进一步提取见维护页）
        "content_search_enabled": False,
        # 重复文件检测（默认关闭；本地计算内容哈希，不存文件内容）
        "dup_detect_enabled": False,
        # 可选联网 AI 解析（默认关闭；手动按钮触发，只发送描述文字）
        "ai_enabled": False,
        "ai_consent": False,
        "ai_base_url": "https://api.deepseek.com/v1",
        "ai_model": "deepseek-chat",
        "ai_api_key_enc": "",   # DPAPI 加密存储的 API Key（base64）
        # 本地语义向量搜索（默认关闭；bge-small-zh 模型首次启用需下载约 24MB）
        "vec_enabled": False,
        "vec_consent": False,
        # 界面状态记忆（退出时写入）：窗口几何、结果区布局、上次搜索词
        "window_geometry": "",
        "results_layout": None,
        "last_query_text": "",
    }

    def __init__(self, data_dir: str | None = None):
        self.data_dir = os.path.abspath(data_dir or default_data_dir())
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "config.json")
        self.values = dict(self.DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            for k, v in data.items():
                if k in self.DEFAULTS:
                    self.values[k] = v

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            # ensure_ascii=True：排除目录等路径可能含非法字符，转义避免崩溃
            json.dump(self.values, f, ensure_ascii=True, indent=2)
        os.replace(tmp, self.path)

    def get(self, key):
        return self.values.get(key, self.DEFAULTS.get(key))

    def set(self, key, value) -> None:
        self.values[key] = value

    def db_path(self) -> str:
        return os.path.join(self.data_dir, "lostfile_index.db")


def app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_data_dir() -> str:
    env = os.environ.get("LOSTFILE_DATA_DIR")
    if env:
        return env
    root = app_root()
    if getattr(sys, "frozen", False):
        if os.path.exists(os.path.join(root, "portable.txt")):
            return os.path.join(root, "data")
        appdata = os.environ.get("APPDATA") or root
        return os.path.join(appdata, APP_NAME)
    return os.path.join(root, "data")