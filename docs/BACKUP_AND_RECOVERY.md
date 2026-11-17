# QuizVerse Live — Backup, Recovery & Data Protection Runbook

## 1. Automated Database Backups

### PostgreSQL Backups (`pg_dump`)
Run the backup script manually or via a scheduled cron/Cloud Scheduler job:
```bash
python scripts/backup_database.py --dir /var/backups/quizverse
```
Or directly using standard `pg_dump`:
```bash
PGPASSWORD="$DB_PASSWORD" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d quizverse \
    -F c \
    -b \
    -v \
    -f "/var/backups/quizverse/backup_$(date +%Y%m%d_%H%M%S).dump"
```

### Cloud SQL Automated Backups
On Google Cloud SQL, automated daily backups with point-in-time recovery (PITR) should be enabled:
```bash
gcloud sql instances patch quizverse-pg-instance \
    --backup-start-time 03:00 \
    --enable-bin-log
```

---

## 2. Restoration Procedure

### Restoring from a Custom Dump (`.dump` file)
1. Stop application traffic (set service to maintenance or drain traffic).
2. Create or verify a clean target database:
   ```bash
   PGPASSWORD="$DB_PASSWORD" dropdb -h "$DB_HOST" -U "$DB_USER" quizverse_restored
   PGPASSWORD="$DB_PASSWORD" createdb -h "$DB_HOST" -U "$DB_USER" quizverse_restored
   ```
3. Restore using `pg_restore`:
   ```bash
   PGPASSWORD="$DB_PASSWORD" pg_restore \
       -h "$DB_HOST" \
       -p "$DB_PORT" \
       -U "$DB_USER" \
       -d quizverse_restored \
       -v \
       /var/backups/quizverse/backup_YYYYMMDD_HHMMSS.dump
   ```
4. Verify table integrity:
   ```bash
   python -c "from quizverse.persistence.db import Database; db = Database('postgresql+psycopg://user:pass@host:5432/quizverse_restored'); assert db.ping(); print('RESTORE VERIFIED OK')"
   ```
5. Point `DATABASE_URL` to the restored database and restart the application service.

---

## 3. Migration Rollback Procedure

To roll back the most recent database migration revision:
```bash
alembic downgrade -1
```
To verify the rollback on a separate test instance before applying to production:
```bash
python scripts/verify_alembic_isolated.py
```

---

## 4. Secret Rotation Procedure

1. **Database Password Rotation**:
   - Generate a new strong database password.
   - Update user password in PostgreSQL: `ALTER USER postgres WITH PASSWORD 'NEW_PASSWORD';`.
   - Update Secret Manager: `echo -n "NEW_DATABASE_URL" | gcloud secrets versions add quizverse-database-url --data-file=-`.
   - Cloud Run redeploys automatically with the latest secret version.
2. **AI Provider Key Rotation**:
   - Update Secret Manager: `echo -n "NEW_GEMINI_KEY" | gcloud secrets versions add quizverse-gemini-api-key --data-file=-`.
