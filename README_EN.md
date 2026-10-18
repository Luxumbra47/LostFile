# LostFile (Digital Lost & Found)

> Can't remember the file name? Describe what you remember about it — LostFile finds it for you.

LostFile is a **local Windows file finder**. Unlike traditional file search, you don't need to know the file name — remember roughly its **type, date, size, or location**, or describe it in a sentence:

- "昨天下载的 PDF，大概 3MB。" (The PDF I downloaded yesterday, around 3MB.)
- "上周修改的图片。" (The image I modified last week.)
- "下载目录里超过 100MB 的视频。" (Videos over 100MB in the downloads folder.)
- "最近一个月创建的 Word 文档。" (Word documents created in the last month.)

LostFile parses the sentence into editable search conditions, shows them for confirmation, and explains **why each result matched**.

**Local by default: file contents are not read, nothing local is uploaded, and no API key or database server is required.** Every capability that touches the network or reads file contents is an **opt-in, off-by-default** feature that asks for confirmation first — see [Privacy](#privacy).

![Main window: natural-language search, condition chips and match reasons](docs/screenshots/main_search.png)

## Features

- **Multi-condition search**: file type (image/video/audio/PDF/Word/Excel/PPT/archive/installer/text/code/…), time range (today/yesterday/last 7 days/last 30 days/custom), size range (<1MB / 1–10MB / 10–100MB / >100MB / custom), location, name keywords, extension, created/modified time.
- **Chinese natural-language search**: parsed by local rules (no AI, no network). Recognized conditions are shown as removable chips and can be adjusted in the filter panel; anything not understood is reported honestly — LostFile never pretends to understand.
- **Two-pane results**: the upper pane groups "related files" by keyword (name variants like `project-notes` / `project_notes` / `projectnotes` and pinyin/homophone matches are all recognized); the lower pane is the formal result set filtered by all conditions. Click a column header to sort; select a row to see why it matched.
- **Honest fallback when nothing matches**: conditions are relaxed step by step (location → time → size → type, the keyword is always kept) and the banner tells you what was relaxed; misspellings produce a "did you mean" candidate list; if still nothing, only a **small** recency list is shown — LostFile never dumps the whole index as "results".
- **Match reasons**: each result explains which conditions it satisfies and where the keyword hit, and explicitly marks files that no longer exist (moved/deleted).
- **Display count**: 500 / 2000 / **Unlimited**. Choosing Unlimited renders one page first and shows a **"Load more"** button to append further pages until everything is shown — no hidden 50,000-row cap.
- **User-chosen index scope**: you pick the folders to index (Downloads/Desktop/Documents/custom, plus auto-discovered common folders on other drives); **the whole disk is never scanned by default**; incremental updates, pause/cancel scan, exclude directories and file types.
- **Index maintenance**: Settings → Maintenance shows the index size, **compacts the index** to reclaim disk space, and verifies integrity.
- **Result actions**: open, reveal in Explorer, copy full path, copy file name, properties, add to favorites, filter-within-results; right-click offers **move a single file to the Recycle Bin** (with confirmation, restorable). Toolbar entries for **Favorites** and **Recycle Bin**.
- **Remembers your setup**: window size, pane layout and column widths, and the last search text; shortcuts `Ctrl+F` (search), `F5` (rescan), `Ctrl+L` (clear), `Enter` (open), `Ctrl+C` (copy path), `Delete` (move to Recycle Bin).
- **Fast on large indexes (optional)**: keyword matching is accelerated by SQLite FTS5 (trigram). Measured on a 1.67M-row index, highly selective terms (full English names, digit strings) drop from ~3 s to tens of milliseconds; common short terms are comparable. It costs ~+1.2 GB per 1.67M rows and can be turned off with one click (falls back to the original path, identical results).
- **Optional "AI parse" button** (off by default): asks an AI to work out what the thing you described actually is (e.g. a downloaded project, game or app name), derives name variants (canonical spelling, English name/abbreviation, compact spelling without spaces/hyphens, close synonyms) plus time/size/type/location conditions, then **searches your machine directly** and shows the AI's reasoning. Supports any OpenAI-compatible endpoint (DeepSeek / Qwen / SiliconFlow / **local Ollama**, etc.; you supply endpoint + key). Only the description text is sent; the key is stored encrypted with Windows DPAPI; failures fall back to local rule parsing.
- **Optional "content search"** (off by default): extracts plain text from whitelisted text files (txt/md/log/csv/config/source code, …), **first 16 KB per file**, into a local full-text index; matches appear in a "content hits" group with a hover preview. `node_modules`, `site-packages`, `.git`, `build`, `dist` and similar dependency/build directories are **skipped** by default so the index stays small. Note: 2-character Chinese words cannot be matched in content (a documented limitation of the trigram index).
- **Optional "duplicate detection"** (off by default): finds files with identical contents by content hash (regardless of name); review groups in the "Duplicates" dialog and confirm moving them to the Recycle Bin. File contents are never stored.
- **Optional "local semantic search"** (off by default): a bge-small-zh embedding model encodes file **names** to recall files that are semantically related even when the name lacks your keywords ("semantically related" group, hover to see similarity). A ~24 MB model is downloaded once from ModelScope after confirmation, then runs fully offline.
- **Optional "search GitHub online"** (off by default): sends only the **keywords you typed** to the public GitHub search API to trace where a downloaded thing came from (e.g. a same-named skill/repo). Enable with a consent dialog; automatically skipped when offline, and local search is unaffected.
- **Optional "recent files" import** (off by default): reads only paths and timestamps from Windows recent-items records; the source app is shown as "unknown".
- **Privacy first**: by default only file metadata (path/name/extension/size/times) is indexed and file contents are never read; all network/content features are off by default with a confirmation dialog; one-click clear of index and search history; portable mode keeps all data next to the app.

![Two-pane results: collapsible "related files" groups above the result table](docs/screenshots/main_groups.png)

![Filter panel](docs/screenshots/main_filter.png) ![Index maintenance: index size and compaction](docs/screenshots/settings_maintenance.png) ![Privacy settings](docs/screenshots/settings_privacy.png)

![AI parsing service configuration](docs/screenshots/settings_ai.png) ![Local semantic search](docs/screenshots/settings_vec.png)

## Requirements & Installation

- Windows 10/11 (64-bit). No admin privileges needed.
- **Download**: get the latest portable zip `LostFile_v<version>_win64.zip` from the [Releases](./releases) page and unzip it.
- **Portable**: after unzipping, run `LostFile.exe`. In portable mode all data lives in `data/` next to the executable — delete that folder to wipe everything.
- Remove `portable.txt` next to the exe to switch to normal mode (data stored in `%APPDATA%\LostFile`).

## Quick Start (from source)

```powershell
# 1. Create a venv and install dependencies (Python 3.10+, recommended 3.12)
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Run
.\scripts\run.ps1        # or .venv\Scripts\python.exe -m lostfile

# 3. Test
.\scripts\test.ps1

# 4. Build the Windows package
.\scripts\build.ps1      # output: dist\LostFile\LostFile.exe
```

The first launch shows a wizard to pick which folders to index. After scanning, search by describing the file or by using the filter panel.

## Usage

1. **Find a file by describing it**: type a description (e.g. "昨天的 PDF，大概 3MB"), press Enter, review the parsed condition chips (each can be removed or adjusted in the filter panel), then choose "确认并搜索" (Confirm & search).
2. **Find by conditions**: click "筛选" (Filters) and combine type/time/size/location freely.
3. **Understand results**: select a row to see why it matched and whether the file still exists.
4. **Two panes**: the upper pane lists related files by keyword group even when strict conditions don't match (click the triangle to expand, double-click to open); the lower pane is the formal, condition-filtered result set.
5. **When nothing is found**: the banner explains which conditions were relaxed; misspellings get "did you mean" candidates; for just-downloaded/installed files, click "重新扫描" (Rescan) first.
6. **Shortcuts**: `Ctrl+F` focus search, `F5` rescan, `Ctrl+L` clear input; in the result table `Enter` open, `Ctrl+C` copy path, `Delete` move to Recycle Bin.
7. **Maintenance**: Settings → Maintenance shows the index size and offers "压缩索引" (compact index) to reclaim disk space.
8. **Enhancements (all off by default)**: Settings → Maintenance enables "content search / duplicate detection"; Settings → Semantic enables the "semantically related" group (downloads a ~24 MB model once); the "AI 解析" button next to the search box splits complex descriptions; the toolbar's "重复文件" (Duplicates) shows dedup results.

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for the full guide (in Chinese).

## Privacy

By default LostFile runs fully locally: the index holds only file metadata (path/name/extension/size/times), file contents are **not** read, and file names, paths, search history and usage statistics are never uploaded.

Exactly **three opt-in, off-by-default** features use the network, and each one is confirmed by a dialog before it is enabled. All of them send only the minimum information:

- **"Search GitHub online"**: sends only the **keywords you typed** to the public GitHub search API (`api.github.com`) to trace where a downloaded thing came from.
- **"AI parse"**: sends only the **description text you typed** to the OpenAI-compatible endpoint you configured yourself; the API key is stored on this machine encrypted with Windows DPAPI.
- **"Semantic model download"**: on first enabling semantic search, downloads a ~24 MB model (from ModelScope) once after confirmation, then runs fully offline.

None of them ever send file names, paths, index contents or file contents.

Content search (plain text only, first 16 KB, skipping dependency/build directories) and duplicate detection (content hashes only, contents are never stored) are local computations and are also off by default. LostFile never permanently deletes your files: it only offers moving a **single** file to the Windows Recycle Bin (with confirmation, restorable). See [docs/PRIVACY.md](docs/PRIVACY.md).

## Tech Stack

Python 3 + PySide6 (Qt 6) + SQLite (WAL incremental index + FTS5 trigram acceleration) + NumPy/ONNX Runtime (local semantic vectors) + PyInstaller (portable build). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design decisions and [docs/COMPETITIVE_RESEARCH.md](docs/COMPETITIVE_RESEARCH.md) for the competitive survey.

## Difference from similar tools

Everything / WizFile / Listary require file names or operator syntax; LostFile targets ordinary users with natural-language descriptions, a visual condition editor, and match-reason explanations — and the indexed scope is entirely user-chosen.

## License

[MIT License](LICENSE). Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md); security reports: [SECURITY.md](SECURITY.md).

> Language: **English** · [简体中文](README.md)
>
> Note: the current UI and the detailed documentation are Chinese-first. An English UI is on the roadmap.
