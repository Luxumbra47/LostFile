# LostFile 隐私说明

生效版本：0.3.4（2026-10-18）

## 一句话总结

**LostFile 默认全本地运行，默认只索引文件元数据、不读取文件内容、不上传任何本地信息，也不会永久删除你的原始文件（仅提供单个文件“删除到回收站”，可随时还原）。任何联网、内容读取能力都是默认关闭的可选功能，开启前都会弹窗说明并征求确认：联网只会发送你输入的描述/关键词文字，绝不会发送本地文件名、路径、索引或文件内容。**

## 我们收集什么（都只存在你的电脑上）

| 数据 | 用途 | 存储位置 | 是否默认开启 |
| --- | --- | --- | --- |
| 你选择的索引目录列表 | 决定搜索范围 | 本地 SQLite 数据库 | 是 |
| 文件路径、文件名、扩展名 | 搜索与展示 | 本地 SQLite 数据库 | 是 |
| 文件大小、创建时间、修改时间 | 按特征筛选 | 本地 SQLite 数据库 | 是 |
| 索引状态（扫描时间、错误计数） | 显示索引健康度 | 本地 SQLite 数据库 | 是 |
| 搜索历史 | 输入补全（可一键清除） | 本地 SQLite 数据库 | 是 |
| 收藏列表 | 收藏功能 | 本地 SQLite 数据库 | 是 |
| 界面主题等设置、窗口布局、上次搜索词 | 配置与“记住界面” | 本地 JSON | 是 |
| 纯文本文件开头 ≤16KB 的正文 | “正文内容检索”功能 | 本地 SQLite 数据库 | **否**（可选功能） |
| 文件内容哈希（blake2b、含文件名引用） | “重复文件检测”功能 | 本地 SQLite 数据库 | **否**（可选功能） |
| 文件名编码出的向量矩阵（int8） | “本地语义搜索”功能 | 应用数据目录 vectors.bin 等 | **否**（可选功能） |
| AI 服务的 API Key | 可选“AI 解析”功能 | 本机 DPAPI 加密的 config.json | **否**（可选功能） |

## 我们明确不做的事

- **不上传本地信息**：文件名、路径、搜索记录、索引内容、文件正文、使用统计一律不出本机。会联网的只有两个**默认关闭**的可选功能：
  - “在线搜索 GitHub”：只把**你输入的关键词**发给 GitHub 公开搜索接口；
  - “AI 解析”：只把**你输入的描述文字**发给你自己配置的 AI 服务（OpenAI 兼容端点）。
  两者都不发送任何本地文件名、路径或索引内容，开启前都会弹窗确认。
- **默认不读内容**：默认只索引元数据。只有你主动开启“正文内容检索”（仅纯文本、≤16KB）或“重复文件检测”（仅计算哈希、不保存正文）后才会读取文件内容。
- **不后台监控**：无后台常驻进程，无文件系统实时监控，扫描与各索引构建只在你主动开启后进行。
- **不永久删除**：结果列表只提供**单个文件**的“删除到回收站”——需二次确认，文件移入 Windows 回收站可随时还原；没有批量删除，也不会绕过回收站直接抹除。
- **不越界读取敏感数据**：“最近打开”功能仅解析 Windows 最近使用记录（.lnk）中的目标路径与时间，来源应用显示“未知”；不会读取浏览器密码、Cookie、书签或其他凭据。

## 可选功能：“在线搜索 GitHub”

- 默认**关闭**；首次勾选时应用会弹窗说明，确认后才生效。
- 开启后，每次搜索只会把**你输入的关键词文本**发送到 GitHub 公开搜索接口（`api.github.com`），用于查找同名仓库（例如确认某个下载来的 skill/项目在网上的出处）。
- **不会发送**：本地文件名、路径、目录结构、索引内容、搜索历史。
- 请求失败或无网络时静默跳过，本地搜索不受影响。
- 随时可在结果区右上角取消勾选关闭。

## 可选功能：“AI 解析”（手动按钮触发）

- 默认**关闭**；首次点击主界面「AI 解析」按钮时会弹窗说明，确认后才启用。
- 只把**你输入的描述文字**发给**你自己配置**的 AI 服务（OpenAI 兼容端点，支持 DeepSeek/通义/硅基流动/本地 Ollama 等）；端点与模型在 设置 → AI 中自填。
- **不会发送**：本地文件名、路径、目录结构、索引内容、文件正文、搜索历史。
- API Key 经 **Windows DPAPI** 加密后保存在本机 config.json；DPAPI 与 Windows 帐户绑定——换用户或重装系统后无法解密（应用会提示重新填写），这本身就是一种安全特性。
- 网络失败、超时或返回异常时自动回退本地规则解析，并在界面上如实提示；本地搜索永远可用。

## 可选功能：“正文内容检索”

- 默认**关闭**；在 设置 → 维护 勾选时弹窗说明，确认后才启用。
- 仅在本机读取**白名单纯文本文件**（txt/md/日志/csv/配置/源代码等）开头最多 **16KB** 的内容存入本地索引（SQLite FTS），用于按正文关键词命中。
- 默认跳过 node_modules、site-packages、.git、build、dist 等依赖/构建目录，避免正文索引体积膨胀。
- 不解析 Office/PDF，不读取图片/视频等二进制内容；内容绝不上传；可随时关闭、在维护页一键清空重建。
- 受检索技术限制，2 个字的中文词无法用于正文匹配（已在界面与文档中如实说明）。

## 可选功能：“重复文件检测”

- 默认**关闭**；在 设置 → 维护 勾选时弹窗说明，确认后才启用。
- 只在本机计算文件的内容哈希（blake2b）用于比对重复；**不保存文件正文**，哈希值不可还原出内容；绝不上传。
- 超过 2GB 的文件自动跳过；结果在工具栏「重复文件」中查看，删除仍走回收站。

## 可选功能：“本地语义搜索”

- 默认**关闭**；在 设置 → 语义 启用时弹窗说明，确认后才启用。
- 开启时首次会**联网下载一次**向量模型（bge-small-zh-v1.5 量化版，约 24MB，从 ModelScope 下载到应用数据目录 `models/`，失败自动换镜像并明确报错），之后全程离线。
- 只对**文件名**编码（不读取文件内容）；向量矩阵（int8、约 512MB/百万文件，内存映射加载）仅存本机；可用「删除向量数据」一键清空。

## 可选功能：“最近打开”导入

- 默认**关闭**；开启前应用会弹出明确说明并获得你的确认。
- 开启后仅执行一次对 `%APPDATA%\Microsoft\Windows\Recent` 目录的只读解析。
- 只能可靠获得：目标文件路径（若仍存在）与快捷方式时间（标为“约”）。
- 无法可靠得知：来源应用、打开次数、文件内容——这些一律显示“未知”或直接不显示。

## 数据位置与清除

- **便携模式（默认）**：全部数据在程序目录的 `data/` 下（`lostfile_index.db`、`config.json`、模型 `models/`、向量 `vectors.bin` 等）。删除 `data/` 或整个程序目录即彻底清除。
- **普通模式**（删除 exe 旁 `portable.txt`）：数据在 `%APPDATA%\LostFile`。
- 应用内可在 设置 → 隐私 中一键：清除全部索引 / 清除搜索历史 / 退出时自动清除索引；各可选项在对应设置页有一键重置/删除入口。

## 索引范围的自我决定

LostFile 不默认扫描整个磁盘。索引哪些目录完全由你在引导页与设置中决定，并可随时移除；“正文内容检索 / 重复检测 / 语义向量”三个后台任务也只有你在对应设置页开启后才会运行。

## 真相与近似

- “下载时间”这一信息 Windows 不单独记录，LostFile 用创建/修改时间近似，并在条件标签中明示“按修改时间”。
- 文件大小“大概 3MB”会放宽为 2–5MB 搜索，标签中写明“约数自动放宽”。
- 无法理解的描述会如实提示并引导你使用筛选面板或「AI 解析」，绝不假装理解。

## 反馈

对隐私实现有疑问或发现疑似违背本说明的行为，请按 [SECURITY.md](../SECURITY.md) 的渠道反馈。

---

# LostFile Privacy Policy (English summary)

- Fully local by default: default indexing stores only file metadata (path/name/extension/size/timestamps) and file contents are never read; nothing local is ever uploaded.
- Network-capable features are all **opt-in and off by default** with a confirmation dialog: "Search GitHub online" sends only the keywords you typed to the public GitHub API; "AI parse" sends only the description text you typed to an AI endpoint **you configured** (API key stored encrypted with Windows DPAPI, machine-bound). Never file names, paths, index contents or file contents.
- Optional local features (also off by default): text-content indexing (plain-text files, first 16KB, skip node_modules/site-packages/.git/build/dist, local only), duplicate detection (content hashes only, contents are never stored), and semantic search (filename embeddings; a ~24MB model is downloaded once from ModelScope on first enable, then fully offline).
- Original files are never permanently deleted: the result list offers only a per-file "move to Recycle Bin" (with confirmation, restorable); there is no bulk delete.
- The optional "Recent files" import is disabled by default and, when enabled, only reads .lnk target paths and timestamps; the source app is shown as "unknown". No passwords, cookies, or other credentials are ever accessed.
- All data stays in the app's data folder (portable mode: `data/` next to the exe, including models and vectors). Deleting it removes everything. One-click clearing is available in Settings → Privacy.