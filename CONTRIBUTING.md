# 参与贡献

感谢你对 LostFile 的关注！欢迎任何形式的贡献：报告问题、改进文档、修复缺陷、实现新功能。

## 开发环境

- Windows 10/11（64 位）
- Python 3.10+（推荐 3.12）
- Git

```powershell
git clone <repo-url> LostFile
cd LostFile
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

依赖见 `requirements.txt`：PySide6-Essentials、pyinstaller、pytest、pypinyin、httpx、numpy、onnxruntime（后面两者仅用于**默认关闭**的可选本地语义搜索）。pip 缓存可通过 `PIP_CACHE_DIR` 环境变量重定向。

## 提交前检查

```powershell
.\scripts\test.ps1     # 全部测试必须通过（当前 160 项 + 1 项条件跳过）
.\scripts\build.ps1    # 构建必须成功（测试不通过会自动中止构建）
```

## 代码约定

- 界面与注释使用简体中文，与现有代码保持一致
- 核心引擎（`lostfile/db.py`、`indexer.py`、`query.py`、`nl.py`）与 Qt 解耦，新增逻辑优先写成可无界面测试的纯函数
- 新增或修改解析规则（`nl.py`）必须同步补充 `tests/test_nlparser.py` 用例
- 不对用户文件做写入、移动操作；删除能力只能走“移入 Windows 回收站 + 用户确认”，不得永久删除
- 不新增网络请求代码；确需联网的功能必须是**默认关闭、用户显式确认后才生效**，且只发送用户输入的关键词/查询词，不得发送文件名、路径或索引内容（现有唯一先例：「在线搜索 GitHub」）

## 测试策略

- `tests/test_indexer.py`：扫描、增量删除感知、Unicode、超长路径、排除、取消/暂停
- `tests/test_search.py`：类型/大小/时间/位置/组合/排序/截断/分页
- `tests/test_nlparser.py`：规格书全部例句 + 无法理解时的诚实行为
- `tests/test_integration.py`：扫描→解析→搜索全链路 + 原始文件不被修改
- `tests/test_ui_smoke.py`：offscreen 主窗口/引导/设置/结果区冒烟
- `tests/test_fts.py`：FTS5 trigram 与旧 LIKE 实现的等价性对拍
- `tests/test_content.py`：正文提取（16KB 上限）、依赖目录跳过、命中分组
- `tests/test_ai.py`：AI 解析契约、名称线索合并与失败回退
- `tests/test_dedup.py` / `tests/test_vec.py` / `tests/test_secrets.py`：重复检测、语义向量、DPAPI 加解密
- `tests/test_fileops.py` / `tests/test_db.py` / `tests/test_drives.py`：文件操作、库迁移、盘符探测

## 提交流程

1. Fork 仓库并创建功能分支
2. 提交小而聚焦的改动，提交信息说明动机（为什么改）
3. 确保 `scripts\test.ps1` 全绿
4. 发起 Pull Request，说明改动内容与验证方式

## 报告问题

请尽量包含：Windows 版本、复现步骤、期望与实际行为、（可选）`data/` 目录之外的日志与截图。**不要上传你的索引数据库或任何个人文件路径。**

## 行为准则

保持友善、建设性的交流；尊重隐私——不要在 Issue/PR 中粘贴真实个人文件路径。