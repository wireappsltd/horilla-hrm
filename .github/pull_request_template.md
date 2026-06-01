## ✨ Pull Request Summary

## What type of PR is this? (select one)

- [ ] ✨ Feature / Enhancement
- [ ] 🐛 Bug Fix
- [ ] ⚡ Hotfix
- [ ] 🧠 Refactor
- [ ] 🧹 Chore (maintenance, dependency bump, docs)
- [ ] 🗄️ Migration / Data fix

---

## 🔧 Description

<!-- Short description of the change and the user-facing impact. -->

## 📋 Ticket Link(s)

<!-- e.g. HRMOD-431 — https://wireapps.atlassian.net/browse/HRMOD-431 -->

## 📦 Scope (apps touched)

<!-- Tick the Django apps modified in this PR. -->

- [ ] `employee`
- [ ] `attendance`
- [ ] `leave`
- [ ] `payroll`
- [ ] `recruitment`
- [ ] `onboarding` / `offboarding`
- [ ] `pms`
- [ ] `asset`
- [ ] `helpdesk`
- [ ] `project`
- [ ] `base` / `horilla` (core)
- [ ] `horilla_api` (REST API)
- [ ] `notifications` / `horilla_automations`
- [ ] `auth` / `biometric` / `horilla_ldap` / `outlook_auth`
- [ ] `templates/` (UI only)
- [ ] Other: <!-- specify -->

## 🔍 What's Done

<!-- Bullet the key changes — models, views, serializers, templates, migrations, signals, permissions, etc. -->

- ...
- ...
- ...

## 🗄️ Migrations

- [ ] No migrations
- [ ] New migration(s) included — list file(s): <!-- e.g. leave/migrations/0042_xxx.py -->
- [ ] Data migration / backfill script included
- [ ] Reversible? <!-- yes / no — explain if no -->

## 🔐 Security & Permissions

<!-- Required for any view/API/permission change. Delete the section if N/A. -->

- [ ] Touches authentication, sessions, or 2FA
- [ ] Touches `@login_required` / `@permission_required` / DRF permissions
- [ ] Touches multi-tenant company scoping (`HorillaCompanyManager`)
- [ ] Handles PII / payroll / employee documents
- [ ] No new secrets committed (`.env`, credentials, tokens)

## ✅ Checklist

- [ ] Feature tested locally against a fresh `manage.py migrate`
- [ ] `pre-commit run --all-files` passes (black, isort, trailing whitespace)
- [ ] `python manage.py check` passes
- [ ] `python manage.py makemigrations --check --dry-run` is clean (no missing migrations)
- [ ] Relevant unit tests added / updated
- [ ] No `print()` / debug code / commented-out blocks left behind
- [ ] No new untracked files committed (one-off scripts, `*.pdf`, dumps)
- [ ] Docs / `CLAUDE.md` / inline help updated if behaviour changed

## 📸 UI (if applicable)

<!-- Add screenshots / GIFs for any change to templates, forms, or list views. Include before/after where it helps. -->

## 🚀 Deployment Notes

<!-- Anything ops needs to know: env var changes, manual data fixes, cache flushes, downtime, feature flags. Write "None" if nothing. -->