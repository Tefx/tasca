"""Authentication probe routes."""

from typing import Literal

from pydantic import BaseModel

from tasca.shell.api.auth import validate_authentication_token
from tasca.shell.api.fastapi_compat import APIRouter, Depends

router = APIRouter()


class AuthValidationResponse(BaseModel):
    """The role associated with a validated REST credential."""

    role: Literal["viewer", "admin"]


@router.get("/validate", response_model=AuthValidationResponse)
async def validate_authentication_endpoint(
    role: Literal["viewer", "admin"] = Depends(validate_authentication_token),
) -> AuthValidationResponse:
    """Validate a credential without changing service state."""
    return AuthValidationResponse(role=role)
