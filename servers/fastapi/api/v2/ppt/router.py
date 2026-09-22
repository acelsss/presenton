from fastapi import APIRouter, Depends
from services.agent_tools.native_policy import require_native_capability

from api.v2.ppt.endpoints.presentation import PRESENTATION_V2_ROUTER


API_V2_PPT_ROUTER = APIRouter(prefix="/ppt", dependencies=[Depends(require_native_capability)])
API_V2_PPT_ROUTER.include_router(PRESENTATION_V2_ROUTER)
