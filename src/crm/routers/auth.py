from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crm.database import get_session
from crm.dependencies import get_current_user
from crm.models import User
from crm.schemas import Token, UserRead
from crm.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
async def login(
    form: OAuth2PasswordRequestForm = Depends(), session: AsyncSession = Depends(get_session)
) -> Token:
    user = await session.scalar(select(User).where(User.email == form.username.lower()))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
