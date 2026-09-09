"""Start the Eli backend:  python run.py"""
import uvicorn

from eli.config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run("eli.main:app", host=HOST, port=PORT, log_level="info")
