import os
import sys
import time
import ctypes
from ctypes import wintypes
import subprocess
from datetime import datetime
from pathlib import Path

# ==============================================================================
# 0. 强制 UTF-8 标准输出配置 (彻底解决英文 Windows cp1252 代码页打印中文报错)
# ==============================================================================
if sys.platform == "win32":
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ==============================================================================
# 1. 核心配置与目标定义
# ==============================================================================
PROJECT_TITLE = "Project Modular Codebase"
OUTPUT_FILENAME = "all-packed.md"
ROOT_DIR = Path("C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\KokoroTTS")


def get_desktop_dir() -> Path:
    if sys.platform == "win32":
        try:
            shell32 = ctypes.windll.shell32
            shell32.SHGetFolderPathW.argtypes = [
                wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR
            ]
            shell32.SHGetFolderPathW.restype = ctypes.c_long
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if shell32.SHGetFolderPathW(None, 0x0000, None, 0, buf) == 0:
                p = Path(buf.value)
                if p.exists():
                    return p
        except Exception:
            pass
    return Path.home() / "Desktop"


# 生成的 Markdown 文件自动保存在桌面
OUTPUT_FILE = get_desktop_dir() / OUTPUT_FILENAME

# 扫描目标（严格按优先级顺序排列，文件夹自动包含所有子目录）
TARGET_PATHS = [
    Path("C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\KokoroTTS\\kokoro_studio"),
    Path("C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\KokoroTTS\\tts_player.py")
]

# 扫描时自动忽略的无关缓存/版本控制目录
IGNORE_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".venv", "venv",
    "node_modules", ".idea", ".vscode", ".vs"
}

# 明确跳过的二进制文件后缀
BINARY_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".obj", ".o", ".a", ".lib",
    ".pyc", ".pyo", ".pyd", ".class", ".jar", ".war",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".psd",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".avi", ".mkv", ".mov",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".db", ".sqlite", ".sqlite3", ".mdb", ".ttf", ".otf", ".woff", ".woff2"
}

# True = 自动纳入所有可读文本/代码文件；False = 仅限 ALLOWED_EXTENSIONS 白名单
INCLUDE_ALL_TEXT_FILES = True

# 允许纳入打包的代码与文本扩展名（当 INCLUDE_ALL_TEXT_FILES = False 时生效）
ALLOWED_EXTENSIONS = {
    ".ahk",
    ".bat",
    ".cmd",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".ts",
    ".txt",
    ".vbs",
    ".yaml",
    ".yml"
}

# 语言高亮标签映射
LANG_TAG_MAP = {
    ".ahk": "autohotkey",
    ".json": "json",
    ".py": "python",
    ".bat": "batch",
    ".cmd": "batch",
    ".vbs": "vbscript",
    ".ps1": "powershell",
    ".ini": "ini",
    ".txt": "text",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".cs": "csharp",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".sql": "sql",
    ".sh": "bash",
    ".toml": "toml",
    ".lua": "lua",
    ".php": "php",
    ".rb": "ruby"
}

# 文件类型中文描述
TYPE_DESC_MAP = {
    ".ahk": "AutoHotkey 脚本",
    ".json": "JSON 配置文件",
    ".py": "Python 脚本",
    ".bat": "Windows 批处理",
    ".cmd": "Windows 批处理",
    ".vbs": "VBScript 脚本",
    ".ps1": "PowerShell 脚本",
    ".ini": "配置文件",
    ".txt": "文本文件",
    ".md": "Markdown 文档",
    ".yaml": "YAML 配置",
    ".yml": "YAML 配置",
    ".xml": "XML 数据文件",
    ".html": "HTML 网页",
    ".css": "CSS 样式表",
    ".js": "JavaScript 脚本",
    ".ts": "TypeScript 脚本",
    ".c": "C 源码",
    ".cpp": "C++ 源码",
    ".h": "C/C++ 头文件",
    ".cs": "C# 源码",
    ".java": "Java 源码",
    ".go": "Go 源码",
    ".rs": "Rust 源码",
    ".sql": "SQL 脚本",
    ".sh": "Shell 脚本",
    ".toml": "TOML 配置",
    ".lua": "Lua 脚本"
}


# ==============================================================================
# 2. Windows 原生文件对象剪贴板引擎 (PowerShell STA .NET 驱动)
# ==============================================================================
def copy_file_to_clipboard(file_path: Path) -> bool:
    if not file_path.exists():
        return False

    abs_path = str(file_path.resolve()).replace("'", "''")

    ps_cmd = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$files = New-Object System.Collections.Specialized.StringCollection; "
        f"$files.Add('{abs_path}'); "
        "[System.Windows.Forms.Clipboard]::SetFileDropList($files); "
        "if ([System.Windows.Forms.Clipboard]::ContainsFileDropList()) { exit 0 } else { exit 1 }"
    )

    try:
        creation_flags = 0x08000000 if sys.platform == "win32" else 0
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Sta", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"[!] 剪贴板调用异常: {e}")
        return False


# ==============================================================================
# 3. Windows 句柄强制释放与进程强杀引擎 (调用系统 Restart Manager API)
# ==============================================================================
def force_close_and_release(file_path: Path):
    if not file_path.exists():
        return

    try:
        with open(file_path, "a", encoding="utf-8"):
            pass
        return
    except PermissionError:
        print(f"[!] 检测到 {file_path.name} 正在被其他程序占用，正在执行强制关闭...")
    except Exception:
        pass

    try:
        rstrtmgr = ctypes.WinDLL("rstrtmgr")
    except Exception:
        subprocess.run(["taskkill", "/F", "/IM", "Typora.exe"], capture_output=True)
        return

    CCH_RM_SESSION_KEY = 32
    session_key = (wintypes.WCHAR * (CCH_RM_SESSION_KEY + 1))()
    session_handle = wintypes.DWORD()

    if rstrtmgr.RmStartSession(ctypes.byref(session_handle), 0, session_key) != 0:
        return

    try:
        file_paths = (wintypes.LPCWSTR * 1)(str(file_path.resolve()))
        if rstrtmgr.RmRegisterResources(session_handle, 1, file_paths, 0, None, 0, None) != 0:
            return

        n_proc_needed = wintypes.UINT(0)
        n_proc = wintypes.UINT(0)
        reboot_reasons = wintypes.DWORD()

        res = rstrtmgr.RmGetList(
            session_handle,
            ctypes.byref(n_proc_needed),
            ctypes.byref(n_proc),
            None,
            ctypes.byref(reboot_reasons)
        )

        if res == 234 or n_proc_needed.value > 0:
            class RM_UNIQUE_PROCESS(ctypes.Structure):
                _fields_ = [
                    ("dwProcessId", wintypes.DWORD),
                    ("ProcessStartTime", wintypes.FILETIME)
                ]

            class RM_PROCESS_INFO(ctypes.Structure):
                _fields_ = [
                    ("Process", RM_UNIQUE_PROCESS),
                    ("strAppName", wintypes.WCHAR * 256),
                    ("strServiceShortName", wintypes.WCHAR * 64),
                    ("ApplicationType", wintypes.DWORD),
                    ("AppStatus", wintypes.DWORD),
                    ("TSSessionId", wintypes.DWORD),
                    ("bRestartable", wintypes.BOOL)
                ]

            n_proc = wintypes.UINT(n_proc_needed.value)
            proc_info = (RM_PROCESS_INFO * n_proc.value)()

            if rstrtmgr.RmGetList(
                session_handle,
                ctypes.byref(n_proc_needed),
                ctypes.byref(n_proc),
                proc_info,
                ctypes.byref(reboot_reasons)
            ) == 0:
                for i in range(n_proc.value):
                    pid = proc_info[i].Process.dwProcessId
                    app_name = proc_info[i].strAppName
                    print(f"[*] 发现锁定进程: {app_name} (PID: {pid})，正在强制终止...")
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    finally:
        rstrtmgr.RmEndSession(session_handle)

    try:
        if file_path.exists():
            file_path.unlink()
    except Exception:
        pass


# ==============================================================================
# 4. 递归文件收集与排序引擎
# ==============================================================================
def is_text_file(fpath: Path) -> bool:
    if fpath.suffix.lower() in BINARY_EXTENSIONS:
        return False
    try:
        with open(fpath, "rb") as f:
            chunk = f.read(4096)
        return b"\x00" not in chunk
    except Exception:
        return False


def is_allowed_file(fpath: Path, explicitly_selected: bool = False) -> bool:
    if fpath.resolve() == OUTPUT_FILE.resolve():
        return False
    if explicitly_selected:
        return is_text_file(fpath)
    if INCLUDE_ALL_TEXT_FILES:
        return is_text_file(fpath)
    return fpath.suffix.lower() in ALLOWED_EXTENSIONS


def collect_code_files(targets):
    file_list = []
    seen_paths = set()

    for target in targets:
        if not target.exists():
            print(f"[!] 警告：路径未找到，已跳过 -> {target}")
            continue

        if target.is_file():
            resolved = target.resolve()
            if is_allowed_file(target, explicitly_selected=True) and resolved not in seen_paths:
                file_list.append((target, target))
                seen_paths.add(resolved)
        elif target.is_dir():
            for root, dirs, files in os.walk(target):
                dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
                for f in sorted(files):
                    fpath = Path(root) / f
                    resolved = fpath.resolve()
                    if is_allowed_file(fpath, explicitly_selected=False) and resolved not in seen_paths:
                        file_list.append((fpath, target))
                        seen_paths.add(resolved)

    return file_list


def format_relative_path(fpath: Path, target: Path) -> str:
    try:
        rel = fpath.relative_to(ROOT_DIR)
        if str(rel) != ".":
            return str(rel)
    except ValueError:
        pass
    if target.is_dir():
        return str(Path(target.name) / fpath.relative_to(target))
    return fpath.name


def read_text_smart(fpath: Path) -> str:
    raw = fpath.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ==============================================================================
# 5. Markdown 打包写入主流程
# ==============================================================================
def main() -> bool:
    print("=" * 65)
    print(f"        {PROJECT_TITLE} -> {OUTPUT_FILE.name}")
    print("=" * 65)
    print(f"[*] 项目根目录: {ROOT_DIR}")
    print(f"[*] 目标保存至: {OUTPUT_FILE}")

    force_close_and_release(OUTPUT_FILE)

    files = collect_code_files(TARGET_PATHS)
    if not files:
        print("[x] 错误：未检索到任何符合条件的代码文件！")
        print("    -> 请检查目标文件夹是否为空，或扩展名过滤规则是否过严。")
        return False

    print(f"[*] 共检索到 {len(files)} 个代码文件，正在读取内容与元数据...")

    records = []
    total_lines = 0
    total_size_bytes = 0

    for idx, (fpath, target) in enumerate(files, 1):
        rel_path = format_relative_path(fpath, target)
        ext = fpath.suffix.lower()
        size_kb = round(fpath.stat().st_size / 1024, 2)
        total_size_bytes += fpath.stat().st_size

        content = read_text_smart(fpath)
        line_count = len(content.splitlines())
        total_lines += line_count

        records.append({
            "idx": idx,
            "path": fpath,
            "rel": rel_path,
            "name": fpath.name,
            "ext": ext if ext else "(无后缀)",
            "type_desc": TYPE_DESC_MAP.get(ext, "其他文本代码"),
            "lang": LANG_TAG_MAP.get(ext, "text"),
            "size_kb": size_kb,
            "lines": line_count,
            "content": content
        })

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_size_kb = round(total_size_bytes / 1024, 2)

    md_lines = [
        f"# {PROJECT_TITLE} (完整模块化代码归档)",
        "",
        f"> **生成时间**：{now_str}  ",
        f"> **根目录位置**：`{ROOT_DIR}`  ",
        f"> **归档文件清单**：共 **{len(records)}** 个模块与脚本 | 总行数：**{total_lines}** 行 | 总大小：**{total_size_kb} KB**  ",
        "",
        "---",
        "",
        "## 📑 模块与文件索引 (Table of Contents)",
        "",
        "| 序号 | 文件名 | 相对路径 | 文件类型 | 大小 (KB) | 代码行数 |",
        "| :---: | :--- | :--- | :---: | :---: | :---: |"
    ]

    for r in records:
        md_lines.append(
            f"| {r['idx']:02d} | `{r['name']}` | `{r['rel']}` | {r['type_desc']} | {r['size_kb']} | {r['lines']} |"
        )

    md_lines.extend([
        "",
        "---",
        "",
        "## 💻 代码正文详情",
        ""
    ])

    for r in records:
        print(f"[{r['idx']:02d}/{len(records):02d}] 正在打包: {r['rel']}")
        md_lines.extend([
            f"### {r['idx']:02d}. `{r['rel']}`",
            "",
            f"* **文件名**：`{r['name']}`",
            f"* **相对路径**：`{r['rel']}`",
            f"* **绝对路径**：`{r['path']}`",
            f"* **文件类型**：{r['type_desc']} (`{r['ext']}`)",
            f"* **代码行数**：{r['lines']} 行",
            "",
            f"````{r['lang']}",
            r["content"],
            "````",
            "",
            "---",
            ""
        ])

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(md_lines), encoding="utf-8")
    print("=" * 65)
    print(f"[√] 成功打包完成！目标文件已保存至桌面：\n    -> {OUTPUT_FILE}")

    if copy_file_to_clipboard(OUTPUT_FILE):
        print(f"[√] 剪贴板注入成功：已将 {OUTPUT_FILE.name} 作为【文件实体】存入剪贴板！")
        print("    -> 现在可直接在任意文件夹、微信、QQ、浏览器或邮件中按 Ctrl + V 粘贴该文件。")
        print("=" * 65)
        return True
    else:
        print("[x] 警告：未能将文件放入剪贴板，请检查是否有后台剪贴板监听程序锁定了通道。")
        print("=" * 65)
        return False


if __name__ == "__main__":
    ok = False
    try:
        ok = main()
    except Exception as exc:
        print(f"\n[x] 运行发生异常: {exc}")
        ok = False

    if "--no-pause" not in sys.argv:
        if ok:
            print("窗口将在 3 秒后自动关闭...")
            time.sleep(3)
        else:
            input("按回车键退出...")
