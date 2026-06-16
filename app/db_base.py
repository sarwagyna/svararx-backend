"""SQLAlchemy declarative base — no settings or engine initialization."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
