"""배포용 진입점 (Render 등): gunicorn wsgi:app"""
from app import create_app

app = create_app()
