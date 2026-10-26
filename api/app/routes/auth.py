"""Accounts: sign up, verify email with a 6-digit code, log in, reset password.

Flow the web app follows:
  1. POST /auth/signup          -> a code is emailed
  2. POST /auth/verify-email    -> returns a login token
  3. later: POST /auth/login    -> returns a login token (only for verified emails)
  4. every other request sends:  Authorization: Bearer <token>
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..emailer import send_email
from ..models import EmailCode, User
from ..security import (code_matches, create_access_token, decode_access_token, hash_code,
                       hash_password, new_code, verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY_HASH = hash_password("timing-equaliser")


# ---------- request / response shapes (this is the contract with the web app) ----------
class SignupIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class EmailIn(BaseModel):
    email: EmailStr


class VerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ResetIn(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    email_verified: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- helpers ----------
def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops the timezone


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, email_verified=u.email_verified)


def _token(u: User) -> TokenOut:
    return TokenOut(access_token=create_access_token(u.id, u.token_version), user=_user_out(u))


def _find(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def _issue_code(db: Session, user: User, purpose: str) -> bool:
    """Email a fresh code. Returns False (and sends nothing) during the resend cooldown."""
    now = datetime.now(timezone.utc)
    last = db.scalar(select(EmailCode).where(EmailCode.user_id == user.id, EmailCode.purpose == purpose)
                     .order_by(EmailCode.id.desc()))
    if last and (now - _utc(last.created_at)).total_seconds() < config.RESEND_COOLDOWN_SECONDS:
        return False
    db.execute(update(EmailCode).where(EmailCode.user_id == user.id, EmailCode.purpose == purpose,
                                       EmailCode.used.is_(False)).values(used=True))
    code = new_code()
    db.add(EmailCode(user_id=user.id, purpose=purpose, code_hash=hash_code(code),
                     expires_at=now + timedelta(minutes=config.CODE_TTL_MINUTES)))
    db.commit()
    if purpose == "verify":
        subject, what = "Your TravelSync verification code", "verify your email"
    else:
        subject, what = "Your TravelSync password reset code", "reset your password"
    send_email(user.email, subject,
               f"Hi {user.name},\n\nYour code to {what} is: {code}\n\n"
               f"It expires in {config.CODE_TTL_MINUTES} minutes. If you didn't ask for it, ignore this email.")
    return True


def _check_code(db: Session, user: User, purpose: str, code: str) -> None:
    rec = db.scalar(select(EmailCode).where(EmailCode.user_id == user.id, EmailCode.purpose == purpose,
                                            EmailCode.used.is_(False)).order_by(EmailCode.id.desc()))
    if not rec or _utc(rec.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(400, "code_expired")
    if rec.attempts >= config.CODE_MAX_ATTEMPTS:
        raise HTTPException(400, "too_many_attempts")
    if not code_matches(code, rec.code_hash):
        rec.attempts += 1
        db.commit()
        raise HTTPException(400, "wrong_code")
    rec.used = True
    db.commit()


# ---------- the dependency every protected endpoint uses ----------
_bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                 db: Session = Depends(get_db)) -> User:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_logged_in")
    try:
        data = decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_or_expired_token")
    user = db.get(User, data.get("sub"))
    if not user or user.token_version != data.get("ver") or not user.email_verified:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_or_expired_token")
    return user


# ---------- endpoints ----------
@router.post("/signup", status_code=201)
def signup(body: SignupIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    user = _find(db, email)
    if user and user.email_verified:
        raise HTTPException(409, "email_already_registered")
    if user:  # signed up before but never verified: let them start over
        user.name, user.password_hash = body.name, hash_password(body.password)
    else:
        user = User(email=email, name=body.name, password_hash=hash_password(body.password))
        db.add(user)
    db.commit()
    _issue_code(db, user, "verify")
    return {"message": "verification_code_sent", "email": email}


@router.post("/verify-email", response_model=TokenOut)
def verify_email(body: VerifyIn, db: Session = Depends(get_db)):
    user = _find(db, body.email)
    if not user:
        raise HTTPException(400, "code_expired")
    if user.email_verified:
        raise HTTPException(409, "already_verified")
    _check_code(db, user, "verify", body.code)
    user.email_verified = True
    db.commit()
    return _token(user)


@router.post("/resend-code")
def resend_code(body: EmailIn, db: Session = Depends(get_db)):
    user = _find(db, body.email)
    if user and not user.email_verified:
        _issue_code(db, user, "verify")
    return {"message": "if_the_account_exists_a_code_was_sent"}  # same answer either way


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = _find(db, body.email)
    if not user:
        verify_password(_DUMMY_HASH, body.password)  # same timing as a real check
        raise HTTPException(401, "wrong_email_or_password")
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "wrong_email_or_password")
    if not user.email_verified:
        _issue_code(db, user, "verify")
        raise HTTPException(403, "email_not_verified")
    return _token(user)


@router.post("/forgot-password")
def forgot_password(body: EmailIn, db: Session = Depends(get_db)):
    user = _find(db, body.email)
    if user and user.email_verified:
        _issue_code(db, user, "reset")
    return {"message": "if_the_account_exists_a_code_was_sent"}


@router.post("/reset-password", response_model=TokenOut)
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    user = _find(db, body.email)
    if not user or not user.email_verified:
        raise HTTPException(400, "code_expired")
    _check_code(db, user, "reset", body.code)
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1  # logs out every other device
    db.commit()
    return _token(user)


@router.post("/logout-all")
def logout_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.token_version += 1
    db.commit()
    return {"message": "logged_out_everywhere"}


me_router = APIRouter(tags=["auth"])


@me_router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return _user_out(user)
