# CRM backend

FastAPI + PostgreSQL backend for the local CRM prototype.

## Run locally

```bash
docker compose -f backend/compose.yaml up --build
```

Open:

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- OpenAPI: `http://localhost:8000/openapi.json`
- Health: `http://localhost:8000/health`
- PostgreSQL from the host: `localhost:5433`

Demo accounts all use password `demo12345`:

- `manager@crm.local`
- `scenarist@crm.local`
- `editor@crm.local`
- `client@crm.local`

The frontend base URL is `http://localhost:8000/api/v1`.

## Initial API

- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `GET|POST /api/v1/clients`
- `GET|POST /api/v1/projects`
- `GET /api/v1/users/scenarists`
- `GET|POST /api/v1/scenarios`
- `GET|PATCH /api/v1/scenarios/{id}`
- `PUT /api/v1/scenarios/{id}/approvals/{stage}`
- `GET|POST /api/v1/scenarios/{id}/comments`
- `PUT /api/v1/scenarios/{id}/montage`
- `PUT /api/v1/scenarios/{id}/montage/editor`
- `PUT /api/v1/scenarios/{id}/publication`

`GET /scenarios` returns lightweight list items with frontend-ready `title`, `project`,
`scenarist`, `deadline`, `score`, and `comments_count`. Heavy `research`, `content`, `approvals`,
`montage`, and `publication` sections are returned only by `GET /scenarios/{id}`.

List filters include repeatable `status`, `project_id`, `assigned_scenarist_id`,
`deadline_from`, `deadline_to`, full-text-style `search`, `sort_by`, and `sort_order`.

## Creation permissions

- Only a manager can create clients and projects. A project must reference an existing,
  active client.
- A manager can create a scenario in an active project and optionally assign an active
  user with the `scenarist` role. `GET /api/v1/users/scenarists` is the manager-only
  assignment directory.
- A scenarist can create a scenario only in an existing active project. The backend
  always assigns the new scenario to the authenticated scenarist.
- Editors and clients cannot create root scenario rows. In the sheet API all four roles
  receive the same working columns. Manager, scenarist, and editor may update every
  inline/detail field once its workflow section is available. Client can update only the
  available pre-generation/final-client decision, comment, and pre-generation note fields.
  Row visibility remains role-scoped.

Creation errors use stable semantics: `403` for a forbidden role or attempted cross-user
assignment, `404` for a missing referenced entity, and `409` for an inactive entity,
wrong assignment role, or duplicate business identifier.

Google Sheets import will be a separate read-only adapter. It is intentionally not connected to the production server in this first local slice.

The approved source mapping is documented in
[`docs/google-sheets-scenarist-mapping.md`](docs/google-sheets-scenarist-mapping.md).
The client portal mapping is documented in
[`docs/google-sheets-client-mapping.md`](docs/google-sheets-client-mapping.md).

## Deploy to Railway

The repository is ready for Railway Dockerfile deployment. `railway.json` runs Alembic
migrations before each deployment, checks `/health`, and the container listens on Railway's
`PORT` automatically.

1. In Railway, create a project and add a PostgreSQL service.
2. Add a service from this GitHub repository.
3. In the backend service Variables tab, configure:

   ```env
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   APP_ENV=production
   APP_SECRET_KEY=<a-long-random-secret>
   CORS_ORIGINS=https://your-frontend-domain.example
   ACCESS_TOKEN_EXPIRE_MINUTES=480
   SEED_DEMO_DATA=true
   ```

   `SEED_DEMO_DATA=true` creates the four documented demo accounts for temporary testing.
   Set it to `false` and change/remove demo credentials before real use.

4. Open Settings → Networking and generate a public domain.
5. Verify `https://<domain>/health` and `https://<domain>/docs`.
6. Set the frontend API base URL to `https://<domain>/api/v1` and add the exact frontend
   origin to `CORS_ORIGINS`.

Do not paste a database password or `APP_SECRET_KEY` into GitHub. Railway variables and the
Postgres reference variable keep secrets outside the repository.
