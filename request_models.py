from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class PlanRequest(BaseModel):
    fest_title: str = Field(..., description="시작 지점/행사 명칭")
    fest_location_text: str = Field(..., description="시작 지점 주소 또는 명칭")
    travel_needs: Dict[str, Any] = Field(
        ..., description="{start_at,end_at,categories[,budget]}"
    )
