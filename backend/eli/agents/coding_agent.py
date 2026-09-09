"""Coding Agent: VS Code / terminal awareness, project file access, and the
error -> analyse -> fix -> test -> verify workflow.

The model does the reasoning; this module gives it eyes and hands inside the project:
- what VS Code is showing (file, workspace, language, unsaved state) from the window title
- where projects live (Documents/Desktop/Downloads, ELI_PROJECT_DIRS, VS Code's recent workspaces)
- find / read / write files (writes are confirmation-gated and backed up), git status, list dirs
"""
from __future__ import annotations

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
