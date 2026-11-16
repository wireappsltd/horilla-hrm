# Horilla PMO API (for Meridian)

Base path: `/api/pmo/` on `hris.wireapps.co.uk` (prod) and `hris.dev.wireapps.co.uk` (dev).
All endpoints are read-only `GET`, return JSON, and require an API key.

## Authentication

Send the key in one of:

```
X-API-Key: <key>
Authorization: Api-Key <key>
```

- A missing or wrong key returns `401 {"detail": "..."}` with `WWW-Authenticate: Api-Key`.
- **Key rotation:** the server accepts several keys at once (`PMO_API_KEY=key1,key2`). To rotate, we add the new key, Meridian redeploys with it, and then we remove the old one. There's no downtime.
- **Call the API only from Meridian's server side** (route handlers or cron), never from the browser. A key shipped to the browser is public.

## Conventions

| Topic | Rule |
|---|---|
| Dates | `YYYY-MM-DD`. No time and no timezone, so they never shift by a day. |
| Timestamps | ISO 8601 with offset, in the server timezone (e.g. `2026-11-16T19:02:02.835812+05:30`). |
| Timestamp query params | Must include an offset (`Z` or `+05:30`). **URL-encode `+` as `%2B`**; an unencoded `+` becomes a space and returns 400. |
| Pagination | `limit` (default 20, values above 100 are clamped to 100) and `offset` (≥ 0). The response is `{count, limit, offset, results}`. |
| Errors | `400`, `401` and `404` from these endpoints return `{"detail": "<message>"}`. See [Errors](#errors) for caveats. |

---

## 1. `GET /employees/`

| Param | Notes |
|---|---|
| `search` | Case-insensitive match on first name, last name, email, department or job title. |
| `include_inactive` | `true` returns active **and** inactive employees. The default is active only. |
| `limit`, `offset` | Pagination. |
| `updated_since` | **Not supported. Returns 400.** Horilla has no last-modified time for employees, so the daily sync should page through `?include_inactive=true&limit=100`. |

## 2. `GET /employees/<id>/`

- `200` for active **and** inactive employees. Check `is_active`.
- `404` only when no employee with that ID exists (never created, or hard-deleted).

```json
{
  "id": 42,
  "name": "Jane Doe",
  "first_name": "Jane",
  "last_name": "Doe",
  "email": "jane.doe@wireapps.co.uk",
  "department": "Engineering",
  "job_title": "Senior Backend Engineer",
  "is_active": false,
  "date_joined": "2024-03-01",
  "leaving_date": "2026-10-31",
  "updated_at": null
}
```

- `leaving_date` is the last working date recorded in Horilla offboarding. It's `null` if the employee was archived without going through offboarding.
- `updated_at` is always `null` for employees (see `updated_since` above).
- `department`, `job_title` and `date_joined` can be `null` if HR hasn't filled in the employee's work information.

**Recommendation:** deactivate a Meridian user only when the API returns `is_active: false`. Treat a `404` as "needs manual review", not as a leaver.

## 3. `GET /leaves/`

| Param | Notes |
|---|---|
| `start_date`, `end_date` | **Required.** Returns leaves that overlap this range, edges included. |
| `employee_id` | Optional. |
| `status` | Optional. One value or a comma-separated list: `requested`, `approved`, `cancelled`, `rejected`. |
| `updated_since` | Optional timestamp with an offset. Returns leaves changed after it. |
| `limit`, `offset` | Pagination. Ordered by `id`. |

```json
{
  "count": 1, "limit": 100, "offset": 0,
  "results": [{
    "id": 9001,
    "employee_id": 42,
    "leave_type_id": 3,
    "leave_type": "Annual Leave",
    "start_date": "2026-10-20",
    "end_date": "2026-10-22",
    "start_breakdown": "full_day",
    "end_breakdown": "first_half",
    "requested_days": 2.5,
    "status": "approved",
    "cancellation_status": null,
    "updated_at": "2026-10-08T09:15:00+05:30"
  }]
}
```

- **Statuses:**
  - `status` is one of `requested`, `approved`, `cancelled` or `rejected`. An approved leave that is later cancelled becomes `cancelled` and is still returned, including through `updated_since`.
  - `cancellation_status` (`null`, `requested`, `approved` or `rejected`) tracks an employee's request to cancel an approved leave. While it is `requested`, the leave is still `approved`.
- **Half days:** `start_breakdown` and `end_breakdown` are each `full_day`, `first_half` or `second_half`.
- **`requested_days`:** Horilla's own count. It excludes company leave days and holidays when the leave type is configured to.
- **Inactive employees:** their leaves are still returned.
- **`updated_at`:** the latest of creation, review, cancellation review, and any edit recorded in the leave's audit history.
- **IDs:** a leave's `id` never changes and is never reused.
- **Deleted leaves:** if a leave is **hard-deleted**, it disappears and `updated_since` can't report it. Meridian should do a periodic full sync of the planning window (e.g. nightly, for the next 90 days) and remove any leave that's no longer returned.

## 4. `GET /leave-types/`

```json
{ "results": [ { "id": 3, "name": "Annual Leave", "is_paid": true, "is_active": true } ] }
```

Not paginated. **TODO (HR):** send the current production list so we can agree how it maps to Meridian's types (Annual, Sick, Personal, Public Holiday, Other). It comes from calling this endpoint on prod.

## 5. `GET /holidays/?year=2026`

```json
{
  "year": 2026,
  "results": [{
    "id": 12,
    "name": "Christmas Day",
    "start_date": "2026-12-25",
    "end_date": "2026-12-25",
    "recurring": true,
    "original_start_date": "2020-12-25",
    "company_id": 1,
    "company": "WireApps",
    "is_poya_holiday": false,
    "is_mercantile_holiday": false
  }]
}
```

- `year` is required.
- Recurring holidays are moved into the requested year. A 29 Feb holiday becomes 28 Feb in non-leap years.
- `company_id` / `company` say which Horilla company's employees a holiday applies to. `null` means all companies.
- Horilla scopes holidays by **company**, not by country or office.

## Errors

| Status | When | Body |
|---|---|---|
| 400 | Invalid or missing query parameter | `{"detail": "..."}` (JSON) |
| 401 | Missing or invalid key | `{"detail": "..."}` (JSON) |
| 404 | Unknown employee ID | `{"detail": "Employee not found."}` (JSON) |
| 404 | Unknown URL path | Django's 404 page (**HTML**, not JSON) |
| 500 | Unexpected server error | Django's 500 page (**HTML**, not JSON) |

Treat any non-JSON response as a transient server error and retry with backoff.

---

## Answers to Meridian's questions

| # | Question | Answer |
|---|---|---|
| 1 | Reachable from the public internet? | **TODO (DevOps):** confirm both hosts are public and that there's no IP allowlist. |
| 2 | Rate limits? | None in the application; DRF throttling is not configured. **TODO (DevOps):** confirm there are no proxy or WAF limits. |
| 3 | Two keys at once during rotation? | **Yes.** `PMO_API_KEY` takes a comma-separated list; see [Authentication](#authentication). |
| 4 | Key delivery | **TODO (owner):** dev and prod keys go out separately through the password manager, never by email or chat. |
| 5 | Is the employee `id` stable and never reused? | **Yes.** It's a database primary key that never changes, and PostgreSQL sequences don't reuse values. An employee can be hard-deleted (the ID then returns 404), but HR normally archives instead. |
| 6 | Which email? Can it be empty? Is it unique? | It's the employee's **Horilla login email** (the user account's email, falling back to the employee record's email). The employee email is required and **unique** in Horilla, and the login account is created from it. It is the work address for WireApps staff. |
| 7 | Do department and job title change often? Can someone have several? | **One** department and **one** job title per employee (a single work-information record). They change only when HR edits the record, so rarely (promotions, transfers). |
| 8 | Fixed timezone? | Dates are plain `YYYY-MM-DD` with no timezone and never shift. Timestamps carry an explicit offset in the server timezone (`TIME_ZONE`, +05:30). |
| 9 | Storing leave type (sick leave is special category data under UK GDPR) | **TODO (WireApps DPO/HR):** decide. Today the API returns the real leave type. If needed, we can add a setting so selected types come back as a generic "Leave". |
| 10 | Dev test employees | **TODO (HR, on dev):** one active employee; one inactive with an offboarding last working date; one with approved, requested and cancelled leaves; one with a half-day leave. |
| 11 | Contact and breaking changes | **TODO (owner):** name the contact. Changes to these endpoints will be additive (new fields only). Any rename or removal will be announced in advance, and a `/api/pmo/v1/` prefix can be added before such a change. |
| 12 | Error bodies | See [Errors](#errors). JSON for 400, 401 and employee 404; HTML for unknown paths and 500s. |
