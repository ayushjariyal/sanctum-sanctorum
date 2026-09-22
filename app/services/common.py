"""Helpers shared by more than one service module."""
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def commit_or_conflict(db: Session, detail: str) -> None:
    """Commit the session, translating a unique-constraint violation into a 409.

    Letting the database detect the clash keeps the check atomic: a prior ``SELECT``
    would leave a window in which two concurrent requests both see no match.
    """
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=detail) from None
