# Milestone 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vegapunk runs as one Docker container in which a person can create the admin account, sign in, see the app shell, and edit their profile.

**Architecture:** The FastAPI backend owns a SQLite database in a data directory and serves the built React app as static files, so one container serves everything. The single admin signs in with a password; the server keeps sessions in the database and identifies the browser with an HttpOnly cookie. The frontend asks the API for its state on load and routes to Setup, Sign in, or the app.

**Tech Stack:** Python 3.12, FastAPI, stdlib `sqlite3`, `argon2-cffi`; TypeScript, React 19, Vite, `react-router`, Vitest with Testing Library; Docker.

**Spec:** [`AGENTS.md`](../../AGENTS.md) ("First-version scope" and "Architecture"). Screen designs: the "Vegapunk MVP" canvas (`SignIn`, `Setup`, `Profile`, `Main` for the shell) and the "Vegapunk" design system (`project/README.md`, `project/tokens.css`).

## Global Constraints

- One admin account only; no other users or roles.
- Vegapunk changes nothing on the host. This milestone does not touch Docker at all.
- New dependencies are limited to: `argon2-cffi` (backend); `react-router`, `lucide-react@0.460.0` (exact pin), `@fontsource/ibm-plex-sans`, `@fontsource/ibm-plex-mono`, and dev-only `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom` (frontend). Ask before adding anything else.
- Fonts are bundled, never loaded from a third-party host at runtime.
- All timestamps are stored as ISO-8601 UTC text.
- All API routes live under `/api`. Every route except `/api/health`, `/api/setup/*` and `/api/auth/sign-in` requires a signed-in admin and returns 401 otherwise.
- UI copy follows the design system: plain and exact; "Agent" not "AI"; dark theme (`vp-root`) only in this milestone.
- Colors, spacing, radius, type and motion come only from the tokens in `tokens.css`.
- Backend: `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` pass. Frontend: `npm run lint`, `npm test` and `npm run build` pass.
- Commit after each task with a message describing the change. Do not push unless asked.

## Review Focus

Each line names a condition no happy-path test covers; the task in brackets owns its test.

1. **A second setup attempt after the admin exists** (double-click, two tabs, replayed request) must return 409 and never create or overwrite an account. [Task 2]
2. **An expired, deleted or garbage session cookie** must give 401 from the API and land the person on Sign in, not on a blank or broken page. [Tasks 3, 6]
3. **A data directory that is missing or not writable** must stop startup with one clear message naming the path, not a stack trace on first request. [Task 1]
4. **A database written by a newer Vegapunk than the one running** (after a downgrade) must stop startup with a clear message instead of running against an unknown schema. [Task 1]
5. **The API being unreachable or returning 500 while the app loads** must show "Couldn't load Vegapunk" with a Retry button, not an endless spinner. [Task 6]

---

## Implementation notes

Where the build differs from this plan:

- **Transactions (Task 1).** `get_db` does not commit. Connections are autocommit and usable from any thread, and every write runs inside `db.transaction(conn)`, which commits before the response is built. Review found that committing in the dependency teardown ran after the response was sent and failed under concurrent requests. Sign-in does its lockout check, password check and record in one transaction so parallel guesses are counted exactly. `backend/tests/test_concurrency.py` sends overlapping requests to a real server.
- **Component styles (Task 5).** One `frontend/src/styles/components.css`, extracted from the design system's stylesheet, replaces the per-component CSS files.
- **Auth state (Task 6).** `refresh()` returns the new state, so Sign in can explain a session that the browser did not keep. `useAuth` and its types live in `auth/auth-context.ts` and the provider in `auth/AuthProvider.tsx`.
- **Forms (Tasks 6 and 7).** Forms check username and password rules before sending, counting characters as the server does, so people see specific messages instead of a generic 422.

---

## File Structure

```
backend/vegapunk/
  config.py            settings read from environment variables
  db.py                connection, migrations, request dependency
  migrations/0001_init.sql
  app.py               create_app(settings): routers, startup, static files
  main.py              app = create_app(load_settings())   (existing; shrinks)
  __main__.py          CLI: reset-password
  auth/
    passwords.py       hash and verify
    admin.py           the single admin record
    sessions.py        sessions and sign-in lockout
    routes.py          /api/setup, /api/auth, /api/me, require_admin
backend/tests/
  conftest.py          settings, app and client fixtures on a temp data dir
  test_config.py  test_db.py  test_setup.py  test_sessions.py
  test_auth_routes.py  test_profile.py  test_cli.py  test_static.py
frontend/src/
  styles/tokens.css  styles/base.css
  api.ts               fetch wrapper and ApiError
  auth/AuthContext.tsx  session state for the whole app
  components/          Button, Input, AppShell, SideNav, AccountMenu,
                       PageHeader, EmptyState, Alert (one .tsx + .css each)
  pages/               Setup, SignIn, Overview, Profile, LoadError
  App.tsx              routes and guards
Dockerfile  compose.yaml  .dockerignore
```

---

### Task 1: Settings and database

**Files:**
- Create: `backend/vegapunk/config.py`, `backend/vegapunk/db.py`, `backend/vegapunk/migrations/0001_init.sql`, `backend/vegapunk/app.py`
- Modify: `backend/vegapunk/main.py`, `backend/tests/test_health.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_config.py`, `backend/tests/test_db.py`

**Interfaces:**
- Produces:
  - `config.Settings` (frozen dataclass): `data_dir: Path`, `static_dir: Path | None`, `secure_cookies: bool`; property `db_path -> Path` = `data_dir / "vegapunk.db"`.
  - `config.load_settings(env: Mapping[str, str] = os.environ) -> Settings`. `VEGAPUNK_DATA_DIR` (default `data`), `VEGAPUNK_STATIC_DIR` (unset or blank → `None`), `VEGAPUNK_SECURE_COOKIES` (`"1"` → `True`, default `False`).
  - `db.connect(path: Path) -> sqlite3.Connection` with `row_factory = sqlite3.Row`, `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`.
  - `db.migrate(conn: sqlite3.Connection) -> int`: applies `migrations/NNNN_*.sql` files whose number is above `PRAGMA user_version`, in order, each in a transaction, then sets `user_version`; returns the resulting version. Raises `db.SchemaTooNew` if `user_version` is above the highest file.
  - `db.prepare_data_dir(settings: Settings) -> None`: creates the directory if missing; raises `db.DataDirError` (message includes the path) if it cannot be created or written.
  - `db.get_db(request: Request) -> Iterator[sqlite3.Connection]`: FastAPI dependency; opens a connection to `request.app.state.settings.db_path`, yields it, commits on success, rolls back on exception, closes.
  - `app.create_app(settings: Settings) -> FastAPI`: stores `settings` on `app.state`; its lifespan calls `prepare_data_dir` then `migrate`; registers `/api/health`.
  - `main.app = create_app(load_settings())`.
  - Test fixtures in `conftest.py`: `settings(tmp_path) -> Settings`, `client(settings) -> TestClient` (entered as a context manager so the lifespan runs), `conn(settings) -> sqlite3.Connection` (migrated).

`0001_init.sql`:

```sql
CREATE TABLE admin (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  username TEXT NOT NULL,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE sign_in_failures (
  failed_at TEXT NOT NULL
);
```

- [ ] **Step 1: Write the failing tests**

```python
# test_config.py
def test_defaults():
    s = load_settings({})
    assert s.data_dir == Path("data") and s.static_dir is None and s.secure_cookies is False
    assert s.db_path == Path("data/vegapunk.db")

def test_reads_environment():
    s = load_settings({"VEGAPUNK_DATA_DIR": "/d", "VEGAPUNK_STATIC_DIR": "/s", "VEGAPUNK_SECURE_COOKIES": "1"})
    assert (s.data_dir, s.static_dir, s.secure_cookies) == (Path("/d"), Path("/s"), True)

def test_blank_static_dir_is_none():
    assert load_settings({"VEGAPUNK_STATIC_DIR": " "}).static_dir is None

# test_db.py
def test_migrate_creates_tables_and_is_repeatable(tmp_path):
    conn = connect(tmp_path / "v.db")
    assert migrate(conn) == 1
    assert migrate(conn) == 1
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"admin", "sessions", "sign_in_failures"} <= names

def test_refuses_database_from_newer_version(tmp_path):
    conn = connect(tmp_path / "v.db")
    conn.execute("PRAGMA user_version = 999")
    with pytest.raises(SchemaTooNew):
        migrate(conn)

def test_unwritable_data_dir_names_the_path(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(DataDirError, match=str(blocker)):
        prepare_data_dir(Settings(data_dir=blocker / "sub", static_dir=None, secure_cookies=False))

def test_foreign_keys_are_on(tmp_path):
    assert connect(tmp_path / "v.db").execute("PRAGMA foreign_keys").fetchone()[0] == 1
```

- [ ] **Step 2: Run them and confirm they fail** — `cd backend && uv run pytest tests/test_config.py tests/test_db.py -q` → import errors.
- [ ] **Step 3: Implement `config.py`, `db.py`, the migration file and `app.py`; reduce `main.py` to the one `app = ...` line.** Read migration files with `importlib.resources` so they ship inside the package.
- [ ] **Step 4: Move the health test onto the `client` fixture** and confirm the startup failure modes surface: `SchemaTooNew` and `DataDirError` propagate out of the lifespan (uvicorn then exits with the message).
- [ ] **Step 5: Run everything** — `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` → all pass.
- [ ] **Step 6: Commit.**

---

### Task 2: Admin account and first-run setup

**Files:**
- Create: `backend/vegapunk/auth/__init__.py`, `auth/passwords.py`, `auth/admin.py`, `auth/routes.py`
- Modify: `backend/pyproject.toml` (add `argon2-cffi`), `backend/vegapunk/app.py` (include the router)
- Test: `backend/tests/test_setup.py`

**Interfaces:**
- Consumes: `db.get_db`, the `client` and `conn` fixtures.
- Produces:
  - `passwords.hash_password(password: str) -> str`, `passwords.verify_password(password: str, password_hash: str) -> bool` (Argon2id with library defaults; `verify` returns `False` on mismatch or a malformed hash, never raises).
  - `admin.Admin` (frozen dataclass): `username: str`, `display_name: str`, `password_hash: str`, `created_at: datetime`.
  - `admin.get_admin(conn) -> Admin | None`
  - `admin.create_admin(conn, *, username: str, display_name: str, password: str, now: datetime) -> Admin`; raises `admin.AdminExists` if a row exists.
  - `admin.USERNAME_PATTERN = r"^[a-z0-9_-]{1,32}$"`, `admin.MIN_PASSWORD_LENGTH = 10`.
  - `routes.router` (prefix `/api`), with:
    - `GET /api/setup/status` → 200 `{"admin_created": bool}`
    - `POST /api/setup/admin` body `{"username": str, "password": str, "display_name": str | null}` → 201 `{"username", "display_name", "created_at"}`. `display_name` defaults to the username. 409 `{"detail": "The admin account already exists."}` if one exists. 422 if the username does not match the pattern or the password is shorter than 10 characters.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_before_and_after_setup(client):
    assert client.get("/api/setup/status").json() == {"admin_created": False}
    r = client.post("/api/setup/admin", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 201
    assert r.json()["username"] == "admin" and r.json()["display_name"] == "admin"
    assert client.get("/api/setup/status").json() == {"admin_created": True}

def test_second_setup_is_rejected_and_changes_nothing(client, conn):
    client.post("/api/setup/admin", json={"username": "admin", "password": "correct horse"})
    r = client.post("/api/setup/admin", json={"username": "other", "password": "another pass"})
    assert r.status_code == 409
    assert [row["username"] for row in conn.execute("SELECT username FROM admin")] == ["admin"]

@pytest.mark.parametrize("body", [
    {"username": "Admin", "password": "correct horse"},      # uppercase
    {"username": "", "password": "correct horse"},
    {"username": "a" * 33, "password": "correct horse"},
    {"username": "admin", "password": "short"},
])
def test_invalid_input_is_rejected(client, body):
    assert client.post("/api/setup/admin", json=body).status_code == 422
    assert client.get("/api/setup/status").json() == {"admin_created": False}

def test_password_is_stored_hashed(client, conn):
    client.post("/api/setup/admin", json={"username": "admin", "password": "correct horse"})
    stored = conn.execute("SELECT password_hash FROM admin").fetchone()[0]
    assert "correct horse" not in stored and verify_password("correct horse", stored)

def test_verify_rejects_wrong_and_malformed():
    assert not verify_password("x", hash_password("y"))
    assert not verify_password("x", "not-a-hash")
```

- [ ] **Step 2: Run and confirm failure** — `uv run pytest tests/test_setup.py -q`.
- [ ] **Step 3: Add the dependency** — `uv add argon2-cffi`.
- [ ] **Step 4: Implement `passwords.py`, `admin.py` and the two routes.** `create_admin` relies on the `CHECK (id = 1)` primary key: insert with `id = 1` and translate `sqlite3.IntegrityError` into `AdminExists`, so two racing requests cannot both succeed.
- [ ] **Step 5: Run the full backend checks** → all pass.
- [ ] **Step 6: Commit.**

---

### Task 3: Sessions, sign in, sign out, lockout

**Files:**
- Create: `backend/vegapunk/auth/sessions.py`
- Modify: `backend/vegapunk/auth/routes.py`
- Test: `backend/tests/test_sessions.py`, `backend/tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `admin.get_admin`, `passwords.verify_password`, `db.get_db`, `Settings.secure_cookies`.
- Produces:
  - `sessions.SESSION_COOKIE = "vegapunk_session"`, `SESSION_LIFETIME = timedelta(days=30)`, `MAX_FAILURES = 5`, `LOCKOUT = timedelta(minutes=15)`.
  - `sessions.create_session(conn, now: datetime) -> str`: returns a new random token (`secrets.token_urlsafe(32)`); stores only its SHA-256 hex digest.
  - `sessions.session_is_valid(conn, token: str, now: datetime) -> bool`
  - `sessions.delete_session(conn, token: str) -> None`, `sessions.delete_all_sessions(conn, *, except_token: str | None = None) -> None`
  - `sessions.record_failure(conn, now) -> None`, `sessions.clear_failures(conn) -> None`
  - `sessions.locked_until(conn, now) -> datetime | None`: if at least 5 failures fall within the 15 minutes before `now`, returns the fifth-most-recent failure time plus 15 minutes; otherwise `None`.
  - `routes.require_admin(...) -> Admin`: FastAPI dependency; 401 `{"detail": "Not signed in."}` when the cookie is missing, unknown or expired.
  - `POST /api/auth/sign-in` body `{"username", "password"}` → 204 and sets the cookie (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` 30 days, `Secure` when `secure_cookies`). 401 `{"detail": "That username or password doesn't match."}`. 429 with a `Retry-After` header (seconds) and `{"detail": "Sign-in is paused after 5 failed tries.", "locked_until": "<ISO time>"}` while locked; a locked request is rejected before the password is checked and does not count as a new failure.
  - `POST /api/auth/sign-out` → 204, deletes the session, clears the cookie. Succeeds even without a valid cookie.
  - `GET /api/me` → 200 `{"username", "display_name", "created_at"}`.
  - `POST /api/setup/admin` (from Task 2) now also creates a session and sets the cookie, so setup ends signed in.

- [ ] **Step 1: Write the failing tests**

```python
# test_sessions.py   (T0 = datetime(2026, 1, 1, tzinfo=UTC))
def test_session_round_trip_and_expiry(conn):
    token = create_session(conn, T0)
    assert session_is_valid(conn, token, T0 + timedelta(days=29))
    assert not session_is_valid(conn, token, T0 + timedelta(days=31))
    assert not session_is_valid(conn, "garbage", T0)

def test_token_is_not_stored_in_plain_text(conn):
    token = create_session(conn, T0)
    assert token not in [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]

def test_lockout_after_five_failures_then_release(conn):
    for i in range(4):
        record_failure(conn, T0 + timedelta(minutes=i))
    assert locked_until(conn, T0 + timedelta(minutes=4)) is None
    record_failure(conn, T0 + timedelta(minutes=4))
    assert locked_until(conn, T0 + timedelta(minutes=5)) == T0 + timedelta(minutes=15)
    assert locked_until(conn, T0 + timedelta(minutes=16)) is None

def test_delete_all_can_keep_one(conn):
    keep, drop = create_session(conn, T0), create_session(conn, T0)
    delete_all_sessions(conn, except_token=keep)
    assert session_is_valid(conn, keep, T0) and not session_is_valid(conn, drop, T0)

# test_auth_routes.py   (fixture `admin_client`: client after setup, cookie jar cleared)
def test_me_requires_sign_in(admin_client):
    assert admin_client.get("/api/me").status_code == 401

def test_sign_in_then_me_then_sign_out(admin_client):
    r = admin_client.post("/api/auth/sign-in", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert admin_client.get("/api/me").json()["username"] == "admin"
    assert admin_client.post("/api/auth/sign-out").status_code == 204
    assert admin_client.get("/api/me").status_code == 401

def test_wrong_password_and_unknown_user_look_the_same(admin_client):
    a = admin_client.post("/api/auth/sign-in", json={"username": "admin", "password": "wrong"})
    b = admin_client.post("/api/auth/sign-in", json={"username": "nobody", "password": "wrong"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()

def test_sixth_try_is_paused_even_with_the_right_password(admin_client):
    for _ in range(5):
        admin_client.post("/api/auth/sign-in", json={"username": "admin", "password": "wrong"})
    r = admin_client.post("/api/auth/sign-in", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "locked_until" in r.json()

def test_success_clears_earlier_failures(admin_client, conn):
    admin_client.post("/api/auth/sign-in", json={"username": "admin", "password": "wrong"})
    admin_client.post("/api/auth/sign-in", json={"username": "admin", "password": "correct horse"})
    assert conn.execute("SELECT count(*) FROM sign_in_failures").fetchone()[0] == 0

def test_garbage_cookie_is_401(admin_client):
    admin_client.cookies.set("vegapunk_session", "garbage")
    assert admin_client.get("/api/me").status_code == 401

def test_setup_signs_you_in(client):
    client.post("/api/setup/admin", json={"username": "admin", "password": "correct horse"})
    assert client.get("/api/me").status_code == 200

def test_secure_flag_follows_setting(tmp_path):
    # build an app with secure_cookies=True, run setup, assert "secure" in the set-cookie header
```

- [ ] **Step 2: Run and confirm failure.**
- [ ] **Step 3: Implement `sessions.py`.** Compare token hashes with `hmac.compare_digest`. Delete expired sessions and failures older than the lockout window opportunistically inside `create_session` and `record_failure`.
- [ ] **Step 4: Implement `require_admin` and the routes; update the setup route to sign in.** For an unknown username still run one `verify_password` against a fixed dummy hash, so both failures take similar time.
- [ ] **Step 5: Run the full backend checks** → all pass.
- [ ] **Step 6: Commit.**

---

### Task 4: Profile, password change, and password reset from the host

**Files:**
- Create: `backend/vegapunk/__main__.py`
- Modify: `backend/vegapunk/auth/admin.py`, `backend/vegapunk/auth/routes.py`
- Test: `backend/tests/test_profile.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `require_admin`, `sessions.delete_all_sessions`, `sessions.clear_failures`, `passwords.*`.
- Produces:
  - `admin.update_admin(conn, *, username: str | None = None, display_name: str | None = None) -> Admin`
  - `admin.set_password(conn, password: str) -> None`
  - `PATCH /api/me` body with optional `username`, `display_name` → 200, same shape as `GET /api/me`. 422 for a username that fails the pattern or a display name that is blank or longer than 64 characters.
  - `POST /api/me/password` body `{"current_password", "new_password"}` → 204; signs out every other session and keeps this one. 400 `{"detail": "Your current password doesn't match."}`. 422 if the new password is shorter than 10 characters.
  - `__main__.main(argv: list[str] | None = None, *, read_password: Callable[[str], str] = getpass.getpass) -> int`: command `reset-password` asks for a new password twice, sets it, deletes all sessions and clears sign-in failures. Returns 0 on success; 1 with a message on stderr if no admin exists, the two entries differ, or the password is too short. Run as `python -m vegapunk reset-password`.

There is no email field and no email reset: the first version sends no mail. A locked-out admin resets the password from the host with the command above.

- [ ] **Step 1: Write the failing tests**

```python
# test_profile.py   (fixture `signed_in`: client after setup, still signed in)
def test_update_display_name_and_username(signed_in):
    r = signed_in.patch("/api/me", json={"display_name": "Akira", "username": "akira"})
    assert r.status_code == 200 and r.json()["display_name"] == "Akira"
    assert signed_in.get("/api/me").json()["username"] == "akira"

@pytest.mark.parametrize("body", [{"username": "Bad Name"}, {"display_name": "  "}, {"display_name": "x" * 65}])
def test_update_rejects_invalid(signed_in, body):
    assert signed_in.patch("/api/me", json=body).status_code == 422

def test_change_password(signed_in):
    r = signed_in.post("/api/me/password", json={"current_password": "correct horse", "new_password": "battery staple"})
    assert r.status_code == 204
    assert signed_in.get("/api/me").status_code == 200          # this session survives
    signed_in.post("/api/auth/sign-out")
    assert signed_in.post("/api/auth/sign-in", json={"username": "admin", "password": "battery staple"}).status_code == 204

def test_change_password_needs_the_current_one(signed_in):
    r = signed_in.post("/api/me/password", json={"current_password": "wrong", "new_password": "battery staple"})
    assert r.status_code == 400

def test_change_password_signs_out_other_sessions(signed_in, settings):
    # sign in from a second client, change the password from the first, assert the second gets 401

def test_profile_routes_require_sign_in(client):
    assert client.patch("/api/me", json={}).status_code == 401
    assert client.post("/api/me/password", json={"current_password": "a", "new_password": "b" * 10}).status_code == 401

# test_cli.py   (monkeypatch VEGAPUNK_DATA_DIR to the fixture's data dir)
def test_reset_password_sets_it_and_signs_everyone_out(signed_in, monkeypatch):
    assert main(["reset-password"], read_password=lambda _prompt: "brand new pass") == 0
    assert signed_in.get("/api/me").status_code == 401
    assert signed_in.post("/api/auth/sign-in", json={"username": "admin", "password": "brand new pass"}).status_code == 204

def test_reset_password_clears_lockout(admin_client): ...       # five failures, reset, sign-in returns 204
def test_reset_password_without_admin_fails(settings, capsys): ...   # returns 1, stderr mentions setup
def test_reset_password_mismatch_fails(signed_in): ...           # two different entries → 1, password unchanged
```

- [ ] **Step 2: Run and confirm failure.**
- [ ] **Step 3: Implement the two admin functions, the two routes and the CLI** (`argparse` with one subcommand).
- [ ] **Step 4: Run the full backend checks** → all pass.
- [ ] **Step 5: Commit.**

---

### Task 5: Frontend foundation — tokens, test setup, API client, form components

**Files:**
- Create: `frontend/src/styles/tokens.css`, `frontend/src/styles/base.css`, `frontend/src/api.ts`, `frontend/src/components/Button.tsx` + `.css`, `Input.tsx` + `.css`, `Alert.tsx` + `.css`, `frontend/src/test-setup.ts`
- Modify: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/src/main.tsx`, `frontend/tsconfig.app.json` (Vitest globals types)
- Test: `frontend/src/api.test.ts`, `frontend/src/components/Button.test.tsx`, `Input.test.tsx`

**Interfaces:**
- Produces:
  - `tokens.css`: copied unchanged from the design system artifact (`project/tokens.css`).
  - `base.css`: imports the bundled IBM Plex Sans (400, 500, 600) and IBM Plex Mono (400, 500) from `@fontsource`; sets `body { margin: 0 }`, page background `--surface-100`, text `--ink`, font `--font-sans`; a `.vp-mono` class; a visible focus ring; a `prefers-reduced-motion` rule that removes transforms.
  - `main.tsx` renders `<App />` inside `<div className="vp-root">`.
  - `api.ts`:
    - `class ApiError extends Error { status: number; detail: string; body: unknown }`
    - `api<T>(path: string, options?: { method?: string; body?: unknown }): Promise<T>`: prefixes `/api`, sends JSON with `credentials: "same-origin"`, returns parsed JSON or `undefined` for 204. On a non-2xx response throws `ApiError` whose `detail` is the response's `detail` string when present, else `"Request failed (<status>)"`. On a network failure throws `ApiError` with `status` 0 and `detail` `"Couldn't reach the server."`.
  - `Button`: props `variant?: "primary" | "secondary" | "ghost" | "danger"` (default `secondary`), `size?: "md" | "sm"`, `loading?: boolean`, plus native button props. While `loading` it is disabled and has `aria-busy="true"`. 36px high (28px for `sm`), at least 44px on touch screens (`@media (pointer: coarse)`).
  - `Input`: props `label: string`, `hint?: string`, `error?: string`, `mono?: boolean`, plus native input props. The label is tied to the field; `hint` and `error` are linked with `aria-describedby`; with `error` the field has `aria-invalid="true"`. Text is 16px.
  - `Alert`: props `tone: "info" | "success" | "warning" | "danger"`, `title: string`, `children?`, `action?: ReactNode`. `role="alert"` for `danger`, otherwise `role="status"`.
  - `npm test` runs `vitest run`.

- [ ] **Step 1: Install dependencies and wire up Vitest** — `npm install react-router lucide-react@0.460.0 @fontsource/ibm-plex-sans @fontsource/ibm-plex-mono` and `npm install -D vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom`. In `vite.config.ts` add `test: { environment: "jsdom", globals: true, setupFiles: "./src/test-setup.ts" }` (import `defineConfig` from `vitest/config`). Add `"test": "vitest run"` to `package.json`. Add a `frontend` CI step `npm test` in `.github/workflows/ci.yml` between lint and build.
- [ ] **Step 2: Write the failing tests**

```ts
// api.test.ts   (stub global fetch with vi.fn)
it("returns parsed JSON and sends credentials", ...)      // fetch called with "/api/me", credentials "same-origin"
it("returns undefined for 204", ...)
it("throws ApiError with the server's detail", ...)       // 401 {"detail":"Not signed in."} → status 401, detail "Not signed in."
it("falls back to a generic detail", ...)                 // 500 with HTML body → "Request failed (500)"
it("reports a network failure as status 0", ...)          // fetch rejects → detail "Couldn't reach the server."

// Button.test.tsx
it("is disabled and busy while loading", ...)             // toBeDisabled, aria-busy "true", onClick not called
// Input.test.tsx
it("ties label, hint and error to the field", ...)        // getByLabelText("Username"); aria-invalid; toHaveAccessibleDescription containing the error
```

- [ ] **Step 3: Run and confirm failure** — `npm test`.
- [ ] **Step 4: Implement the styles, `api.ts` and the three components**, matching the design system's look (read the `Button`, `Input` and `Alert` READMEs and previews in the artifact).
- [ ] **Step 5: Run** `npm run lint && npm test && npm run build` → all pass.
- [ ] **Step 6: Commit.**

---

### Task 6: Setup and Sign in, with routing guards

**Files:**
- Create: `frontend/src/auth/AuthContext.tsx`, `frontend/src/pages/Setup.tsx`, `SignIn.tsx`, `LoadError.tsx`, `Overview.tsx` (a heading only; Task 7 fills it)
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.test.tsx`, `frontend/src/pages/Setup.test.tsx`, `SignIn.test.tsx`

**Interfaces:**
- Consumes: `api`, `ApiError`, `Button`, `Input`, `Alert`; the endpoints from Tasks 2 and 3.
- Produces:
  - `type Admin = { username: string; display_name: string; created_at: string }`
  - `type AuthState = { status: "loading" } | { status: "error" } | { status: "needs-setup" } | { status: "signed-out" } | { status: "signed-in"; admin: Admin }`
  - `AuthProvider` and `useAuth(): { state: AuthState; refresh(): Promise<void>; signOut(): Promise<void> }`. `refresh` calls `GET /setup/status`; if no admin → `needs-setup`; otherwise `GET /me` → `signed-in`, or `signed-out` on 401; any other failure → `error`.
  - Routes: `/setup`, `/sign-in`, `/` (Overview), `/profile` (Task 7). Guards: `needs-setup` sends every path to `/setup`; `signed-out` sends every path to `/sign-in`; `signed-in` sends `/setup` and `/sign-in` to `/`; unknown paths go to `/`.
  - Any `api` call that fails with 401 while `signed-in` triggers `refresh()`, which lands the person on Sign in.

Copy (exact):

| Where | Text |
|---|---|
| Both pages, above the heading | `Vegapunk` |
| Setup heading | `Set up your control room` |
| Setup intro | `Create the admin account for this server. You can change these details later in Profile.` |
| Setup fields | `Username` (hint `Lowercase letters, numbers, dashes and underscores.`), `Password` (hint `At least 10 characters.`), `Confirm password` |
| Setup button | `Create account` |
| Setup mismatch error | `The two passwords don't match.` |
| Sign in heading and button | `Sign in` |
| Sign in fields | `Username`, `Password` |
| Sign in 401 | `That username or password doesn't match. After 5 tries sign-in pauses for 15 minutes.` |
| Sign in 429 | `Sign-in is paused until {time}.` with `{time}` as a 12-hour clock time with zone, e.g. `3:12 PM UTC` |
| Sign in footnote | `Forgot your password? Run` `docker exec -it vegapunk python -m vegapunk reset-password` `on the host.` (command in mono) |
| Load error | Alert, danger: title `Couldn't load Vegapunk`, body `The server didn't respond.`, button `Retry` |

- [ ] **Step 1: Write the failing tests** (mock `fetch` per test; render inside `MemoryRouter`)

```ts
// App.test.tsx
it("shows Setup when no admin exists", ...)                         // status {admin_created:false} → heading "Set up your control room"
it("shows Sign in when signed out", ...)                            // admin_created true, /me 401 → heading "Sign in"
it("shows the app when signed in, and redirects /sign-in to /", ...)
it("shows a load error with Retry, and recovers", ...)              // first status call rejects → "Couldn't load Vegapunk"; click Retry → Sign in
it("returns to Sign in when a call gets 401 mid-session", ...)

// Setup.test.tsx
it("blocks submit when passwords differ", ...)                      // error text shown, no POST made
it("creates the account and enters the app", ...)                   // POST /api/setup/admin with {username, password}; then Overview
it("shows the server's message on 409", ...)                        // "The admin account already exists."

// SignIn.test.tsx
it("signs in", ...)
it("shows the mismatch message on 401", ...)
it("shows when sign-in resumes on 429", ...)                        // locked_until "2026-01-01T15:12:00Z" → "Sign-in is paused until 3:12 PM UTC."
it("disables the button while the request is in flight", ...)
```

- [ ] **Step 2: Run and confirm failure.**
- [ ] **Step 3: Implement `AuthContext`, the pages and the routes.** Layout follows `SignIn.dc.html` and `Setup.dc.html`: centred column, 400px wide for Sign in and 640px for Setup, form inside a card. Setup shows only the account form in this milestone (the provider step arrives in milestone 3).
- [ ] **Step 4: Run** `npm run lint && npm test && npm run build` → all pass.
- [ ] **Step 5: Try it for real** — start the backend and `npm run dev`; create the account, sign out by clearing the cookie, sign in, enter a wrong password six times and confirm the pause message.
- [ ] **Step 6: Commit.**

---

### Task 7: App shell, Overview placeholder, Profile

**Files:**
- Create: `frontend/src/components/AppShell.tsx` + `.css`, `SideNav.tsx` + `.css`, `AccountMenu.tsx` + `.css`, `PageHeader.tsx` + `.css`, `EmptyState.tsx` + `.css`, `frontend/src/pages/Profile.tsx`
- Modify: `frontend/src/pages/Overview.tsx`, `frontend/src/App.tsx`
- Test: `frontend/src/components/AppShell.test.tsx`, `frontend/src/pages/Profile.test.tsx`

**Interfaces:**
- Consumes: `useAuth`, `api`, `Button`, `Input`, `Alert`; `PATCH /api/me`, `POST /api/me/password`.
- Produces:
  - `AppShell`: props `children`. Sidebar on wide screens; below 768px a top bar with a menu button that opens the navigation as a drawer. Includes a "Skip to content" link and a `<main id="main">`.
  - `SideNav`: the wordmark `Vegapunk`, one item `Overview` (`gauge` icon, `/`), and the account area. Later milestones add items; do not add disabled placeholders.
  - `AccountMenu`: a button showing the admin's display name; opens a menu with `Profile` (`user` icon) and `Sign out` (`log-out` icon). Keyboard: Enter or Space opens, Escape closes and returns focus, arrow keys move between items.
  - `PageHeader`: props `title: string`, `description?: string`, `actions?: ReactNode`; renders the page's single `<h1>`.
  - `EmptyState`: props `title: string`, `children?`, `action?: ReactNode`.
  - Signed-in routes render inside `AppShell`.

Copy (exact):

| Where | Text |
|---|---|
| Overview | PageHeader title `Overview`; EmptyState title `Nothing to show yet`, body `Vegapunk isn't capturing data from this host yet.` |
| Profile header | title `Profile`, description `Your admin account on this Vegapunk server` |
| Profile details | section `Details`; fields `Display name` (hint `Shown in the sidebar.`), `Username` (hint `You sign in with this.`); button `Save details`; success `Details saved` |
| Profile password | section `Password`; fields `Current password`, `New password` (hint `At least 10 characters.`), `Confirm new password`; button `Change password`; success `Password changed. Other sessions were signed out.`; mismatch `The two passwords don't match.` |

- [ ] **Step 1: Write the failing tests**

```ts
// AppShell.test.tsx
it("shows the admin's display name and navigation", ...)
it("opens the account menu and signs out", ...)            // click name → menuitem "Sign out" → POST /api/auth/sign-out → Sign in page
it("closes the account menu on Escape and restores focus", ...)

// Profile.test.tsx
it("saves details and updates the sidebar name", ...)      // PATCH /api/me {display_name:"Akira", username:"admin"} → "Details saved"; sidebar shows "Akira"
it("shows the server's message when details are rejected", ...)   // 422
it("changes the password", ...)                            // POST /api/me/password → success text; fields cleared
it("shows the server's message for a wrong current password", ...)  // 400 → "Your current password doesn't match."
it("blocks a mismatched confirmation without calling the API", ...)
```

- [ ] **Step 2: Run and confirm failure.**
- [ ] **Step 3: Implement the components and pages**, following `Main.dc.html` for the shell and `Profile.dc.html` for the page, and the `AppShell`, `SideNav`, `AccountMenu`, `PageHeader` and `EmptyState` READMEs in the design system.
- [ ] **Step 4: Run** `npm run lint && npm test && npm run build` → all pass.
- [ ] **Step 5: Check it in a browser** at 1440px and 375px wide: shell, drawer, account menu by keyboard, both Profile forms.
- [ ] **Step 6: Commit.**

---

### Task 8: One container

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `compose.yaml`
- Modify: `backend/vegapunk/app.py`, `.github/workflows/ci.yml`, `README.md`, `AGENTS.md`
- Test: `backend/tests/test_static.py`

**Interfaces:**
- Consumes: `Settings.static_dir`, `create_app`.
- Produces:
  - When `static_dir` is set, the app serves it: files under `/assets` directly; any other GET path that does not start with `/api` returns `index.html` (so client-side routes survive a refresh). An unknown `/api/...` path still returns a JSON 404. When `static_dir` is `None` nothing changes.
  - `Dockerfile`: stage 1 `node:24-slim` builds the frontend (`npm ci`, `npm run build`); stage 2 `python:3.12-slim` installs the backend with `uv sync --locked --no-dev`, copies the built frontend to `/app/static`, sets `VEGAPUNK_DATA_DIR=/data` and `VEGAPUNK_STATIC_DIR=/app/static`, declares `VOLUME /data`, exposes 8000, runs as a non-root user, and starts `uvicorn vegapunk.main:app --host 0.0.0.0 --port 8000`. A `HEALTHCHECK` calls `/api/health` using Python (no curl in the image).
  - `compose.yaml`: one service `vegapunk` with `container_name: vegapunk`, `build: .`, port `8000:8000`, a named volume at `/data`, `restart: unless-stopped`.

- [ ] **Step 1: Write the failing tests**

```python
# test_static.py   (fixture: a temp static dir with index.html containing "<title>Vegapunk</title>" and assets/app.js)
def test_serves_index_and_assets(static_client):
    assert "Vegapunk" in static_client.get("/").text
    assert static_client.get("/assets/app.js").status_code == 200

def test_client_side_routes_fall_back_to_index(static_client):
    assert "Vegapunk" in static_client.get("/profile").text

def test_unknown_api_path_stays_a_json_404(static_client):
    r = static_client.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")

def test_api_still_works_alongside_static(static_client):
    assert static_client.get("/api/health").json() == {"status": "ok"}

def test_no_static_dir_means_no_fallback(client):
    assert client.get("/profile").status_code == 404
```

- [ ] **Step 2: Run and confirm failure.**
- [ ] **Step 3: Implement static serving in `app.py`**, registered after the API router.
- [ ] **Step 4: Write `Dockerfile`, `.dockerignore` and `compose.yaml`.**
- [ ] **Step 5: Verify the container end to end** — `docker compose up --build -d`, then: `curl -s localhost:8000/api/health` → `{"status":"ok"}`; open `http://localhost:8000`, create the account, refresh on `/profile` and stay there; `docker compose restart` and confirm you are still signed in (data persisted); `docker exec -it vegapunk python -m vegapunk reset-password` works; `docker compose down`.
- [ ] **Step 6: Add a `docker` CI job** that runs `docker build .` on pull requests.
- [ ] **Step 7: Update the docs.** `README.md`: a short "Run with Docker" section (`docker compose up -d`, open port 8000, how to reset the password). `AGENTS.md`: add the new commands (`npm test`, `docker compose up --build`), the environment variables, and a line that the database is stdlib `sqlite3` with numbered SQL migrations.
- [ ] **Step 8: Run every check** (backend, frontend, `docker build .`) → all pass.
- [ ] **Step 9: Commit.**
