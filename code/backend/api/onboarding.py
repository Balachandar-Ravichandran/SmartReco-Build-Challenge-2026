"""POST /api/v1/onboarding, GET /api/v1/onboarding/me (Section 14.1)."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.core.config import get_settings
from backend.core.schemas import OnboardingRequest, OnboardingResponse
from backend.db.models import UserOnboarding

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


@router.post("", response_model=OnboardingResponse, status_code=201)
def create_onboarding(body: OnboardingRequest, db: Session = Depends(get_db)):
    settings = get_settings()

    if not (1 <= len(body.selected_topics) <= 5):
        raise HTTPException(422, "selected_topics must contain 1-5 items")

    invalid = set(body.selected_topics) - set(settings.TOPIC_VOCABULARY)
    if invalid:
        raise HTTPException(
            422,
            f"Topics outside controlled vocabulary: {sorted(invalid)}. "
            f"Allowed: {settings.TOPIC_VOCABULARY}",
        )

    if body.goal not in settings.GOAL_VOCABULARY:
        raise HTTPException(
            422,
            f"Goal must be one of: {settings.GOAL_VOCABULARY}",
        )

    row = UserOnboarding(
        user_id=body.user_id,
        selected_topics=json.dumps(body.selected_topics),
        goal=body.goal,
    )
    db.add(row)
    db.flush()

    return OnboardingResponse(onboarding_id=row.id)


@router.get("/me")
def get_my_onboarding(user_id: str, db: Session = Depends(get_db)):
    row = (
        db.query(UserOnboarding)
        .filter(UserOnboarding.user_id == user_id)
        .order_by(UserOnboarding.created_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(404, "No onboarding record found for this user")

    return {
        "onboarding_id": row.id,
        "selected_topics": json.loads(row.selected_topics),
        "goal": row.goal,
        "created_at": row.created_at.isoformat(),
    }
