"""Book catalogue operations."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Book
from app.schemas import BookCreate, BookPage, BookSort, BookUpdate
from app.services.common import commit_or_conflict

# Column ordering for each accepted ``sort`` value; ties are always broken by id.
SORT_ORDERS = {
    "title": Book.title.asc(),
    "-title": Book.title.desc(),
    "price": Book.price_cents.asc(),
    "-price": Book.price_cents.desc(),
}


def create_book(db: Session, data: BookCreate) -> Book:
    """Add a book to the catalogue.

    Rules: the (already normalized) ISBN must be unique -> 409 otherwise.
    """
    book = Book(**data.model_dump())
    db.add(book)
    # books.isbn is the only unique constraint this insert can violate.
    commit_or_conflict(db, "A book with this ISBN already exists")
    db.refresh(book)
    return book


def get_book(db: Session, book_id: int) -> Book:
    """Return a book by id, or raise 404."""
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


def update_book(db: Session, book_id: int, data: BookUpdate) -> Book:
    """Apply a partial update. Only fields present in the request are changed; 404 if missing."""
    book = get_book(db, book_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(book, field, value)
    db.commit()
    db.refresh(book)
    return book


def list_books(
    db: Session,
    q: Optional[str] = None,
    restricted: Optional[bool] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    sort: Optional[BookSort] = None,
    limit: int = 20,
    offset: int = 0,
) -> BookPage:
    """Search the catalogue.

    Rules:
    - ``q`` matches title OR author, case-insensitive substring.
    - ``restricted`` filters exactly; ``min_price``/``max_price`` are inclusive.
    - Sorted by ``sort`` (title / price, ``-`` for descending) with ties broken by id;
      default order is id ascending.
    - ``total`` counts all matches before ``limit``/``offset`` are applied.
    """
    filters = []
    if q:
        filters.append(
            or_(Book.title.icontains(q, autoescape=True), Book.author.icontains(q, autoescape=True))
        )
    if restricted is not None:
        filters.append(Book.restricted == restricted)
    if min_price is not None:
        filters.append(Book.price_cents >= min_price)
    if max_price is not None:
        filters.append(Book.price_cents <= max_price)

    # Counted separately so ``total`` reflects the filters, not the current page.
    total = db.scalar(select(func.count()).select_from(Book).where(*filters))
    order_by = [SORT_ORDERS[sort]] if sort else []
    order_by.append(Book.id.asc())
    books = db.scalars(
        select(Book).where(*filters).order_by(*order_by).limit(limit).offset(offset)
    ).all()

    return BookPage(items=books, total=total, limit=limit, offset=offset)
