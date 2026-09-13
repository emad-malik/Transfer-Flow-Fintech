from fastapi import APIRouter

from app.api.v1 import accounts

api_router = APIRouter(prefix="/v1")
api_router.include_router(accounts.router)
