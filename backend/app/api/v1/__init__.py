from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, me, plans

router = APIRouter()
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(plans.router)
from app.api.v1 import admin, ask, billing, graph, library, research  # noqa: E402

router.include_router(library.router)
router.include_router(ask.router)
router.include_router(graph.router)
router.include_router(research.router)
router.include_router(billing.router)
router.include_router(admin.router)
