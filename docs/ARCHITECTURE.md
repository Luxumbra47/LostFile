# LostFile 技术架构与选型说明

## 一、技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.12（本机已装 3.12.5） | 本机唯一可用的成熟开发环境（无 Node/.NET SDK/Rust 工具链）；标准库自带 SQLite |
| GUI | PySide6-Essentials（Qt 6 Widgets） | 成熟桌面 UI 框架，QSS 可实现现代浅/深主题；跨线程信号机制天然适合后台扫描；PyInstaller 打包支持完善 |
| 数据库 | SQLite（Python 内置 sqlite3） | 零运维、单文件、无服务进程；满足本地索引需求；WAL 模式提升并发读写 |
| 打包 | PyInstaller（onedir 模式） | 产出无需管理员权限、免安装的 Windows 程序；onedir 启动快、杀软误报率低于 onefile |
| 测试 | pytest | 标准成熟方案；核心引擎（扫描/索引/搜索/NL 解析）与 UI 分离，可无界面测试 |

### 被否决的方案
- **Electron / Tauri**：本机无 Node、无 Rust 工具链；Electron 体积大，与“轻量”定位冲突。
- **C# WPF / WinUI**：本机无 .NET SDK，系统级安装需用户确认，且 WPF 长期维护成本较高。
- **C++ 原生（Qt/win32）**：开发与测试效率低，不符合“易维护”要求。
- **Everything SDK 集成**：避免重造文件名索引轮子，但 Everything SDK 并非所有用户都安装；MVP 不引入外部二进制依赖（符合“禁止下载来源不明二进制”约束），未来可作为可选加速插件。

## 二、约束落实

- 全本地运行：搜索、解析、索引不依赖网络与任何 API Key；
- 不需要管理员权限：扫描普通目录、SQLite 单文件、便携数据目录（对系统盘之外目录读写天然无 UAC 要求）；
- 便携优先：检测到应用旁 `portable.txt`（或开发模式）时，数据目录使用 `<应用目录>/data`，配置文件、数据库、日志全部在本地；
- 无系统级修改：不写注册表、不改 PATH、不做全局安装。

## 三、模块划分

```
lostfile/
├── __main__.py       入口（python -m lostfile）
├── run.py            QApplication 引导
├── config.py         AppConfig：配置加载/保存、数据目录解析、已知系统目录（下载/桌面/文档）
├── knownfolders.py   SHGetKnownFolderPath（ctypes），解析“下载/桌面/文档”真实路径
├── filetypes.py      扩展名 → 类型分类（图片/视频/音频/PDF/Word/Excel/PPT/压缩/安装包/文本/代码/其他）
├── db.py             SQLite 层：建表、迁移（PRAGMA user_version）、CRUD、损坏恢复、FTS 表管理
├── indexer.py        目录扫描：增量索引、进度/取消/暂停、错误收集、排除规则（子进程隔离见 scanchild.py）
├── query.py          查询条件模型 → SQL 构建、排序打分、匹配原因、内容检索
├── fts.py            FTS5 探测/同步状态判断/MATCH 转义（关键词 trigram 加速的开关与工具）
├── workers.py        后台索引任务骨架：FTS / 内容 / 哈希 追赶线程（增量、节流、断点续跑）
├── textio.py         纯文本内容提取（白名单扩展名 + 编码探测，≤16KB，跳过依赖/构建目录）
├── hash.py           内容哈希（blake2b）与重复分组统计
├── ai.py             可选联网 AI 解析（OpenAI 兼容端点 → 结构化条件，失败回退）
├── secrets.py        Windows DPAPI 加解密（API Key 本机加密存储）
├── vecmodel.py       语义模型：下载、WordPiece 分词、onnxruntime 推理（懒加载）
├── vecindex.py       语义向量：memmap 矩阵构建（后台）与余弦 Top-K 查询
├── nl.py             中文自然语言规则解析器（类型/时间/大小/位置/关键词）
├── fileops.py        打开文件/打开所在文件夹/复制路径/属性对话框/回收站
├── recent.py         可选“最近打开”导入（ctypes 解析 .lnk，默认关闭）
└── ui/
    ├── theme.py       浅色/深色 QSS
    ├── wizard.py      首次启动引导（选择索引目录）
    ├── mainwindow.py  主窗口编排（搜索流程、后台任务调度、AI 按钮）
    ├── filterpanel.py 多条件筛选面板
    ├── resultsview.py 结果表格 + 分组树 + 匹配原因详情 + 文件操作
    ├── duplicatesdialog.py 重复文件管理对话框
    ├── favoritesdialog.py  收藏夹管理
    └── settingsdlg.py 设置：索引目录/维护/AI/语义/隐私/外观
```

## 四、数据库设计（SQLite，WAL）

- `scan_roots`：索引根目录（路径、状态、最后扫描时间、错误计数）；
- `files`：路径、父目录、文件名、扩展名、大小、mtime、ctime、`scan_tick`（每次全量扫描递增），以及 v4 起的四组后台构建标记：`fts_done`（关键词 FTS）、`content_done` + `content_hash`（内容检索/哈希）、`vec_done`（语义向量）；
- `files_fts`（FTS5 虚拟表，trigram）：文件名与路径的关键词加速索引，普通表+db.py 写路径手动同步（避免 external-content 的 VACUUM 限制与逐行触发器开销）；
- `files_content_fts`（FTS5，trigram）：纯文本正文索引（rowid=files.id，内容由任务线程写入）；
- `favorites`：收藏路径；
- `search_history`：本地搜索历史（可一键清除）。

**增量更新策略**：每次对某根目录做全量 walk（`os.scandir`，前缀 `\\?\` 支持长路径），每发现一个文件即 UPSERT 并写入当前 `scan_tick`；扫描**成功完成**后，删除该根目录下 `scan_tick` 过旧的行（即已被删除/移走的文件）。若扫描被取消/暂停则不做清理，保证不丢索引。目录变化只影响变化部分之外，还会以“每文件比较 size+mtime”判断是否更新行，避免无谓写库。

**后台索引任务**：关键词 FTS / 内容提取 / 内容哈希 / 文件名向量均由 `workers.py` 的 daemon 线程按 `*_done=0` 增量追赶（chunk+delay 节流、可停止、失败下次启动续跑）。FTS 追赶在启动与每次扫描结束后自动进行；其余三个由用户在设置页开启后启动，绝不默认运行。

## 五、扫描并发与取消

- 扫描运行在 `QThread` 工作线程，通过 `threading.Event` 实现暂停（`wait()`）与取消（`set()`）；
- 每处理一个条目检查一次取消标记，保证能及时停止；
- 进度信号（已扫描条目数/已索引文件数/错误数）经 queued 信号回传 UI，不阻塞主线程；
- 扫描中文件被删/无权限 → 记为跳过/错误并继续；符号链接/交接点默认跳过（防环）。

## 六、自然语言解析（本地规则为主，可选 AI 增强）

优先级：**位置 > 时间 > 大小 > 类型 > 剩余词→文件名关键词**。支持：
- 时间：今天/昨天/前天/本周/上周/最近 N 天/最近一个月/N 月 N 日/创建于·修改于 限定词（默认按“修改时间”近似，并明示）；
- 大小：“大约/大概 3MB”（±容差区间）、“大于/超过 100MB”、“小于 1MB”、“1–10MB”；
- 类型：PDF/Word/Excel/PPT/图片/视频/音频/压缩包/安装包/文本/代码 等；
- 位置：下载目录/桌面/文档目录/盘符/具体路径。

解析结果以“条件标签”回显，用户可点击修改或一键套用到筛选面板；**未理解的部分如实提示**，绝不伪装理解。

**可选“AI 解析”**（默认关闭、手动按钮）：把描述文字发给用户自配的 OpenAI 兼容端点，让 LLM 输出与上述完全一致的 JSON 条件，再走同一条 chips → SearchQuery 管线；失败/超时/非法输出一律回退本地规则解析并如实提示。

## 六之补充、检索加速与语义（0.3.0）

- **关键词 FTS（trigram，可关闭）**：`files_fts` 为 external-content 表（文本不复制，VACUUM 可用；本机 SQLite 不自动建同步触发器，故由 db.py 写路径手动同步），建成且与 `files` 完全同步（`fts_done` 无残留）时，长度 ≥3 码位的纯子串变体走 `id IN (SELECT rowid FROM files_fts WHERE files_fts MATCH ?)`；短词、词序模式与拼音列保留 LIKE 原路径。两路径结果集逐例对拍（`test_fts.py::test_fts_like_equivalence_matrix`）保证一致。实测 167 万行：高选择性词提速几十倍，常见词相当；索引约 +1.2GB，可在 设置 → 维护 关闭并删除（回退 LIKE、结果不变）。
- **内容检索**：`files_content_fts`(trigram) + `textio.extract_text`（白名单扩展名、≤16KB、跳过 node_modules/site-packages/.git/build/dist 等依赖与构建目录、utf-8/gbk/utf-16 探测）；命中以分组树「内容命中」呈现，片段用 Python 侧窗口截取。
- **语义向量**：`vecmodel.Embedder` 懒加载 onnxruntime + bge-small-zh-v1.5（int8 量化 + scale 还原、mean pooling）；`vecindex.VectorBackfill` 后台把文件名编码写入 `vectors.bin`（np.memmap int8）；查询按分块余弦取 Top-K=200，结果进分组树「语义相关」。
- 三个本地后台任务（内容/哈希/向量）与联网功能全部默认关闭，由设置页开关 + 首次确认弹窗控制。

## 七、排序与匹配原因

- 排序分：文件名精确匹配 > 前缀 > 包含 > 路径包含，叠加时间新度，SQL `CASE` 计算后按分降序；
- 每个结果附带“匹配原因”列表（如“文件类型符合 PDF”“大小在 2–5 MB 内”），展示前对文件做一次 `stat` 校验存在性，已不存在的结果明确标注。

## 八、异常与隐私处理

- 数据库损坏：打开时 `PRAGMA integrity_check`，异常则将原库改名备份并重建，向用户提示；
- 版本升级：`PRAGMA user_version` 迁移（当前 v4）；
- 隐私：默认仅存元数据（路径/名/扩展/大小/时间/索引状态），不读文件内容；联网能力（GitHub 搜索 / AI 解析 / 模型下载）全部默认关闭、首次确认、只发送最小信息（关键词/描述文字），API Key 经 DPAPI 本机加密；设置页提供“清除索引/清除搜索历史/退出时是否保留索引/查看隐私说明”，「维护」页（库大小、压缩索引、完整性检查、内容与哈希任务管理），「AI」页（端点/模型/密钥/测试），「语义」页（模型下载/重建/删除）。

## 九、测试策略

- `tests/test_indexer.py`：扫描、增量删除感知、Unicode 文件名、超长路径跳过、错误计数、取消；
- `tests/test_search.py`：类型/日期/大小/组合/排序；
- `tests/test_nlparser.py`：规格书例句逐条断言 + 无法理解时的行为；
- `tests/test_db.py`：CRUD、损坏恢复、版本迁移；
- `tests/test_fts.py`：FTS/LIKE 两路径结果集等价性矩阵、短词回退、扫描/删除后的同步；
- `tests/test_content.py`：文本提取（多编码/截断/二进制跳过）、正文命中、重建与清理；
- `tests/test_dedup.py`：哈希与分组、大文件跳过、对话框删除流程；
- `tests/test_ai.py`：mock httpx 的成功/HTTP 错误/非 JSON/代码块 JSON、条件规范化、UI 按钮成功与回退；
- `tests/test_secrets.py`：DPAPI 加解密往返；
- `tests/test_vec.py`：WordPiece 单元、假模型全链路构建与余弦 Top-K、语义分组 UI；
- `tests/test_integration.py`：扫描→搜索全链路（无 UI）；
- `tests/test_ui_smoke.py`：`QT_QPA_PLATFORM=offscreen` 下主窗口/向导/搜索冒烟。

## 十、构建

- `scripts/build.ps1`：PyInstaller onedir 打包 → `dist/LostFile/LostFile.exe`；
- `scripts/run.ps1` / `scripts/test.ps1`：一键运行/测试；
- `LostFile.spec`：收集 pypinyin 数据；onnxruntime（语义向量，懒加载）走 `collect_all` 并在 `hiddenimports` 显式声明，避免静态分析漏掉动态库；
- 构建产物与虚拟环境均放在仓库目录内（`.venv/`、`build/`、`dist/` 均已在 `.gitignore` 中）；pip 缓存可用 `PIP_CACHE_DIR` 重定向到仓库外的位置。