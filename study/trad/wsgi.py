"""Render 등 서버 실행용: gunicorn --workers 1 --threads 8 wsgi:app"""
from app import create_app

app = create_app()
