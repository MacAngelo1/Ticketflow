# TicketFlow

A REST API for event management, ticket reservations, seat selection, and admission control. Built with FastAPI, PostgreSQL, SQLAlchemy, and uv.

## Features

- JWT authentication with customer, organizer, and admin roles.
- Event creation, updates, cancellation, and admin moderation.
- Ticket categories with quantities and decimal NGN pricing.
- Assigned seating and general-admission inventory.
- Expiring reservations with idempotency keys.
- PostgreSQL row locking to prevent overselling and seat conflicts.
- Simulated payment, confirmation, and full-order refunds.
- QR ticket downloads and single-use check-in.
- Background console confirmation notifications.
- Shared database-backed rate limits.
- Organizer sales reports, platform reports, and admin audit logs.

Payments and refunds are simulated; no money is transferred. Notifications are logged to the console rather than delivered by email. Administration is provided through API endpoints, not a graphical dashboard.

## Requirements

- Python 3.12–3.14
- uv
- PostgreSQL, or Docker Compose for the supplied PostgreSQL 18 service

## Setup

Run commands from the project root. If uv is not installed:

```bash
python -m pip install --user uv
```

The commands below use `python -m uv` to avoid executable PATH issues. `uv` can be used directly when available.

### Install dependencies

```bash
python -m uv sync --locked
```

### Configure the environment

For a new checkout without a configured `.env`:

```bash
cp .env.example .env
python -m uv run python -c 'import secrets; print(secrets.token_hex(32))'
```

Paste the generated value into `SECRET_KEY` in `.env`. Keep existing configured values when restarting an existing installation.

### Start PostgreSQL

With Docker Desktop running:

```bash
docker compose up -d postgres
docker compose ps
```

The supplied configuration exposes PostgreSQL on localhost port 5433:

```dotenv
DATABASE_URL=postgresql+psycopg://ticketflow:ticketflow_local@127.0.0.1:5433/ticketflow
```

Alternatively, create a role and database in an existing PostgreSQL installation. Run these as separate SQL statements using an administrator connection with auto-commit enabled:

```sql
CREATE ROLE ticketflow WITH LOGIN PASSWORD 'ticketflow_local';
```

```sql
CREATE DATABASE ticketflow OWNER ticketflow;
```

For an installation using port 5432, update `.env` accordingly:

```dotenv
DATABASE_URL=postgresql+psycopg://ticketflow:ticketflow_local@127.0.0.1:5432/ticketflow
```

The supplied credentials are local development examples. Do not recreate an existing database or overwrite an existing role unnecessarily.

### Apply migrations

```bash
python -m uv run alembic upgrade head
```

### Create an administrator

```bash
python -m uv run python -m app.cli create-admin admin@example.com
```

The command prompts for a password and records the account creation in the audit log. Public registration cannot create admin accounts.

### Start the API

```bash
python -m uv run uvicorn app.main:app --reload --port 8001
```

- Interactive API documentation: http://127.0.0.1:8001/docs
- OpenAPI schema: http://127.0.0.1:8001/openapi.json
- Process health: http://127.0.0.1:8001/health

## Authentication

Register through `POST /auth/register`:

```json
{
  "email": "organizer@example.com",
  "full_name": "Event Organizer",
  "password": "Organizer123!",
  "role": "organizer"
}
```

Supported registration roles are `customer` and `organizer`. Login through `POST /auth/token` using form fields:

- `username`: the registered email address
- `password`: the account password
- `grant_type`: `password`

In Swagger, use **Authorize** with the same email and password. Leave client ID and client secret empty. Protected requests require `Authorization: Bearer <access_token>`.

## Purchase workflow

1. An organizer creates an event through `POST /events/`. New events are pending.
2. The organizer adds ticket categories through `POST /events/{event_id}/ticket-types`.
3. For seated events, the organizer defines seats through `POST /events/{event_id}/seats`.
4. An admin activates the event through `PATCH /admin/events/{event_id}/moderation`.
5. A customer creates a reservation through `POST /reservations/`, providing an `Idempotency-Key` header.
6. The customer calls `POST /reservations/{reservation_id}/pay-demo` with `{"outcome":"success"}`.
7. The customer confirms through `POST /reservations/{reservation_id}/confirm`.
8. Tickets are available through `GET /tickets/me`, with PNG downloads at `GET /tickets/{ticket_id}/qr`.

Example reservation body, using actual ticket-type and seat IDs:

```json
{
  "ticket_type_id": 1,
  "quantity": 1,
  "seat_ids": [1]
}
```

For general admission, use `seat_ids: []`. Reuse an idempotency key only when retrying the same logical reservation. A new order requires a new key.

## Main endpoints

| Area | Endpoints |
| --- | --- |
| Authentication | POST /auth/register, POST /auth/token, GET /auth/me |
| Events | GET/POST /events/, GET /events/mine, GET/PUT/DELETE /events/{event_id} |
| Inventory | GET/POST /events/{event_id}/ticket-types, GET/POST /events/{event_id}/seats |
| Organizer reports | GET /events/{event_id}/sales |
| Reservations | POST /reservations/, GET /reservations/me |
| Order actions | POST /reservations/{reservation_id}/pay-demo, /confirm, /cancel |
| Tickets | GET /tickets/me, GET /tickets/{ticket_id}/qr, POST /tickets/check-in |
| Administration | GET /admin/users, /admin/events, /admin/reservations, /admin/reports, /admin/audit-logs, /admin/settings |
| Moderation | PATCH /admin/users/{user_id}/status, PATCH /admin/events/{event_id}/moderation |
| Refunds | POST /admin/reservations/{reservation_id}/refund |

Refer to Swagger for request schemas and required permissions.

## Data integrity and lifecycle

- Inventory mutations acquire an event-row lock before checking availability.
- Unpaid reservations expire after the configured hold period. Paid and confirmed orders retain inventory until refunded.
- Prices are calculated server-side using Decimal/Numeric and saved on reservations.
- Confirmation retries return the existing tickets rather than issuing duplicates.
- QR tokens have a separate purpose from access tokens. Check-in validates live ticket status and rejects replay.
- Event deletion cancels the event while preserving order history. Cancellation does not automatically refund orders.
- Event detail changes require no live reservations or paid orders and trigger reapproval.
- Refunds cover the full order and reject orders containing checked-in tickets.
- Public event browsing includes active upcoming events. Management endpoints enforce role and ownership checks.

## Configuration

| Variable | Default or purpose |
| --- | --- |
| DATABASE_URL | PostgreSQL connection URL using psycopg |
| SECRET_KEY | Required signing secret of at least 32 characters |
| ACCESS_TOKEN_MINUTES | 30 |
| RESERVATION_MINUTES | 10 |
| MAX_TICKETS_PER_ORDER | 6 |
| RATE_LIMIT_ENABLED | true |
| DEMO_PAYMENTS_ENABLED | true |

Restart the API after configuration changes. `/admin/settings` provides a read-only view of non-secret settings. Setting `DEMO_PAYMENTS_ENABLED=false` disables the simulation; it does not configure a real gateway.

Rate limits use fixed one-minute windows: 20 shared registration/login attempts per IP, 5 reservation attempts per account, and 10 payment attempts per account. Exceeded limits return HTTP 429 with `Retry-After`.

## Testing

Use a dedicated database whose name ends in `_test`. Tests truncate its application tables. Never use the application database for testing.

With Docker:

```bash
docker compose exec postgres createdb -U ticketflow ticketflow_test
export TEST_DATABASE_URL='postgresql+psycopg://ticketflow:ticketflow_local@127.0.0.1:5433/ticketflow_test'
```

For an existing PostgreSQL installation, create `ticketflow_test` owned by `ticketflow` and use its actual port in the URL.

```bash
DATABASE_URL="$TEST_DATABASE_URL" python -m uv run alembic upgrade head
python -m uv run pytest -q
python -m uv run ruff check app tests migrations
```

Tests cover authentication, roles, validation, moderation, reservations, payment retries, refunds, expiry, rate limits, QR generation, check-in replay, and concurrent attempts to reserve the same seat or final general-admission ticket.

## Project structure

```text
app/
  api/               HTTP endpoints
  services/          Inventory, notifications, rate limiting, shared rules
  config.py          Environment settings
  database.py        Engine and request-scoped sessions
  models.py          SQLAlchemy tables and constraints
  schemas.py         Request and response validation
  security.py        Password hashing and JWT dependencies
  main.py            FastAPI application
  cli.py             Local administrative commands
migrations/          Alembic schema revisions
tests/               PostgreSQL integration tests
```

## Operational notes

- Console notifications are best effort; durable delivery would require an outbox and worker.
- Payment and refund endpoints are simulations, not gateway integrations.
- A paid but unconfirmed order retains stock until confirmed or refunded.
- Event-level locking prioritizes correctness but serializes writes for a single popular event.
- The application uses NGN only and supports one ticket category per reservation.
- QR images are generated on download; no email attachments are sent.
- Audit logs provide application-level accountability, not tamper-proof storage.
- Keep `.env`, virtual environments, and caches out of version control.

Old rate-limit counters can be removed with:

```bash
python -m uv run python -m app.cli cleanup-rate-limits
```
