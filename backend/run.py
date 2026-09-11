"""Start the Eli backend:  python run.py"""
import os
import sys

# The embeddable Python used by the installed app controls sys.path itself via a ._pth file and does
# not add the script's own directory automatically (unlike a normal install) — do it explicitly so
# `eli` is importable regardless of who launches this file or what the working directory is.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.stdout is None or sys.stderr is None:
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(log_dir, exist_ok=True)
    f = open(os.path.join(log_dir, "server.log"), "a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f

import uvicorn

from eli.config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run("eli.main:app", host=HOST, port=PORT, log_level="info")
