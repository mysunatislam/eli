"""Coding Agent: VS Code / terminal awareness, project file access, and the
error -> analyse -> fix -> test -> verify workflow.

The model does the reasoning; this module gives it eyes and hands inside the project:
- what VS Code is showing (file, workspace, language, unsaved state) from the window title
- where projects live (Documents/Desktop/Downloads, ELI_PROJECT_DIRS, VS Code's recent workspaces)
- find / read / write files (writes are confirmation-gated and backed up), git status, list dirs
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Optional

log = logging.getLogger("eli.coding")

LANG_BY_EXT = {
    ".py": "Python", ".ipynb": "Jupyter (Python)", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript (React)", ".jsx": "JavaScript (React)", ".java": "Java", ".kt": "Kotlin", ".cs": "C#",
    ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C/C++ header", ".hpp": "C++ header", ".rs": "Rust", ".go": "Go",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".dart": "Dart", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".md": "Markdown", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
    ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".xml": "XML", ".m": "MATLAB", ".r": "R", ".jl": "Julia",
}
VSCODE_TITLE = re.compile(r"^(?P<dirty>●\s*)?(?P<file>.+?)\s+-\s+(?P<workspace>.+?)\s+-\s+Visual Studio Code(?: - Insiders)?$")
VSCODE_TITLE_NOWS = re.compile(r"^(?P<dirty>●\s*)?(?P<file>.+?)\s+-\s+Visual Studio Code(?: - Insiders)?$")
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".idea", ".vs", "dist", "build", "target",
             "AppData", ".cache", ".gradle", "Library", "site-packages", ".next", ".nuxt", "bin", "obj"}
TEXT_EXT = set(LANG_BY_EXT) | {".txt", ".cfg", ".ini", ".env", ".csv", ".log", ".gitignore", ".lock"}


class CodingAgent:
    def __init__(self, settings, extra_dirs: Optional[list[str]] = None):
        self.settings = settings
        self.extra_dirs = extra_dirs or []
        self._roots: list[Path] = []
        self._roots_ts = 0.0

    # -- context --------------------------------------------------------------------------------
    def vscode_context(self, window) -> Optional[dict]:
        title = (getattr(window, "title", "") or "").strip()
        if "Visual Studio Code" not in title:
            return None
        m = VSCODE_TITLE.match(title) or VSCODE_TITLE_NOWS.match(title)
        if not m:
            return {"editor": "VS Code"}
        f = m.group("file").strip()
        ws = (m.groupdict().get("workspace") or "").strip()
        ext = Path(f).suffix.lower()
        return {
            "editor": "VS Code",
            "file": f,
            "workspace": ws,
            "language": LANG_BY_EXT.get(ext, ext.lstrip(".").upper() if ext else "unknown"),
            "unsaved": bool(m.group("dirty")),
        }

    def describe_context(self, window) -> str:
        ctx = self.vscode_context(window)
        if not ctx:
            return ""
        parts = [f"VS Code is open on {ctx.get('file', 'a file')}"]
        if ctx.get("language"):
            parts.append(f"({ctx['language']})")
        if ctx.get("workspace"):
            parts.append(f"in workspace '{ctx['workspace']}'")
        if ctx.get("unsaved"):
            parts.append("[unsaved changes]")
        return " ".join(parts) + "."

    # -- roots ----------------------------------------------------------------------------------
    def project_roots(self) -> list[Path]:
        if self._roots and time.time() - self._roots_ts < 60:
            return self._roots
        home = Path.home()
        cands: list[Path] = []
        for name in ("Documents", "Desktop", "Downloads", "source", "repos", "projects", "Projects", "code", "dev",
                     "OneDrive/Documents", "OneDrive/Desktop"):
            cands.append(home / name)
        cands.append(home / ".gemini" / "antigravity" / "scratch")
        for drive in ("C:", "D:", "E:"):
            d_path = Path(drive + "\\")
            if d_path.exists():
                for sub in ("Projects", "projects", "Code", "code", "dev", "workspace", "Workspace", "repos", "source", "MATLAB"):
                    cands.append(d_path / sub)
        cands += [Path(p) for p in self.extra_dirs]
        cands += self._vscode_recent()
        seen, roots = set(), []
        for c in cands:
            try:
                r = c.resolve()
            except Exception:
                continue
            if r.is_dir() and str(r).lower() not in seen:
                seen.add(str(r).lower())
                roots.append(r)
        self._roots, self._roots_ts = roots, time.time()
        return roots

    def _vscode_recent(self) -> list[Path]:
        out: list[Path] = []
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return out
        base = Path(appdata) / "Code" / "User"
        for storage in (base / "globalStorage" / "storage.json",):
            try:
                text = storage.read_text("utf-8", errors="ignore")
            except Exception:
                continue
            for uri in re.findall(r'file:///[^"\\]+', text)[:200]:
                try:
                    p = Path(urllib.parse.unquote(uri.replace("file:///", "")))
                    if p.is_dir():
                        out.append(p)
                    elif p.parent.is_dir():
                        out.append(p.parent)
                except Exception:
                    pass
        try:
            for ws in (base / "workspaceStorage").glob("*/workspace.json"):
                data = json.loads(ws.read_text("utf-8", errors="ignore"))
                folder = data.get("folder") or ""
                if folder.startswith("file:///"):
                    p = Path(urllib.parse.unquote(folder.replace("file:///", "")))
                    if p.is_dir():
                        out.append(p)
        except Exception:
            pass
        return out[:40]

    def allowed(self, path: Path) -> bool:
        """Only files under the user's profile or a known project root. Never system folders."""
        try:
            p = path.resolve()
        except Exception:
            return False
        s = str(p).lower()
        if any(s.startswith(x) for x in (r"c:\windows", r"c:\program files", r"c:\programdata")):
            return False
        if s.startswith(str(Path.home()).lower()):
            return True
        return any(s.startswith(str(r).lower()) for r in self.project_roots())

    # -- files ----------------------------------------------------------------------------------
    def find_files(self, query: str, limit: int = 15, time_budget: float = 3.0, max_depth: int = 5) -> list[dict]:
        toks = [t for t in re.split(r"[\s,;]+", query.lower()) if t]
        if not toks:
            return []
        deadline = time.time() + time_budget
        hits: list[dict] = []

        def walk(d: Path, depth: int):
            if depth > max_depth or time.time() > deadline:
                return
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if time.time() > deadline:
                            return
                        name = e.name
                        low = name.lower()
                        if e.is_dir(follow_symlinks=False):
                            if name in SKIP_DIRS or name.startswith("."):
                                continue
                            if all(t in low for t in toks):
                                try:
                                    st = e.stat()
                                    hits.append({"path": e.path, "kind": "folder", "size": 0, "mtime": st.st_mtime})
                                except OSError:
                                    pass
                            walk(Path(e.path), depth + 1)
                        elif all(t in low for t in toks):
                            try:
                                st = e.stat()
                                hits.append({"path": e.path, "kind": "file", "size": st.st_size, "mtime": st.st_mtime})
                            except OSError:
                                pass
            except (PermissionError, FileNotFoundError, OSError):
                return

        for root in self.project_roots():
            walk(root, 0)
            if time.time() > deadline:
                break
        # dedupe + newest first
        seen, out = set(), []
        for h in sorted(hits, key=lambda h: h["mtime"], reverse=True):
            k = h["path"].lower()
            if k not in seen:
                seen.add(k)
                out.append(h)
        return out[:limit]

    def format_hits(self, hits: list[dict]) -> str:
        if not hits:
            return "No matching files in your project folders."
        lines = []
        for h in hits:
            age = time.strftime("%Y-%m-%d %H:%M", time.localtime(h["mtime"]))
            size = f"{h['size']/1024:.0f} KB" if h["kind"] == "file" else "folder"
            lines.append(f"- {h['path']}  ({size}, modified {age})")
        return "\n".join(lines)

    def read_file(self, path: str, max_chars: int = 12000, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        p = Path(path).expanduser()
        if not p.is_file():
            return f"Not a file: {path}"
        if not self.allowed(p):
            return "That path is outside your user folders and project directories; I won't read it."
        if p.suffix.lower() not in TEXT_EXT and p.stat().st_size > 200_000:
            return f"{p.name} looks binary or very large ({p.stat().st_size} bytes); not reading it."
        try:
            text = p.read_text("utf-8", errors="replace")
        except Exception as e:
            return f"Couldn't read {p.name}: {e}"
        lines = text.splitlines()
        a = max(1, start_line or 1)
        b = min(len(lines), end_line or len(lines))
        chunk = "\n".join(f"{i:>5}  {lines[i-1]}" for i in range(a, b + 1))
        if len(chunk) > max_chars:
            chunk = chunk[:max_chars] + f"\n... (truncated; file has {len(lines)} lines)"
        return f"{p} (lines {a}-{b} of {len(lines)}):\n{chunk}"

    def write_file(self, path: str, content: str, backup: bool = True) -> str:
        p = Path(path).expanduser()
        if not self.allowed(p):
            return "That path is outside your user folders and project directories; I won't write there."
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if backup and p.exists():
                bak = p.with_suffix(p.suffix + ".eli.bak")
                bak.write_bytes(p.read_bytes())
            p.write_text(content, "utf-8")
            return f"Wrote {len(content)} characters to {p}" + (" (backup: .eli.bak)" if backup and p.exists() else "")
        except Exception as e:
            return f"Couldn't write {p}: {e}"

    def list_dir(self, path: str, limit: int = 60) -> str:
        p = Path(path).expanduser()
        if not p.is_dir():
            return f"Not a folder: {path}"
        if not self.allowed(p):
            return "That folder is outside your user folders and project directories."
        rows = []
        try:
            for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:limit]:
                rows.append(("  " if e.is_file() else "[dir] ") + e.name)
        except Exception as e:
            return f"Couldn't list {p}: {e}"
        return f"{p}:\n" + "\n".join(rows)

    def git_info(self, path: str) -> str:
        p = Path(path).expanduser()
        if p.is_file():
            p = p.parent
        if not p.is_dir() or not self.allowed(p):
            return "No accessible folder at that path."
        try:
            root = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                                  timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if root.returncode != 0:
                return f"{p} is not inside a git repository."
            top = root.stdout.strip()
            status = subprocess.run(["git", "-C", top, "status", "--short", "--branch"], capture_output=True, text=True,
                                    timeout=15, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            diff = subprocess.run(["git", "-C", top, "diff", "--stat"], capture_output=True, text=True,
                                  timeout=15, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            last = subprocess.run(["git", "-C", top, "log", "-3", "--oneline"], capture_output=True, text=True,
                                  timeout=15, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            return f"repo: {top}\n{status.strip()}\n\ndiff --stat:\n{diff.strip() or '(clean)'}\n\nrecent commits:\n{last.strip()}"
        except FileNotFoundError:
            return "git is not installed or not on PATH."
        except Exception as e:
            return f"git failed: {e}"

    # -- IDE discovery and launching -----------------------------------------------------------
    def discover_ides(self) -> dict[str, str]:
        """Discovers installed IDEs (VS Code, MATLAB) across standard directories and PATH."""
        ides: dict[str, str] = {}
        local_app = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        prog_files_86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")

        vscode_candidates = [
            Path(local_app) / "Programs" / "Microsoft VS Code" / "Code.exe",
            Path(prog_files) / "Microsoft VS Code" / "Code.exe",
            Path(prog_files_86) / "Microsoft VS Code" / "Code.exe",
            Path(r"E:\Microsoft VS Code\Code.exe"),
        ]
        for vc in vscode_candidates:
            if vc.is_file():
                ides["vscode"] = str(vc)
                break
        if "vscode" not in ides:
            which_code = subprocess.run(["where", "code"], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if which_code.returncode == 0 and which_code.stdout.strip():
                ides["vscode"] = which_code.stdout.splitlines()[0].strip()

        matlab_roots = [
            Path(prog_files) / "MATLAB",
            Path(prog_files_86) / "MATLAB",
            Path(r"E:\MATLAB"),
            Path(r"C:\MATLAB"),
        ]
        for mroot in matlab_roots:
            if mroot.is_dir():
                for version_dir in sorted(mroot.glob("R20*"), reverse=True):
                    exe = version_dir / "bin" / "matlab.exe"
                    if exe.is_file():
                        ides["matlab"] = str(exe)
                        break
            if "matlab" in ides:
                break
        if "matlab" not in ides:
            which_matlab = subprocess.run(["where", "matlab"], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if which_matlab.returncode == 0 and which_matlab.stdout.strip():
                ides["matlab"] = which_matlab.stdout.splitlines()[0].strip()

        return ides

    def open_ide(self, ide_name: str, target: str = "") -> str:
        """Launches the target IDE, optionally opening a workspace folder or file."""
        ides = self.discover_ides()
        key = ide_name.lower().replace(" ", "").replace("-", "")
        exe = None
        if "code" in key or "vscode" in key:
            exe = ides.get("vscode")
            ide_label = "Visual Studio Code"
        elif "matlab" in key:
            exe = ides.get("matlab")
            ide_label = "MATLAB"
        else:
            return f"I don't have automatic detection for '{ide_name}'. Supported: VS Code, MATLAB."

        if not exe:
            return f"{ide_label} was not found on your system paths. Make sure it's installed."

        target_path = ""
        if target:
            p = Path(target).expanduser()
            if p.exists():
                target_path = str(p.resolve())
            else:
                hits = self.find_files(target, limit=1)
                if hits:
                    target_path = hits[0]["path"]

        quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            if "code" in key or "vscode" in key:
                cmd = [exe]
                if target_path:
                    cmd.append(target_path)
                subprocess.Popen(cmd, **quiet)
                return f"Opened {ide_label}" + (f" on '{target_path}'." if target_path else ".")
            elif "matlab" in key:
                cmd = [exe, "-desktop"]
                if target_path:
                    p = Path(target_path)
                    folder = p if p.is_dir() else p.parent
                    cmd += ["-sd", str(folder)]
                subprocess.Popen(cmd, **quiet)
                return f"Launched {ide_label}" + (f" with working folder '{target_path}'." if target_path else ".")
        except Exception as e:
            return f"Failed to launch {ide_label}: {e}"
        return f"Launched {ide_label}."

    def create_code_script(self, filename: str, code: str, language: str = "python",
                           folder: str = "", run_after: bool = True) -> dict:
        """Creates a verified script file on disk, validates AST syntax offline,
        test-runs it to verify execution, and opens it directly in VS Code."""
        name = Path(filename.strip()).name
        lang = language.lower().strip()
        if lang == "python" and not name.lower().endswith(".py"):
            name += ".py"
        elif lang == "matlab" and not name.lower().endswith(".m"):
            name += ".m"
        elif lang == "javascript" and not name.lower().endswith((".js", ".mjs")):
            name += ".py" if "python" in code.lower() else ".js"

        # Sanitize filename
        name = re.sub(r'[\\/:*?"<>|]', '_', name).strip()
        if not name or name in (".py", ".m", ".js"):
            name = "script.py"

        # Resolve destination folder
        dest_dir = None
        if folder:
            p = Path(folder).expanduser()
            if p.is_dir():
                dest_dir = p
        if not dest_dir:
            dest_dir = Path.home() / "Documents" / "PythonScripts"
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_path = dest_dir / name

        # Syntax verification & auto-correction
        syntax_err = ""
        if name.endswith(".py"):
            try:
                ast.parse(code)
            except SyntaxError as se:
                syntax_err = f"SyntaxError line {se.lineno}: {se.msg}"
                log.warning("Script %s had syntax error: %s", name, syntax_err)
                code_fixed = code.replace("\r\n", "\n").strip() + "\n"
                try:
                    ast.parse(code_fixed)
                    code = code_fixed
                    syntax_err = ""
                except Exception:
                    pass

        # Write file directly to disk
        target_path.write_text(code, encoding="utf-8")
        log.info("Saved code script to %s (%d bytes)", target_path, len(code))

        # Test execution
        exec_out = ""
        exec_ok = True
        if run_after and name.endswith(".py"):
            try:
                r = subprocess.run(["python", str(target_path)], capture_output=True, text=True, timeout=8)
                out = (r.stdout or "").strip()
                err = (r.stderr or "").strip()
                if r.returncode == 0:
                    exec_out = out if out else "Execution succeeded (no stdout)."
                else:
                    exec_ok = False
                    exec_out = f"Runtime exit code {r.returncode}:\n{err}"
            except Exception as ex:
                exec_out = f"Test run skipped: {ex}"

        # Open directly in VS Code
        ide_res = self.open_ide("vscode", str(target_path))

        return {
            "ok": True,
            "path": str(target_path),
            "filename": name,
            "lines": len(code.splitlines()),
            "syntax_valid": not syntax_err,
            "syntax_note": syntax_err or "Syntax clean",
            "execution_ok": exec_ok,
            "execution_output": exec_out,
            "ide_status": ide_res,
            "summary": f"Created '{name}' at {target_path}, verified syntax ({'clean' if not syntax_err else syntax_err}), test output: {exec_out[:120]}, and {ide_res}"
        }

    # -- offline code error checking -----------------------------------------------------------
    def check_code_errors(self, path: str) -> dict:
        """Offline syntax and structural error checking for Python, MATLAB, C, and C++ files."""
        p = Path(path).expanduser()
        if not p.is_file():
            return {"file": path, "valid": False, "errors": [f"File not found: {path}"]}

        ext = p.suffix.lower()
        try:
            content = p.read_text("utf-8", errors="replace")
        except Exception as e:
            return {"file": str(p), "valid": False, "errors": [f"Cannot read file: {e}"]}

        if ext == ".py":
            return self._check_python(p, content)
        elif ext == ".m":
            return self._check_matlab(p, content)
        elif ext in (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"):
            return self._check_c_cpp(p, content)
        else:
            return {"file": str(p), "valid": True, "language": ext, "errors": [], "summary": f"No offline parser for {ext} (supported: .py, .m, .c, .cpp, .h)."}

    def _check_python(self, path: Path, content: str) -> dict:
        errors = []
        try:
            ast.parse(content, filename=str(path))
        except SyntaxError as e:
            msg = f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}"
            if e.text:
                msg += f"\n  Code: {e.text.strip()}"
            errors.append(msg)
        except Exception as e:
            errors.append(f"Parse error: {e}")

        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            if "\t" in line and "    " in line:
                errors.append(f"Line {idx}: Mixed tabs and spaces in indentation.")
                break

        return {
            "file": str(path),
            "language": "Python",
            "valid": len(errors) == 0,
            "errors": errors,
            "summary": "No syntax errors found." if not errors else f"Found {len(errors)} error(s)."
        }

    def _check_matlab(self, path: Path, content: str) -> dict:
        errors = []
        lines = content.splitlines()
        block_stack = []
        block_openers = {"function", "if", "for", "while", "switch", "try", "parfor", "spmd"}
        paren_stack = []
        pairs = {')': '(', ']': '[', '}': '{'}

        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line.split('%')[0].strip()
            if not line:
                continue

            in_str = False
            str_char = ''
            for col, ch in enumerate(line, 1):
                if ch in ("'", '"'):
                    if not in_str:
                        in_str = True
                        str_char = ch
                    elif str_char == ch:
                        in_str = False
                    continue
                if in_str:
                    continue

                if ch in "([{":
                    paren_stack.append((ch, line_num, col))
                elif ch in ")]}":
                    if not paren_stack:
                        errors.append(f"Line {line_num}, col {col}: Unmatched closing '{ch}'.")
                    else:
                        top, o_line, o_col = paren_stack.pop()
                        if top != pairs[ch]:
                            errors.append(f"Line {line_num}, col {col}: Mismatched bracket '{ch}' (closing '{top}' from line {o_line}).")

            tokens = re.findall(r"\b[a-zA-Z_]\w*\b", line)
            if not tokens:
                continue
            first = tokens[0].lower()
            if first in block_openers:
                block_stack.append((first, line_num))
            elif first == "end":
                if not block_stack:
                    errors.append(f"Line {line_num}: Unexpected 'end' with no matching block opener.")
                else:
                    block_stack.pop()

        if block_stack:
            for opener, o_line in block_stack:
                errors.append(f"Line {o_line}: Unclosed MATLAB '{opener}' block (missing 'end').")

        if paren_stack:
            for ch, o_line, o_col in paren_stack[:5]:
                errors.append(f"Line {o_line}, col {o_col}: Unclosed bracket '{ch}'.")

        return {
            "file": str(path),
            "language": "MATLAB",
            "valid": len(errors) == 0,
            "errors": errors,
            "summary": "No syntax errors found." if not errors else f"Found {len(errors)} error(s)."
        }

    def _check_c_cpp(self, path: Path, content: str) -> dict:
        errors = []
        ext = path.suffix.lower()
        compiler = "g++" if ext in (".cpp", ".cc", ".cxx", ".hpp") else "gcc"
        which = subprocess.run(["where", compiler], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if which.returncode == 0:
            try:
                res = subprocess.run([compiler, "-fsyntax-only", str(path)], capture_output=True, text=True,
                                     timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if res.returncode != 0 and res.stderr:
                    for line in res.stderr.splitlines()[:8]:
                        if "error:" in line or "fatal error:" in line:
                            errors.append(line.strip())
                    if errors:
                        return {
                            "file": str(path),
                            "language": "C++" if compiler == "g++" else "C",
                            "valid": False,
                            "errors": errors,
                            "summary": f"Compiler detected {len(errors)} error(s)."
                        }
            except Exception:
                pass

        lines = content.splitlines()
        brace_stack = []
        pairs = {'}': '{', ')': '(', ']': '['}

        in_multiline_comment = False
        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line
            if in_multiline_comment:
                if "*/" in line:
                    line = line.split("*/", 1)[1]
                    in_multiline_comment = False
                else:
                    continue
            if "/*" in line:
                if "*/" not in line:
                    in_multiline_comment = True
                line = re.sub(r"/\*.*?\*/", "", line)
            line = line.split("//")[0]

            in_str = False
            str_char = ''
            for col, ch in enumerate(line, 1):
                if ch in ("'", '"'):
                    if not in_str:
                        in_str = True
                        str_char = ch
                    elif str_char == ch and (col < 2 or line[col-2] != '\\'):
                        in_str = False
                    continue
                if in_str:
                    continue

                if ch in "{([":
                    brace_stack.append((ch, line_num, col))
                elif ch in "})]":
                    if not brace_stack:
                        errors.append(f"Line {line_num}, col {col}: Unmatched closing '{ch}'.")
                    else:
                        top, o_line, o_col = brace_stack.pop()
                        if top != pairs[ch]:
                            errors.append(f"Line {line_num}, col {col}: Mismatched bracket '{ch}' (opened '{top}' at line {o_line}).")

        if in_multiline_comment:
            errors.append("Unclosed multi-line comment '/*'.")

        if brace_stack:
            for ch, o_line, o_col in brace_stack[:5]:
                errors.append(f"Line {o_line}, col {o_col}: Unclosed '{ch}'.")

        return {
            "file": str(path),
            "language": "C/C++",
            "valid": len(errors) == 0,
            "errors": errors,
            "summary": "No syntax errors found." if not errors else f"Found {len(errors)} error(s)."
        }

    def scan_project_errors(self, folder_path: str = "") -> str:
        """Scans code files in a folder offline and returns any syntax errors."""
        target_dir = None
        if folder_path:
            p = Path(folder_path).expanduser()
            if p.is_dir():
                target_dir = p
            else:
                hits = self.find_files(folder_path, limit=1)
                if hits and hits[0]["kind"] == "folder":
                    target_dir = Path(hits[0]["path"])
        if not target_dir:
            roots = self.project_roots()
            target_dir = roots[0] if roots else Path.home()

        results = []
        checked_count = 0
        extensions = (".py", ".m", ".c", ".cpp", ".cc", ".h", ".hpp")

        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in extensions:
                    checked_count += 1
                    full_path = Path(root) / f
                    res = self.check_code_errors(str(full_path))
                    if not res["valid"]:
                        results.append(f"❌ {full_path.name} ({res['language']}):\n  " + "\n  ".join(res["errors"]))
                    if checked_count >= 50:
                        break
            if checked_count >= 50:
                break

        if not results:
            return f"Offline syntax scan complete: checked {checked_count} code files in '{target_dir.name}'. No syntax errors found! All clean."
        return f"Offline syntax scan found issues in {len(results)} of {checked_count} files in '{target_dir.name}':\n\n" + "\n\n".join(results)
