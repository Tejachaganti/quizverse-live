"""High-entropy, hashed capabilities for anonymous quiz ownership and sharing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import or_, select, update

from quizverse.persistence.db import Database
from quizverse.persistence.models import AnonymousOwner, CapabilityGrant, Quiz

TOKEN_BYTES = 32


class CapabilityScope(StrEnum):
    OWNER = "owner"
    QUIZ_READ = "quiz_read"
    QUIZ_EDIT = "quiz_edit"


@dataclass(frozen=True, slots=True)
class IssuedCapability:
    """Raw capability is returned once and deliberately hidden from repr output."""

    owner_id: str
    grant_id: str
    token: str

    def __repr__(self) -> str:
        return "IssuedCapability(owner_id=<redacted>, grant_id=<redacted>, token=<redacted>)"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", errors="strict")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class CapabilityService:
    """Issue, validate, rotate, and revoke persisted bearer capabilities."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_owner(self) -> IssuedCapability:
        owner_id = secrets.token_hex(16)
        grant_id = secrets.token_hex(16)
        token = _new_token()
        with self._database.sessions.begin() as session:
            owner = AnonymousOwner(id=owner_id)
            grant = CapabilityGrant(
                id=grant_id,
                owner_id=owner_id,
                token_hash=_token_hash(token),
                scope=CapabilityScope.OWNER.value,
            )
            session.add_all([owner, grant])
        return IssuedCapability(owner_id=owner_id, grant_id=grant_id, token=token)

    def issue_share(
        self,
        owner_token: str,
        quiz_id: str,
        *,
        editable: bool = False,
        expires_in_seconds: int | None = None,
    ) -> IssuedCapability:
        if expires_in_seconds is not None and (
            isinstance(expires_in_seconds, bool) or not 1 <= expires_in_seconds <= 31_536_000
        ):
            raise ValueError("Share expiry must be between 1 second and 365 days.")
        scope = CapabilityScope.QUIZ_EDIT if editable else CapabilityScope.QUIZ_READ
        grant_id = secrets.token_hex(16)
        token = _new_token()
        digest = _token_hash(token)
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
            if expires_in_seconds is not None
            else None
        )
        with self._database.sessions.begin() as session:
            owner_grant = self._active_grant(session, owner_token, CapabilityScope.OWNER)
            quiz = session.get(Quiz, quiz_id)
            if quiz is None or quiz.owner_id != owner_grant.owner_id or quiz.deleted_at is not None:
                raise PermissionError("Quiz access is not authorized.")
            if quiz.status != "published":
                raise PermissionError("Only published quizzes can receive share grants.")
            session.add(
                CapabilityGrant(
                    id=grant_id,
                    owner_id=owner_grant.owner_id,
                    quiz_id=quiz_id,
                    token_hash=digest,
                    scope=scope.value,
                    expires_at=expires_at,
                )
            )
            owner_id = owner_grant.owner_id
        return IssuedCapability(owner_id=owner_id, grant_id=grant_id, token=token)

    def validate(self, token: str, *, scope: CapabilityScope, quiz_id: str | None = None) -> str | None:
        """Return the principal owner ID when an active, correctly scoped token matches."""
        if not isinstance(scope, CapabilityScope) or not isinstance(token, str) or not 40 <= len(token) <= 128:
            return None
        try:
            digest = _token_hash(token)
        except UnicodeEncodeError:
            return None
        with self._database.session() as session:
            grant = session.scalar(select(CapabilityGrant).where(CapabilityGrant.token_hash == digest))
            if grant is None or grant.revoked_at is not None:
                return None
            if grant.expires_at is not None and _as_utc(grant.expires_at) <= datetime.now(UTC):
                return None
            if not hmac.compare_digest(grant.token_hash, digest):
                return None
            if grant.scope != scope.value:
                return None
            if scope == CapabilityScope.OWNER and (grant.quiz_id is not None or quiz_id is not None):
                return None
            if scope != CapabilityScope.OWNER and grant.quiz_id != quiz_id:
                return None
            if grant.owner.revoked_at is not None:
                return None
            if grant.quiz_id is not None:
                quiz = grant.quiz
                if (
                    quiz is None
                    or quiz.deleted_at is not None
                    or quiz.owner_id != grant.owner_id
                    or quiz.status != "published"
                ):
                    return None
            return grant.owner_id

    def rotate_owner(self, current_token: str) -> IssuedCapability:
        if not isinstance(current_token, str) or not 40 <= len(current_token) <= 128:
            raise PermissionError("Capability is invalid or revoked.")
        try:
            current_digest = _token_hash(current_token)
        except UnicodeEncodeError as exc:
            raise PermissionError("Capability is invalid or revoked.") from exc

        new_token = _new_token()
        new_grant_id = secrets.token_hex(16)
        digest = _token_hash(new_token)
        with self._database.sessions.begin() as session:
            now = datetime.now(UTC)
            # Claim the old grant with one conditional write. Concurrent callers
            # using the same token serialize at this statement; only one can match
            # revoked_at IS NULL and receive the owner ID.
            owner_id = session.execute(
                update(CapabilityGrant)
                .where(
                    CapabilityGrant.token_hash == current_digest,
                    CapabilityGrant.scope == CapabilityScope.OWNER.value,
                    CapabilityGrant.quiz_id.is_(None),
                    CapabilityGrant.revoked_at.is_(None),
                    or_(CapabilityGrant.expires_at.is_(None), CapabilityGrant.expires_at > now),
                    CapabilityGrant.owner.has(AnonymousOwner.revoked_at.is_(None)),
                )
                .values(revoked_at=now)
                .returning(CapabilityGrant.owner_id)
            ).scalar_one_or_none()
            if owner_id is None:
                raise PermissionError("Capability is invalid or revoked.")
            session.add(
                CapabilityGrant(
                    id=new_grant_id,
                    owner_id=owner_id,
                    token_hash=digest,
                    scope=CapabilityScope.OWNER.value,
                )
            )
        return IssuedCapability(owner_id=owner_id, grant_id=new_grant_id, token=new_token)

    def revoke_owner(self, current_token: str) -> bool:
        """Revoke an owner identity and every grant issued under it."""
        with self._database.sessions.begin() as session:
            current = self._active_grant(session, current_token, CapabilityScope.OWNER)
            owner = current.owner
            owner.revoked_at = datetime.now(UTC)
            for grant in owner.grants:
                if grant.revoked_at is None:
                    grant.revoked_at = owner.revoked_at
            return True

    def revoke(self, owner_token: str, grant_id: str, *, quiz_id: str | None = None) -> bool:
        """Revoke one grant owned by this principal, optionally resource-scoped."""
        with self._database.sessions.begin() as session:
            owner_grant = self._active_grant(session, owner_token, CapabilityScope.OWNER)
            statement = select(CapabilityGrant).where(
                CapabilityGrant.id == grant_id,
                CapabilityGrant.owner_id == owner_grant.owner_id,
            )
            if quiz_id is not None:
                statement = statement.where(CapabilityGrant.quiz_id == quiz_id)
            grant = session.scalar(statement)
            if grant is None or grant.revoked_at is not None:
                return False
            grant.revoked_at = datetime.now(UTC)
            return True

    @staticmethod
    def _active_grant(session, token: str, scope: CapabilityScope) -> CapabilityGrant:
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            raise PermissionError("Capability is invalid or revoked.")
        try:
            digest = _token_hash(token)
        except UnicodeEncodeError as exc:
            raise PermissionError("Capability is invalid or revoked.") from exc
        grant = session.scalar(select(CapabilityGrant).where(CapabilityGrant.token_hash == digest))
        if (
            grant is None
            or grant.revoked_at is not None
            or grant.scope != scope.value
            or (scope == CapabilityScope.OWNER and grant.quiz_id is not None)
            or (grant.expires_at is not None and _as_utc(grant.expires_at) <= datetime.now(UTC))
            or not hmac.compare_digest(grant.token_hash, digest)
            or grant.owner.revoked_at is not None
        ):
            raise PermissionError("Capability is invalid or revoked.")
        return grant


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
