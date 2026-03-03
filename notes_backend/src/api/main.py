import os
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette import status

from src.auth import create_access_token, get_current_user, hash_password, verify_password
from src.db import execute, execute_returning_one, fetch_all, fetch_one, get_db
from src.models import (
    ApiMessage,
    LoginRequest,
    NoteCreateRequest,
    NoteResponse,
    NoteUpdateRequest,
    NotesListResponse,
    SignupRequest,
    TagCreateRequest,
    TagResponse,
    TokenResponse,
    UserMeResponse,
)

openapi_tags = [
    {"name": "System", "description": "Health & basic endpoints"},
    {"name": "Auth", "description": "Email/password authentication with JWT bearer tokens"},
    {"name": "Notes", "description": "CRUD for notes + autosave/sync semantics"},
    {"name": "Tags", "description": "Per-user tag management"},
]

app = FastAPI(
    title="Cross-device Notes API",
    description=(
        "Backend for a cross-device notes application.\n\n"
        "Authentication: obtain a JWT via /auth/login or /auth/signup, then pass it as:\n"
        "`Authorization: Bearer <token>`.\n\n"
        "Autosave/sync semantics: clients should PATCH notes frequently; server updates `updated_at` "
        "via a DB trigger and returns the updated note. Clients can list notes sorted by updated_at "
        "and use `updated_since` for incremental sync."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# Allow configuring CORS in hosted environments.
# - Default is "*" for simple dev/testing (Authorization header still allowed).
# - To lock down, set e.g. CORS_ALLOW_ORIGINS="https://your-frontend.example.com,https://other.example.com"
_allow_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
_allow_origins = ["*"] if _allow_origins_env == "*" else [o.strip() for o in _allow_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalize_tag_name(name: str) -> str:
    return name.strip()


def _load_note_tags(conn, note_id: int) -> List[TagResponse]:
    rows = fetch_all(
        conn,
        """
        SELECT t.id, t.name
        FROM note_tags nt
        JOIN tags t ON t.id = nt.tag_id
        WHERE nt.note_id = %s
        ORDER BY lower(t.name) ASC
        """,
        (note_id,),
    )
    return [TagResponse(**r) for r in rows]


def _note_row_to_response(conn, row) -> NoteResponse:
    return NoteResponse(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        is_archived=row["is_archived"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        tags=_load_note_tags(conn, row["id"]),
    )


def _get_or_create_tags_by_names(conn, user_id: int, tag_names: List[str]) -> List[int]:
    tag_ids: List[int] = []
    for raw in tag_names:
        name = _normalize_tag_name(raw)
        if not name:
            continue
        # Insert-or-select per-user unique (user_id, lower(name)) enforced by index.
        row = fetch_one(
            conn,
            "SELECT id FROM tags WHERE user_id = %s AND lower(name) = lower(%s)",
            (user_id, name),
        )
        if row:
            tag_ids.append(int(row["id"]))
            continue
        created = execute_returning_one(
            conn,
            "INSERT INTO tags (user_id, name) VALUES (%s, %s) RETURNING id",
            (user_id, name),
        )
        tag_ids.append(int(created["id"]))
    # de-duplicate
    return sorted(set(tag_ids))


def _replace_note_tags(conn, note_id: int, tag_ids: List[int]) -> None:
    execute(conn, "DELETE FROM note_tags WHERE note_id = %s", (note_id,))
    for tid in tag_ids:
        execute(
            conn,
            "INSERT INTO note_tags (note_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (note_id, tid),
        )


@app.get(
    "/",
    tags=["System"],
    summary="Health check",
    response_model=ApiMessage,
)
def health_check() -> ApiMessage:
    """Return a basic health check response."""
    return ApiMessage(message="Healthy")


# -------------------------
# Auth
# -------------------------


@app.post(
    "/auth/signup",
    tags=["Auth"],
    summary="Create an account",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def auth_signup(payload: SignupRequest) -> TokenResponse:
    """
    Create a new user and return an access token.

    Validations:
    - email must be unique (case-insensitive)
    - password must be at least 8 characters (enforced by model)
    """
    with get_db() as conn:
        existing = fetch_one(conn, "SELECT id FROM users WHERE email = %s", (payload.email,))
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        user = execute_returning_one(
            conn,
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id, email",
            (payload.email, hash_password(payload.password)),
        )

    token = create_access_token(user_id=int(user["id"]), email=str(user["email"]))
    return TokenResponse(access_token=token)


@app.post(
    "/auth/login",
    tags=["Auth"],
    summary="Login with email/password",
    response_model=TokenResponse,
)
def auth_login(payload: LoginRequest) -> TokenResponse:
    """Verify credentials and return an access token."""
    with get_db() as conn:
        user = fetch_one(conn, "SELECT id, email, password_hash FROM users WHERE email = %s", (payload.email,))
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not verify_password(payload.password, str(user["password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(user_id=int(user["id"]), email=str(user["email"]))
    return TokenResponse(access_token=token)


@app.get(
    "/auth/me",
    tags=["Auth"],
    summary="Get current user profile",
    response_model=UserMeResponse,
)
def auth_me(user=Depends(get_current_user)) -> UserMeResponse:
    """Return the authenticated user's profile."""
    return UserMeResponse(**user)


# -------------------------
# Tags
# -------------------------


@app.get(
    "/tags",
    tags=["Tags"],
    summary="List tags",
    response_model=List[TagResponse],
)
def list_tags(user=Depends(get_current_user)) -> List[TagResponse]:
    """List all tags for the current user, sorted alphabetically."""
    with get_db() as conn:
        rows = fetch_all(
            conn,
            "SELECT id, name FROM tags WHERE user_id = %s ORDER BY lower(name) ASC",
            (int(user["id"]),),
        )
    return [TagResponse(**r) for r in rows]


@app.post(
    "/tags",
    tags=["Tags"],
    summary="Create a tag",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tag(payload: TagCreateRequest, user=Depends(get_current_user)) -> TagResponse:
    """Create a tag for the current user (unique per user, case-insensitive)."""
    name = _normalize_tag_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="Tag name cannot be empty")

    with get_db() as conn:
        existing = fetch_one(
            conn,
            "SELECT id, name FROM tags WHERE user_id = %s AND lower(name) = lower(%s)",
            (int(user["id"]), name),
        )
        if existing:
            return TagResponse(**existing)

        row = execute_returning_one(
            conn,
            "INSERT INTO tags (user_id, name) VALUES (%s, %s) RETURNING id, name",
            (int(user["id"]), name),
        )
    return TagResponse(**row)


@app.delete(
    "/tags/{tag_id}",
    tags=["Tags"],
    summary="Delete a tag",
    response_model=ApiMessage,
)
def delete_tag(tag_id: int, user=Depends(get_current_user)) -> ApiMessage:
    """Delete a tag owned by the current user."""
    with get_db() as conn:
        tag = fetch_one(conn, "SELECT id FROM tags WHERE id = %s AND user_id = %s", (tag_id, int(user["id"])))
        if not tag:
            raise HTTPException(status_code=404, detail="Tag not found")
        execute(conn, "DELETE FROM tags WHERE id = %s", (tag_id,))
    return ApiMessage(message="Deleted")


# -------------------------
# Notes
# -------------------------


@app.get(
    "/notes",
    tags=["Notes"],
    summary="List notes (supports sync + search)",
    response_model=NotesListResponse,
)
def list_notes(
    user=Depends(get_current_user),
    q: Optional[str] = Query(None, description="Free-text search query (title/content full-text)"),
    tag: Optional[str] = Query(None, description="Filter by tag name"),
    archived: Optional[bool] = Query(None, description="Filter by archive status"),
    updated_since: Optional[str] = Query(
        None,
        description="ISO timestamp. If provided, only notes updated after this time are returned (sync).",
    ),
    limit: int = Query(50, ge=1, le=200, description="Max number of notes to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> NotesListResponse:
    """
    List notes for the current user.

    Autosave/sync:
    - sort by updated_at DESC
    - use `updated_since` to fetch incremental changes since last sync
    """
    params = [int(user["id"])]
    where = ["n.user_id = %s"]

    if archived is not None:
        where.append("n.is_archived = %s")
        params.append(bool(archived))

    if updated_since:
        where.append("n.updated_at > %s::timestamptz")
        params.append(updated_since)

    join_tag = ""
    if tag:
        join_tag = "JOIN note_tags nt ON nt.note_id = n.id JOIN tags t ON t.id = nt.tag_id"
        where.append("t.user_id = %s AND lower(t.name) = lower(%s)")
        params.extend([int(user["id"]), tag])

    if q:
        where.append(
            "to_tsvector('english', coalesce(n.title,'') || ' ' || coalesce(n.content,'')) "
            "@@ plainto_tsquery('english', %s)"
        )
        params.append(q)

    where_sql = " AND ".join(where)

    with get_db() as conn:
        total_row = fetch_one(
            conn,
            f"SELECT COUNT(DISTINCT n.id) AS cnt FROM notes n {join_tag} WHERE {where_sql}",
            tuple(params),
        )
        total = int(total_row["cnt"]) if total_row else 0

        rows = fetch_all(
            conn,
            f"""
            SELECT DISTINCT n.id, n.title, n.content, n.is_archived, n.created_at, n.updated_at
            FROM notes n
            {join_tag}
            WHERE {where_sql}
            ORDER BY n.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        )
        items = [_note_row_to_response(conn, r) for r in rows]

    return NotesListResponse(items=items, total=total)


@app.post(
    "/notes",
    tags=["Notes"],
    summary="Create a note",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_note(payload: NoteCreateRequest, user=Depends(get_current_user)) -> NoteResponse:
    """Create a note for the current user, optionally assigning tags by name."""
    tag_names = payload.tag_names or []
    for tn in tag_names:
        if len(_normalize_tag_name(tn)) > 64:
            raise HTTPException(status_code=400, detail="Tag names must be <= 64 characters")

    with get_db() as conn:
        row = execute_returning_one(
            conn,
            """
            INSERT INTO notes (user_id, title, content)
            VALUES (%s, %s, %s)
            RETURNING id, title, content, is_archived, created_at, updated_at
            """,
            (int(user["id"]), payload.title or "", payload.content or ""),
        )

        tag_ids = _get_or_create_tags_by_names(conn, int(user["id"]), tag_names)
        if tag_ids:
            _replace_note_tags(conn, int(row["id"]), tag_ids)

        return _note_row_to_response(conn, row)


@app.get(
    "/notes/{note_id}",
    tags=["Notes"],
    summary="Get a note",
    response_model=NoteResponse,
)
def get_note(note_id: int, user=Depends(get_current_user)) -> NoteResponse:
    """Get a single note by id (must belong to current user)."""
    with get_db() as conn:
        row = fetch_one(
            conn,
            """
            SELECT id, title, content, is_archived, created_at, updated_at
            FROM notes
            WHERE id = %s AND user_id = %s
            """,
            (note_id, int(user["id"])),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Note not found")
        return _note_row_to_response(conn, row)


@app.patch(
    "/notes/{note_id}",
    tags=["Notes"],
    summary="Update a note (autosave)",
    response_model=NoteResponse,
)
def update_note(note_id: int, payload: NoteUpdateRequest, user=Depends(get_current_user)) -> NoteResponse:
    """
    Patch a note. Intended for autosave: clients can call this frequently.

    Notes:
    - `updated_at` is refreshed by a DB trigger on any update.
    - If `tag_names` is provided, it replaces the current set of tags.
    """
    if payload.tag_names is not None:
        for tn in payload.tag_names:
            if len(_normalize_tag_name(tn)) > 64:
                raise HTTPException(status_code=400, detail="Tag names must be <= 64 characters")

    with get_db() as conn:
        existing = fetch_one(conn, "SELECT id FROM notes WHERE id = %s AND user_id = %s", (note_id, int(user["id"])))
        if not existing:
            raise HTTPException(status_code=404, detail="Note not found")

        fields = []
        params = []
        if payload.title is not None:
            fields.append("title = %s")
            params.append(payload.title)
        if payload.content is not None:
            fields.append("content = %s")
            params.append(payload.content)
        if payload.is_archived is not None:
            fields.append("is_archived = %s")
            params.append(bool(payload.is_archived))

        if fields:
            params.extend([note_id, int(user["id"])])
            execute(
                conn,
                f"UPDATE notes SET {', '.join(fields)} WHERE id = %s AND user_id = %s",
                tuple(params),
            )

        if payload.tag_names is not None:
            tag_ids = _get_or_create_tags_by_names(conn, int(user["id"]), payload.tag_names)
            _replace_note_tags(conn, note_id, tag_ids)

        row = fetch_one(
            conn,
            """
            SELECT id, title, content, is_archived, created_at, updated_at
            FROM notes
            WHERE id = %s AND user_id = %s
            """,
            (note_id, int(user["id"])),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Note not found")
        return _note_row_to_response(conn, row)


@app.delete(
    "/notes/{note_id}",
    tags=["Notes"],
    summary="Delete a note",
    response_model=ApiMessage,
)
def delete_note(note_id: int, user=Depends(get_current_user)) -> ApiMessage:
    """Delete a note (and its note_tags) owned by the current user."""
    with get_db() as conn:
        existing = fetch_one(conn, "SELECT id FROM notes WHERE id = %s AND user_id = %s", (note_id, int(user["id"])))
        if not existing:
            raise HTTPException(status_code=404, detail="Note not found")
        execute(conn, "DELETE FROM notes WHERE id = %s AND user_id = %s", (note_id, int(user["id"])))
    return ApiMessage(message="Deleted")
