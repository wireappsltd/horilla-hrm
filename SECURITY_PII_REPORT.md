# HORILLA HRM — PII HANDLING & SECURITY POSTURE REPORT

**Document Classification:** CONFIDENTIAL — For ISC Distribution Only
**Prepared for:** Information Security Council (ISC) — Monthly Council Meeting
**Prepared by:** Samitha (Technical Lead), with collaboration from Engineering
**Date:** 27 February 2026
**Reference Standard:** ISO/IEC 27001:2022
**Scope:** Horilla HRM Application — Pre-Go-Live Security Assessment

---

## 1. EXECUTIVE SUMMARY

This report presents the findings of a comprehensive security and PII (Personally Identifiable Information) assessment of the Horilla HRM platform, conducted ahead of the scheduled go-live and employee onboarding. The audit covered: PII data inventory, authentication & access controls, encryption practices, OWASP Top 10 vulnerability assessment, and audit/logging capabilities.

### Overall Risk Rating: **HIGH**

| Area | Rating | Summary |
|---|---|---|
| PII Data Inventory | **Critical** | 180+ PII fields across 18 modules; **zero field-level encryption** |
| Authentication & Access Control | **Medium** | Solid Django auth with 2FA available, brute-force protection; some gaps |
| Data Encryption (at rest) | **Critical** | No database field encryption; plaintext credentials in DB |
| Data Encryption (in transit) | **Good** | HTTPS/HSTS enforced in production; secure cookie flags present |
| OWASP Vulnerability Exposure | **High** | Remote code execution risk, missing auth on exports, XSS vectors |
| Audit & Logging | **Medium** | Audit trail exists (django-simple-history) but gaps in access logging |

**Recommendation to ISC:** Address all CRITICAL findings before go-live. A remediation roadmap is provided in Section 8.

---

## 2. PII DATA INVENTORY (ISO 27001 — A.8: Asset Management)

The system stores **180+ PII data fields** across **18 application modules**. None use field-level encryption.

### 2.1 CRITICAL Sensitivity PII (Requires Immediate Protection)

| Module | Model | Field(s) | Data Type | Encrypted? |
|---|---|---|---|---|
| Employee | Employee | `nic` (National ID) | CharField(12) | **No** |
| Employee | Employee | `passport` | CharField(50) | **No** |
| Employee | Employee | `dob` (Date of Birth) | DateField | **No** |
| Employee | Employee | `emergency_contact`, `emergency_contact_name` | CharField | **No** |
| Employee | EmployeeBankDetails | `account_number` | CharField(50) | **No** |
| Employee | EmployeeBankDetails | `swift_code`, `branch`, `any_other_code1/2` | CharField | **No** |
| Employee | EmployeeWorkInformation | `basic_salary`, `salary_hour` | IntegerField | **No** |
| Recruitment | Candidate | `resume` (file) | FileField | **No** |
| Recruitment | LinkedInAccount | `api_token` | CharField(500) | **No** |
| Payroll | Contract | `contract_document`, `wage` | FileField/Float | **No** |
| Biometric | BiometricDevices | `bio_password`, `zk_password` | CharField(100) | **No** |
| Biometric | BiometricDevices | `api_key`, `api_secret`, `api_token` | CharField | **No** |
| Base | DynamicEmailConfiguration | `password` | CharField | **No** |
| Outlook | OutlookConfiguration | `token` | JSONField | **No** |
| Documents | Document | `document` (identity docs) | FileField | **No** |

### 2.2 HIGH Sensitivity PII

| Module | Field(s) | Notes |
|---|---|---|
| Employee | `employee_first_name`, `employee_last_name`, `email`, `phone`, `address`, `city`, `state`, `zip`, `country` | Full identity + contact |
| Employee | `gender`, `blood_group`, `marital_status`, `children`, `qualification` | Personal/medical |
| Employee | `employee_profile` (ImageField) | Biometric-adjacent (photo) |
| Recruitment | `name`, `email`, `mobile`, `address`, `dob`, `gender`, `profile` | Candidate PII |
| Attendance | Clock-in/out timestamps, GPS/location data | Behavioural tracking |
| Onboarding | `token` (portal access), `profile` | Access + photo |
| LDAP | `bind_dn`, `bind_password` | Directory service credentials |

### 2.3 Dynamic/Unstructured PII Risk

Several models contain **JSONField** columns (`additional_info`, `answer_json`, `tracking_fields`, `data`) that can store **arbitrary PII** without schema validation. The `DynamicField` model allows creation of custom CharField, TextField, DateField, and FileField types — meaning new PII categories can be introduced without code review.

### 2.4 File Upload PII

**40+ FileField/ImageField** definitions across the application store documents in the `/media/` directory. These include resumes, contracts, identity documents, profile photos, and supporting evidence — all stored **unencrypted on disk** with **no file type validation**.

---

## 3. AUTHENTICATION & ACCESS CONTROL (ISO 27001 — A.9: Access Control)

### 3.1 Strengths

| Control | Status | Detail |
|---|---|---|
| Password Hashing | **Good** | Django PBKDF2 (industry standard) |
| Password Validators | **Good** | Similarity, minimum length (8), common password, numeric-only checks |
| Multi-Factor Authentication | **Available** | 6-digit email OTP, 10-minute expiry — **but disabled by default** (`TWO_FACTORS_AUTHENTICATION = False`) |
| Brute-Force Protection | **Good** | Fail2Ban-style: 3 attempts, 5-minute ban, per-session tracking, logged to `security.log` |
| Forced Password Change | **Good** | New employees must change password on first login (`ForcePasswordChangeMiddleware`) |
| RBAC | **Good** | Django permissions + custom decorators (`@manager_can_enter`, `@owner_can_enter`, `@permission_required`) |
| Company-Level Isolation | **Good** | `CompanyMiddleware` filters all queries by selected company context |
| CSRF Protection | **Enabled** | `CsrfViewMiddleware` in middleware stack |
| Clickjacking Protection | **Enabled** | `X_FRAME_OPTIONS = "SAMEORIGIN"` |

### 3.2 Weaknesses

| Issue | Severity | Detail | ISO 27001 Control |
|---|---|---|---|
| 2FA disabled by default | **High** | Must be explicitly enabled in `horilla_apps.py` | A.9.4.2 |
| No API rate limiting | **High** | No DRF throttling classes configured; enables brute-force on `/api/auth/login/` | A.9.4.2 |
| JWT token lifetime: 30 days | **High** | `ACCESS_TOKEN_LIFETIME: timedelta(days=30)` — excessive | A.9.4.3 |
| Missing `SESSION_COOKIE_HTTPONLY` | **Medium** | Not explicitly set to `True` — vulnerable to JS session theft | A.14.1.2 |
| Missing `CSRF_COOKIE_HTTPONLY` | **Medium** | Not explicitly set — CSRF token accessible via JS | A.14.1.2 |
| No Content-Security-Policy header | **Medium** | Missing CSP header allows inline scripts/XSS | A.14.1.2 |
| No Referrer-Policy header | **Medium** | PII may leak in referrer headers | A.14.1.2 |
| No password expiration | **Medium** | No forced rotation for existing users | A.9.4.3 |
| No concurrent session limit | **Medium** | Users can have unlimited active sessions | A.9.4.2 |
| Candidate auth uses email + mobile | **Low** | Non-standard auth backend; weak credential pair | A.9.4.2 |

---

## 4. DATA ENCRYPTION ASSESSMENT (ISO 27001 — A.10: Cryptography)

### 4.1 Encryption at Rest

| Area | Status | Finding |
|---|---|---|
| Database field encryption | **Not Implemented** | Zero fields use encrypted field types. Bank accounts, NIC, passport, salary — all plaintext |
| File storage encryption | **Not Implemented** | `/media/` directory files (resumes, contracts, ID docs) stored unencrypted |
| Database-level encryption | **Not Configured** | Default SQLite (no encryption); PostgreSQL available but TDE not configured |
| Credential storage | **Critical** | Biometric passwords, email server password, API tokens stored as **plaintext CharField** |
| Backup encryption | **Unknown** | `horilla_backup` module supports DB dump + Google Drive; encryption status unclear |

### 4.2 Encryption in Transit

| Setting | Value | Status |
|---|---|---|
| `SECURE_SSL_REDIRECT` | `True` (prod) | **Good** |
| `SECURE_HSTS_SECONDS` | `31536000` (1 year) | **Good** |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | **Good** |
| `SECURE_HSTS_PRELOAD` | `True` | **Good** |
| `SESSION_COOKIE_SECURE` | `True` (prod) | **Good** |
| `CSRF_COOKIE_SECURE` | `True` (prod) | **Good** |
| `SECURE_CONTENT_TYPE_NOSNIFF` | `True` | **Good** |
| `SECURE_BROWSER_XSS_FILTER` | `True` | **Good** |
| `SECURE_PROXY_SSL_HEADER` | Configured | **Good** |

**Note:** All production security settings are conditional on `DEBUG=False`. The default is `DEBUG=True`, meaning **a misconfigured deployment will have NO transport security hardening**.

### 4.3 Secret Management

| Finding | Severity | Detail |
|---|---|---|
| Hardcoded default `SECRET_KEY` | **Critical** | `django-insecure-j8op9)1q8$1&0^s&p*_0%d#pr@w9qj@1o=3#@d=a(^@9@zd@%j` in settings.py and `.env.dist` |
| `ALLOWED_HOSTS = ["*"]` | **Critical** | Accepts requests from any hostname — host header injection risk |
| `DEBUG = True` by default | **Critical** | Exposes stack traces, SQL queries, configuration in error pages |
| `.env` file for secrets | **Acceptable** | Uses `django-environ` — adequate for non-containerised deployments |

---

## 5. VULNERABILITY ASSESSMENT — OWASP TOP 10 (ISO 27001 — A.14: System Security)

### 5.1 CRITICAL Vulnerabilities

#### 5.1.1 Remote Code Execution via `exec()` — A03:2021 Injection

**Location:** `payroll/methods/tax_calc.py:104`

```python
code = filing.python_code
exec(code, {}, local_vars)
```

The `FilingStatus` model stores Python code as a `TextField` which is executed via `exec()` at runtime for tax calculations. An admin user (or anyone with database access) can inject **arbitrary Python code** that will execute on the server. Sandboxing is minimal (only `print()` is blocked).

**Impact:** Full server compromise, data exfiltration, lateral movement.

#### 5.1.2 Missing Authentication on Data Exports

Multiple export functions serving CSV/Excel/PDF of sensitive data lack authentication decorators:

| Function | File | Data Exposed |
|---|---|---|
| `payslip_export()` | `payroll/views/views.py:933` | Salary, tax, deductions |
| `attendance_export()` | `attendance/views/views.py:314` | Employee attendance records |
| `employee_export()` | `employee/views.py:2494` | Full employee PII |
| `project_bulk_export()` | Various | Project assignment data |

### 5.2 HIGH Vulnerabilities

#### 5.2.1 Cross-Site Scripting (XSS) via `mark_safe()` — A07:2021

Multiple widget classes render user-influenced data as trusted HTML:

- `recruitment/widgets.py:37` — Dynamic JavaScript injection in recruitment forms
- `offboarding/forms.py:377` — HTML table rendered without escaping
- `payroll/widgets/component_widgets.py:36, 64, 134` — Multiple payroll component widgets

#### 5.2.2 CSRF Protection Bypassed on 9 Endpoints — A01:2021

`@csrf_exempt` decorators found on:

- `facedetection/views.py:86` — Face detection upload
- `onboarding/views.py:1438, 1631` — Onboarding portal endpoints
- `base/views.py:5284, 5377` — Core application views
- `recruitment/views/surveys.py:83` — Candidate survey submissions

#### 5.2.3 Unrestricted File Uploads — A04:2021

40+ `FileField`/`ImageField` definitions with **no file extension validation, no MIME type checking, and no file size limits**. Risk of malicious file upload (web shells, malware).

### 5.3 MEDIUM Vulnerabilities

| Issue | Detail |
|---|---|
| Raw SQL in payroll | `cursor.execute(f"TRUNCATE TABLE {table_name}...")` — table name from model meta, low exploitability but poor practice |
| Bare exception handlers | 659 `try:` blocks, 20+ bare `except:` clauses — masks errors and security events |
| Long JWT lifetime | 30-day access tokens; stolen tokens remain valid for a month |
| No API rate limiting | `/api/auth/login/` endpoint vulnerable to credential stuffing |

---

## 6. AUDIT & LOGGING (ISO 27001 — A.12: Operations Security)

### 6.1 What EXISTS

| Capability | Implementation | Notes |
|---|---|---|
| Model change history | `django-simple-history` via `HorillaAuditLog` | Tracks field-level changes on key models (Contract, WorkInformation, Attendance) |
| Audit tags/categories | `AuditTag` model | Categorization of audit events |
| Failed login logging | `Fail2BanMiddleware` → `security.log` | Logs username + IP on failed attempts |
| Email logging | `EmailLog` model | Tracks sent emails (subject, from, to, body) |

### 6.2 What is MISSING

| Gap | ISO 27001 Control | Impact |
|---|---|---|
| No centralized `LOGGING` configuration in `settings.py` | A.12.4.1 | No structured application logging |
| No logging of successful logins | A.12.4.1 | Cannot audit who accessed the system and when |
| No logging of PII data access (reads) | A.12.4.1 | Cannot demonstrate "need to know" enforcement |
| No logging of data exports | A.12.4.1 | CSV/Excel exports of PII are untracked |
| No log integrity protection | A.12.4.2 | Logs can be modified or deleted |
| No data retention/deletion policy | A.8.3.2 | No automated purge of expired PII (candidates, ex-employees) |
| No data anonymization capability | A.18.1.4 | Cannot fulfil GDPR/privacy right-to-erasure requests |

---

## 7. ISO 27001 ANNEX A CONTROL MAPPING SUMMARY

| ISO 27001 Control | Status | Key Gaps |
|---|---|---|
| **A.5 — Information Security Policies** | Not Assessed | Organizational policy outside code scope |
| **A.6 — Organization of Information Security** | Partial | RBAC exists; no separation of duties enforced in code |
| **A.8 — Asset Management** | **Weak** | PII inventory not maintained; no data classification labels |
| **A.9 — Access Control** | **Moderate** | RBAC + 2FA available; 2FA off by default; no rate limiting on API |
| **A.10 — Cryptography** | **Critical** | Zero field-level encryption; plaintext credentials; hardcoded secrets |
| **A.12 — Operations Security** | **Weak** | Minimal logging; no access audit; no data retention automation |
| **A.13 — Communications Security** | **Good** | HTTPS/HSTS/secure cookies enforced in production |
| **A.14 — System Security** | **High Risk** | RCE via exec(); XSS; CSRF bypasses; unrestricted uploads |
| **A.18 — Compliance** | **Weak** | No data subject access/erasure capability; no retention policy |

---

## 8. REMEDIATION ROADMAP

### PHASE 1 — BEFORE GO-LIVE (Immediate / This Week)

| # | Action | Priority | Effort |
|---|---|---|---|
| 1 | Set `DEBUG=False`, generate unique `SECRET_KEY`, restrict `ALLOWED_HOSTS` to actual domains | **Critical** | 1 hour |
| 2 | Enable 2FA (`TWO_FACTORS_AUTHENTICATION = True`) for all HR staff | **Critical** | 1 hour |
| 3 | Add `@login_required` + `@permission_required` to ALL export views (payslip, attendance, employee) | **Critical** | 2–4 hours |
| 4 | Set `SESSION_COOKIE_HTTPONLY = True`, `CSRF_COOKIE_HTTPONLY = True` | **Critical** | 30 min |
| 5 | Rotate any default credentials; ensure `.env` has unique values | **Critical** | 1 hour |
| 6 | Review and justify or remove all 9 `@csrf_exempt` decorators | **High** | 2–3 hours |

### PHASE 2 — FIRST 30 DAYS POST GO-LIVE

| # | Action | Priority | Effort |
|---|---|---|---|
| 7 | Implement field-level encryption for: bank account numbers, NIC, passport, salary, API tokens/passwords | **Critical** | 2–3 days |
| 8 | Add file upload validation (extension whitelist, MIME check, size limits) across all 40+ FileField instances | **High** | 1–2 days |
| 9 | Reduce JWT `ACCESS_TOKEN_LIFETIME` to 1–7 days; implement refresh token rotation | **High** | 4 hours |
| 10 | Implement DRF throttling/rate limiting on API login and sensitive endpoints | **High** | 4 hours |
| 11 | Replace `exec()` in tax calculation with a safe expression evaluator (e.g., `asteval` or predefined formula engine) | **Critical** | 1–2 days |
| 12 | Configure centralized `LOGGING` in settings.py with structured JSON output | **High** | 4 hours |
| 13 | Add Content-Security-Policy and Referrer-Policy headers | **Medium** | 2 hours |

### PHASE 3 — 60–90 DAYS (ISO 27001 Readiness)

| # | Action | Priority | Effort |
|---|---|---|---|
| 14 | Implement PII access logging (who viewed which employee record, when) | **High** | 3–5 days |
| 15 | Implement data retention policy and automated purge (e.g., candidate data after 12 months) | **High** | 2–3 days |
| 16 | Implement data subject access request (DSAR) and right-to-erasure/anonymization tooling | **High** | 3–5 days |
| 17 | Implement database-level encryption at rest (PostgreSQL TDE or provider-managed) | **Medium** | 1–2 days |
| 18 | Implement signed/time-limited URLs for media file access (resumes, ID docs) | **Medium** | 1–2 days |
| 19 | Conduct dependency vulnerability scan (`pip-audit` / `safety check`) and remediate | **Medium** | 1 day |
| 20 | Add password expiration policy (e.g., 90-day rotation) | **Medium** | 4 hours |
| 21 | Implement concurrent session limits | **Low** | 4 hours |
| 22 | Remediate all `mark_safe()` XSS vectors with proper escaping | **High** | 1 day |

---

## 9. RECOMMENDATIONS TO THE ISC

1. **Do not go live without completing Phase 1 items.** The combination of `DEBUG=True`, unprotected export endpoints, and disabled 2FA represents an unacceptable risk for a system handling employee PII at scale.

2. **Assign a remediation owner** for each Phase 2 and Phase 3 item with target completion dates tracked at ISC level.

3. **Commission a penetration test** after Phase 2 completion, before pursuing ISO 27001 Stage 1 audit.

4. **Establish a PII data register** (as required by ISO 27001 A.8) cataloguing all 180+ fields identified in this report, with assigned data owners and classification levels.

5. **Review third-party integration risks** — LinkedIn API, Microsoft Outlook OAuth, biometric device integrations, and GCP storage all transmit or store PII outside the primary database.

6. **Implement a data processing agreement (DPA) register** for all third-party processors receiving employee PII.

---

## APPENDICES

**Appendix A** — Full PII Field Inventory (180+ fields across 18 modules — available upon request)
**Appendix B** — Authentication Configuration Detail
**Appendix C** — OWASP Vulnerability Detail with File Paths and Line Numbers
**Appendix D** — Django Security Settings Audit

---

*This report was generated through static code analysis of the Horilla HRM repository. It does not constitute a penetration test or dynamic application security test (DAST). A full penetration test is recommended before ISO 27001 certification.*
