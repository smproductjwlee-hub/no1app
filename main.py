"""
로컬 실행: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from app.main import app

__all__ = ["app"]
