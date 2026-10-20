"""Vercel ASGI entrypoint. Set PUBLIC_BASE_URL, REDIS_URL and OPENAI_MODEL."""
from fastapi import FastAPI
from seller import app as seller_app

app = FastAPI()
app.mount("/", seller_app)