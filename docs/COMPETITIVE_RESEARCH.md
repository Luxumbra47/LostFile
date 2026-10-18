# LostFile 竞品调查报告

- 调查日期：2026-10-17
- 调查方式：使用在线搜索工具（WebSearch）检索公开网页与 GitHub 仓库、官方文档页面
- 结论摘要：**“按文件名秒搜”与“全文内容索引”是成熟红海；而“用记忆中的特征（类型/日期/大小/位置）描述文件、以中文自然语言输入、并解释为什么匹配”的本地工具仍属空白。** 本报告不声称穷尽全世界所有类似产品，仅基于可公开检索到的资料整理。

## 一、已检索到的同类产品

### 1. Everything (voidtools)
- 链接：https://www.voidtools.com/
- 功能：基于 NTFS MFT 的全盘实时文件名索引，毫秒级文件名/路径搜索；支持 `size:` `datemodified:` 等英文操作符语法做元数据过滤；默认扫描所有 NTFS 卷。
- 与 LostFile 区别：
  - Everything 需要学习操作符语法（如 `size:>3mb datemodified:yesterday`），普通用户门槛高；
  - 无中文自然语言解析，无“条件解释/匹配原因”；
  - 默认全盘索引（性能极佳但隐私展示弱）；LostFile 让用户显式选择索引范围。
- 结论：**文件名秒搜是其护城河，不重复开发。** 元数据过滤语法值得参考，但 LostFile 用可视化面板+中文 NL 替代语法。

### 2. WizFile (Antibody Software)
- 链接：https://antibody-software.com/wizfile
- 功能：高速文件查找器，可按键名/路径/大小/日期搜索；通过文件系统监控保持索引实时更新；提供大小和修改日期过滤器下拉框。
- 与 LostFile 区别：过滤器基于操作符（`>` `<` 等），仍需理解语法；无中文自然语言；无匹配原因解释；非开源。
- 结论：其“大小/日期下拉过滤器”验证了按特征筛选的用户需求方向，但交互仍面向进阶用户。

### 3. Listary
- 链接：https://www.listary.com/ （帮助文档：https://help.listary.com/search-file）
- 功能：搜索+启动器二合一；内置过滤器（`doc:` `pic:` `video:` `audio:` `folder:`）；支持 `date:today` / `date:yesterday` 等日期语法（中英文文档均有）。
- 与 LostFile 区别：核心是启动器和即时搜索，条件仍须用关键词语法；无中文整句描述解析；无匹配原因；商业闭源。
- 结论：**启动器/即时唤起能力不重复开发。** 其日期过滤语法是“按记忆找文件”的场景印证。

### 4. DocFetcher
- 链接：https://docfetcher.sourceforge.io/
- 功能：开源本地全文索引（按文件内容搜索），支持 doc/xls/ppt/pdf 等格式，跨平台，EPL 许可。
- 与 LostFile 区别：按“内容”搜索，与 LostFile 按“元数据特征”搜索正交；需预处理抽取文本，索引体积大。
- 结论：**全文内容索引不开发**（与 LostFile 的隐私承诺“不读文件正文”直接冲突）。

### 5. Microsoft PowerToys Run
- 链接：https://learn.microsoft.com/zh-cn/windows/powertoys/run
- 功能：快速启动器，可搜索应用/文件夹/文件并执行 Shell 插件；按名称匹配。
- 与 LostFile 区别：面向“快速启动”，检索维度以名称为主，无组合条件筛选与解释。
- 结论：启动器形态不重复开发。

### 6. 命令行类元数据检索工具（GitHub 检索获得）
- fselect（Rust，SQL 风格查询，https://crates.io/crates/fselect）：`select path from /home/user where modified = today and size > 10M`，能力最强但面向 CLI 用户。
- fastfind（https://github.com/RobertFlexx/fastfind）、winix（https://github.com/Yortw/winix）、seekr：以 `--size` `--min-size` 等方式过滤。
- 与 LostFile 区别：全部面向命令行/进阶用户；无中文自然语言；无 GUI 条件面板与解释。
- 结论：**证明“按元数据多条件找文件”是真实需求**，但现有实现都要求用户学习语法或 SQL，LostFile 的 GUI + 中文描述是对同一需求的平民化实现。

### 7. Windows 最近使用记录相关
- Windows 自带 `shell:recent`（%APPDATA%\Microsoft\Windows\Recent 下的 .lnk）与任务栏 Jump List 可查看最近打开的文件；
- 第三方工具如 RecentFilesView（NirSoft）解析这些快捷方式。
- 与 LostFile 关系：LostFile 提供**默认关闭、用户主动开启**的可选“最近打开”导入，仅解析 .lnk 路径与时间，不明示来源应用。

## 二、不应重复开发的功能

1. NTFS MFT 级别全盘实时文件名索引（Everything 的护城河）；
2. 文件全文内容索引与抽取（DocFetcher 领域，且与隐私承诺冲突）；
3. 启动器/进程管理/剪贴板历史（PowerToys Run、Listary 领域）；
4. 实时文件系统监控推送（WizFile 领域，MVP 采用按需增量扫描即可）。

## 三、仍然存在的差异化机会

1. **中文自然语言式描述搜索**：“昨天的 PDF 大概 3MB” → 解析为可编辑条件，现有产品均无（Everything 为英文操作符，Listary 为关键词语法）；
2. **解析结果可视化 + 可修改**：解析出的条件以标签形式展示、点击回到筛选面板修改，解析失败时诚实提示并引导使用筛选器；
3. **匹配原因解释**：每个结果说明“为什么它被找到”（类型符合 PDF、时间符合昨天、大小在 2–5MB 之间），增强结果信任度；
4. **面向普通用户的多条件筛选面板**：类型/时间/大小/位置全部下拉选择，零语法学习；
5. **隐私优先**：用户显式选择索引目录、默认不索引全盘、不读文件内容、全本地存储、开源 MIT；
6. **文件状态校验**：显示结果文件是否仍然存在（移动/删除感知）。

## 资料来源

- Everything 官方文档：https://www.voidtools.com/zh-cn/support/everything/using_everything/ ；搜索语法：http://www.voidtools.com/support/everything/searching/
- WizFile：https://antibody-software.com/wizfile/about ；https://antibody-software.com/wizfile/whats-new
- Listary：https://help.listary.com/search-file ；https://help.listary.com/zh-Hans/advanced-search-syntax
- DocFetcher：https://docfetcher.sourceforge.io/
- PowerToys Run：https://learn.microsoft.com/zh-cn/windows/powertoys/run
- fselect：https://crates.io/crates/fselect
- fastfind：https://github.com/RobertFlexx/fastfind ；winix：https://github.com/Yortw/winix ；seekr：https://muhammad-fiaz.github.io/seekr/
- Windows 最近使用文件：https://learn.microsoft.com/answers/questions/3267639/recent-folders-frequent-folders-recent-files-quick