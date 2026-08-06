"""Signup / login stub (Section 4.1, 15). No real session/JWT validation beyond
existence — deferred per Section 15's explicit scoping.

Core hash/create/authenticate functions are reused by both the JSON API
below and the web login/signup/logout routes in app/main.py, so there's one
password-checking implementation, not two."""
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.db.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: str
    password: str
    role: str = "learner"


class LoginRequest(BaseModel):
    email: str
    password: str


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_user_row(db: Session, email: str, password: str, role: str) -> User:
    if role not in ("learner", "admin"):
        raise ValueError("role must be 'learner' or 'admin'")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError("Email already registered")

    user = User(id=str(uuid.uuid4()), email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).first()
    if not user or user.password_hash != hash_password(password):
        return None
    return user


@router.post("/signup", status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    try:
        user = create_user_row(db, body.email, body.password, body.role)
    except ValueError as e:
        code = 409 if "registered" in str(e) else 422
        raise HTTPException(code, str(e))

    return {"user_id": user.id, "role": user.role}


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, body.email, body.password)
    if not user:
        raise HTTPException(401, "Invalid credentials")

    return {"user_id": user.id, "role": user.role}
