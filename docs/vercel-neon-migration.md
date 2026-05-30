# Vercel + Neon Migration Runbook

Use this runbook to migrate the production Estate Management app from Render to Vercel hosting with Neon Postgres.

Do not delete the Render app or Render Postgres until the Vercel deployment has been verified and a rollback path has been tested.

## Architecture

- Hosting: Vercel Python runtime serving Django through `estate_project/wsgi.py`.
- Database: Neon Postgres.
- Static files: Django `collectstatic` plus WhiteNoise.
- Production source of truth before cutover: Render Postgres.

## Required Environment Variables

Set these in Render before merging this branch if they are missing:

```text
DATABASE_URL=<render-postgres-url>
SECRET_KEY=<existing-or-new-production-secret>
DEBUG=False
ALLOWED_HOSTS=<render-domain>,<custom-domain>
CSRF_TRUSTED_ORIGINS=https://<render-domain>,https://<custom-domain>
SECURE_SSL_REDIRECT=True
```

Set these in Vercel for the real migration:

```text
DATABASE_URL=<neon-app-connection-url>
SECRET_KEY=<same-production-secret-or-a-planned-new-secret>
DEBUG=False
ALLOWED_HOSTS=.vercel.app,<vercel-domain>,<custom-domain>
CSRF_TRUSTED_ORIGINS=https://*.vercel.app,https://<vercel-domain>,https://<custom-domain>
SECURE_SSL_REDIRECT=True
```

Generate a secret key:

```bash
python - <<'PY'
import secrets
print(secrets.token_urlsafe(50))
PY
```

## Database Backup And Restore

Use the direct/unpooled Neon connection string for restores. The pooled URL can be used by the app after restore.

Create the final Render backup during a maintenance window:

```bash
pg_dump --format=custom --no-owner --no-acl --dbname "$RENDER_DATABASE_URL" --file render_final_backup.dump
```

Restore into Neon:

```bash
export DATABASE_URL='<neon-direct-unpooled-url>'
scripts/restore_render_backup.sh render_final_backup.dump
python manage.py migrate
python manage.py showmigrations --plan
python manage.py check
python manage.py check --deploy
```

## Validation Queries

Run against Neon and compare with Render counts:

```sql
select 'auth_user' as table_name, count(*) from auth_user
union all select 'estate_property', count(*) from estate_property
union all select 'estate_tenant', count(*) from estate_tenant
union all select 'estate_tenantrent', count(*) from estate_tenantrent
union all select 'estate_rentpayment', count(*) from estate_rentpayment
union all select 'estate_expense', count(*) from estate_expense
union all select 'estate_employee', count(*) from estate_employee
union all select 'estate_employeesalary', count(*) from estate_employeesalary
union all select 'estate_commissionrate', count(*) from estate_commissionrate
union all select 'estate_otherincome', count(*) from estate_otherincome
order by table_name;
```

## Smoke Tests

- Login and logout.
- Forced password-change flow.
- Dashboard totals.
- Tenants list, details, add, edit, and toggle active.
- Add rent payment.
- Payment history and CSV export.
- Add expense and view expenses ledger.
- Add employee, pay salary, and toggle active.
- Django admin and password reset action.
- Static CSS, logo, and favicon loading.
- Form submissions without CSRF errors.

## Cutover

1. Stop writes to Render during the final backup window.
2. Take the final Render backup.
3. Restore into Neon and run migrations/checks.
4. Deploy Vercel using the Neon database.
5. Verify smoke tests.
6. Point the custom domain to Vercel.
7. Keep Render available for rollback until Vercel has been stable.

## Rollback

- If cutover fails before new writes happen on Vercel, point DNS back to Render.
- If writes happened on Vercel, export and reconcile those rows before rolling back.
- Do not delete Render services until the migration has been stable for at least one billing cycle.
