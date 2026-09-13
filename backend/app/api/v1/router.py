from fastapi import APIRouter

from app.api.v1 import accounts, transfers

api_router = APIRouter(prefix="/v1")
api_router.include_router(accounts.router)
api_router.include_router(transfers.router)
