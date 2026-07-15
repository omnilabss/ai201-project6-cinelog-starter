"""
tests/test_watchlist.py — CineLog

Tests for the watchlist service. Follows the same fixture and assertion
structure as tests/test_collection.py.
"""

import pytest
from app import create_app, db
from models import User, Film, WatchlistEntry
from datetime import datetime, timezone, timedelta
from services.watchlist_service import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    AlreadyOnWatchlistError,
    NotOnWatchlistError,
)
from services.collection_service import FilmNotFoundError, add_to_collection


@pytest.fixture
def app():
    """Create an isolated test app with an in-memory database."""
    app = create_app(config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def sample_user(app):
    """A user to use in tests."""
    with app.app_context():
        user = User(username="testuser", email="test@example.com")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def sample_film(app):
    """A film to use in tests."""
    with app.app_context():
        film = Film(title="Paddington 2", year=2017, genre="Comedy")
        db.session.add(film)
        db.session.commit()
        return film.id


# ── Basic add ───────────────────────────────────────────────────────────────

def test_add_to_watchlist_creates_entry(app, sample_user, sample_film):
    """
    Adding a valid film should create a WatchlistEntry in the database.
    """
    with app.app_context():
        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film)

        assert entry is not None
        assert entry.user_id == sample_user
        assert entry.film_id == sample_film

        # Verify it persisted
        in_db = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).first()
        assert in_db is not None


def test_add_to_watchlist_public_defaults_true_and_can_be_overridden(app, sample_user, sample_film):
    """
    add_to_watchlist() should default public=True but allow callers to
    opt an entry into private visibility explicitly.
    """
    with app.app_context():
        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film)
        assert entry.public is True

        film_b = Film(title="Private Watch", year=2021, genre="Drama")
        db.session.add(film_b)
        db.session.commit()

        private_entry = add_to_watchlist(user_id=sample_user, film_id=film_b.id, public=False)
        assert private_entry.public is False


# ── Deduplication ────────────────────────────────────────────────────────────

def test_add_to_watchlist_duplicate_raises(app, sample_user, sample_film):
    """
    Adding the same film twice should raise AlreadyOnWatchlistError,
    not silently create a duplicate entry.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        with pytest.raises(AlreadyOnWatchlistError):
            add_to_watchlist(user_id=sample_user, film_id=sample_film)

        # Confirm only one entry exists
        count = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).count()
        assert count == 1


# ── Nonexistent film ─────────────────────────────────────────────────────────

def test_add_to_watchlist_nonexistent_film_raises(app, sample_user):
    """
    Adding a film_id that doesn't exist in the database should raise
    FilmNotFoundError, not a database integrity error.
    """
    with app.app_context():
        fake_film_id = 999999

        with pytest.raises(FilmNotFoundError):
            add_to_watchlist(user_id=sample_user, film_id=fake_film_id)


# ── Remove ────────────────────────────────────────────────────────────────────

def test_remove_from_watchlist_deletes_entry(app, sample_user, sample_film):
    """
    Removing a film that's on the watchlist should delete its entry.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        result = remove_from_watchlist(user_id=sample_user, film_id=sample_film)
        assert result is True

        in_db = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).first()
        assert in_db is None


def test_remove_from_watchlist_not_present_raises(app, sample_user, sample_film):
    """
    Removing a film that isn't on the watchlist should raise
    NotOnWatchlistError rather than silently doing nothing.
    """
    with app.app_context():
        with pytest.raises(NotOnWatchlistError):
            remove_from_watchlist(user_id=sample_user, film_id=sample_film)


# ── Cross-feature independence (edge case) ───────────────────────────────────

def test_add_to_watchlist_independent_of_collection(app, sample_user, sample_film):
    """
    A film already logged in a user's collection (already watched) should
    still be addable to that same user's watchlist, and vice versa — the
    two features have separate dedup checks (AlreadyInCollectionError vs.
    AlreadyOnWatchlistError) backed by separate tables, so one shouldn't
    block the other. This is the kind of coupling bug a naive shared
    "already logged this film" check could accidentally introduce.
    """
    with app.app_context():
        add_to_collection(user_id=sample_user, film_id=sample_film)

        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film)
        assert entry is not None

        on_watchlist = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).first()
        assert on_watchlist is not None


# ── Sort order ────────────────────────────────────────────────────────────────

def test_get_watchlist_default_sort_is_alphabetical(app, sample_user):
    """
    get_watchlist() should default to alphabetical-by-title order,
    matching how films are browsed elsewhere in the app (routes/films.py).
    """
    with app.app_context():
        from models import Film

        film_z = Film(title="Zodiac", year=2007, genre="Thriller")
        film_a = Film(title="Alien", year=1979, genre="Horror")
        db.session.add_all([film_z, film_a])
        db.session.commit()

        # Add "Zodiac" first so date-added order would differ from alphabetical.
        add_to_watchlist(user_id=sample_user, film_id=film_z.id)
        add_to_watchlist(user_id=sample_user, film_id=film_a.id)

        titles = [f["title"] for f in get_watchlist(sample_user)]
        assert titles == ["Alien", "Zodiac"]


def test_get_watchlist_sort_date_added(app, sample_user):
    """
    get_watchlist(sort="date_added") should return the most recently
    added film first, regardless of title.
    """
    with app.app_context():
        from models import Film, WatchlistEntry

        film_a = Film(title="Alien", year=1979, genre="Horror")
        film_b = Film(title="Blade Runner", year=1982, genre="Sci-Fi")
        db.session.add_all([film_a, film_b])
        db.session.commit()

        earlier = datetime.now(timezone.utc) - timedelta(days=5)
        later = datetime.now(timezone.utc)

        entry_a = WatchlistEntry(user_id=sample_user, film_id=film_a.id, date_added=earlier)
        entry_b = WatchlistEntry(user_id=sample_user, film_id=film_b.id, date_added=later)
        db.session.add_all([entry_a, entry_b])
        db.session.commit()

        titles = [f["title"] for f in get_watchlist(sample_user, sort="date_added")]
        assert titles == ["Blade Runner", "Alien"]
