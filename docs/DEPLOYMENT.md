# Deployment Guide

**Version**: 1.0.0
**Last Updated**: 2025-12-26
**Audience**: Developers and operators

---

## Overview

This guide covers deployment strategies for the Personal Agent system, supporting both local development and cloud production environments. The system is designed to start simple (single-user, local) and scale to multi-user cloud deployment with **zero code changes**.

---

## Deployment Modes

### Local Development (Recommended for Start)

**Use Case**: Single-user personal agent, rapid iteration, zero cost

**Infrastructure**:
- PostgreSQL 16 on localhost
- Application running on local machine
- Environment variables in `.env` file

**Benefits**:
- ✅ Zero hosting costs
- ✅ Fast iteration (no deployment pipeline)
- ✅ Easy debugging (direct database access)
- ✅ Simple setup (no cloud accounts needed)

**Performance**:
- GET operations: ~5-10ms (well under production targets)
- SET operations: ~10-20ms (well under production targets)
- Throughput: 50-100 req/sec (sufficient for single user)

---

### Cloud Production (Multi-User Ready)

**Use Case**: Multiple users, production-grade reliability, scaling

**Infrastructure**:
- Managed PostgreSQL (AWS RDS, Google Cloud SQL, Supabase)
- Application deployed on cloud platform (AWS ECS, Google Cloud Run, Railway)
- Secrets managed via cloud secret manager

**Benefits**:
- ✅ 99.9% availability (managed database)
- ✅ Automatic backups and point-in-time recovery
- ✅ Scales to multiple users
- ✅ Professional secret management

**Performance** (Production Targets):
- GET operations: < 50ms (p95)
- SET operations: < 100ms (p95)
- Throughput: 1000 req/sec per user

---

## Local Development Setup

### Prerequisites

```bash
# Install PostgreSQL 16
brew install postgresql@16  # macOS
# or
sudo apt install postgresql-16  # Linux

# Install Python 3.11+
brew install python@3.11  # macOS
# or
sudo apt install python3.11  # Linux
```

### Database Setup

```bash
# Start PostgreSQL
brew services start postgresql@16  # macOS
# or
sudo systemctl start postgresql  # Linux

# Create database
createdb personal_agent

# Create user (optional, for isolation)
psql -c "CREATE USER agent_user WITH PASSWORD 'local_dev_password';"
psql -c "GRANT ALL PRIVILEGES ON DATABASE personal_agent TO agent_user;"
```

### Environment Configuration

Create `.env` file in project root:

```bash
# Database
DATABASE_URL=postgresql://localhost:5432/personal_agent
# or with custom user:
# DATABASE_URL=postgresql://agent_user:local_dev_password@localhost:5432/personal_agent

# Encryption
ENCRYPTION_KEY=<generate-with-command-below>

# Auth (development)
JWT_SECRET=<generate-with-command-below>

# Optional: Logging
LOG_LEVEL=DEBUG
```

**Generate Encryption Key**:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start application
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Verify Setup**:
```bash
curl http://localhost:8000/health
# Expected: {"status": "ok", "database": "connected"}
```

---

## Cloud Production Setup

### Option 1: AWS Deployment

#### Database (RDS PostgreSQL)

```bash
# Via AWS Console or CLI
aws rds create-db-instance \
  --db-instance-identifier personal-agent-db \
  --db-instance-class db.t4g.micro \
  --engine postgres \
  --engine-version 16.1 \
  --master-username admin \
  --master-user-password <secure-password> \
  --allocated-storage 20 \
  --backup-retention-period 7 \
  --vpc-security-group-ids <sg-id>
```

**Estimated Cost**: ~$15-20/month (db.t4g.micro)

#### Application (ECS Fargate)

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### Secrets (AWS Secrets Manager)

```bash
# Store encryption key
aws secretsmanager create-secret \
  --name personal-agent/encryption-key \
  --secret-string "<encryption-key>"

# Store database URL
aws secretsmanager create-secret \
  --name personal-agent/database-url \
  --secret-string "postgresql://admin:password@rds-endpoint:5432/personal_agent"
```

**ECS Task Definition** (reference secrets):
```json
{
  "containerDefinitions": [{
    "name": "personal-agent",
    "secrets": [
      {
        "name": "ENCRYPTION_KEY",
        "valueFrom": "arn:aws:secretsmanager:region:account:secret:personal-agent/encryption-key"
      },
      {
        "name": "DATABASE_URL",
        "valueFrom": "arn:aws:secretsmanager:region:account:secret:personal-agent/database-url"
      }
    ]
  }]
}
```

---

### Option 2: Supabase + Railway (Easiest)

#### Database (Supabase - Free Tier Available)

1. Create project at [supabase.com](https://supabase.com)
2. Get connection string from Settings → Database
3. Enable connection pooling (recommended)

**Connection String**:
```
postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
```

#### Application (Railway)

1. Connect GitHub repository to [Railway](https://railway.app)
2. Add environment variables:
   ```
   DATABASE_URL=<supabase-connection-string>
   ENCRYPTION_KEY=<generated-key>
   ```
3. Deploy automatically on git push

**Estimated Cost**: Free (Supabase free tier) + $5/month (Railway)

---

### Option 3: Google Cloud

#### Database (Cloud SQL)

```bash
gcloud sql instances create personal-agent-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --root-password=<secure-password>
```

#### Application (Cloud Run)

```bash
# Build and deploy
gcloud run deploy personal-agent \
  --source . \
  --region us-central1 \
  --set-env-vars DATABASE_URL=<cloud-sql-url> \
  --set-secrets ENCRYPTION_KEY=personal-agent-encryption-key:latest
```

**Estimated Cost**: ~$10-15/month (db-f1-micro + Cloud Run)

---

## Migration: Local → Cloud

### Zero Code Changes Required

The system uses environment variables for all configuration, so migration only requires updating `.env`:

**Before (Local)**:
```bash
DATABASE_URL=postgresql://localhost:5432/personal_agent
ENCRYPTION_KEY=local_dev_key_abc123
```

**After (Cloud)**:
```bash
DATABASE_URL=postgresql://user:pass@cloud-db.region.provider.com:5432/personal_agent
ENCRYPTION_KEY=<from-secret-manager>
```

### Migration Steps

1. **Export local data**:
   ```bash
   pg_dump personal_agent > backup.sql
   ```

2. **Import to cloud database**:
   ```bash
   psql -h <cloud-db-host> -U <user> -d personal_agent < backup.sql
   ```

3. **Update environment variables** (via cloud platform):
   - Railway: Settings → Variables
   - AWS ECS: Task Definition → Environment
   - Supabase: Use Cloud SQL connection string

4. **Deploy application** to cloud platform

5. **Verify**:
   ```bash
   curl https://<your-cloud-url>/health
   ```

---

## Environment Variables Reference

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `DATABASE_URL` | ✅ | PostgreSQL connection string | `postgresql://localhost:5432/personal_agent` |
| `ENCRYPTION_KEY` | ✅ | AES-256 encryption key (base64) | `<32-byte-urlsafe-string>` |
| `JWT_SECRET` | ✅ | JWT signing secret | `<32-byte-urlsafe-string>` |
| `LOG_LEVEL` | ❌ | Logging verbosity | `INFO` (default), `DEBUG` |
| `PORT` | ❌ | Application port | `8000` (default) |
| `ALLOWED_ORIGINS` | ❌ | CORS origins (comma-separated) | `http://localhost:3000` |

---

## Database Backup & Recovery

### Local Development

**Manual Backup**:
```bash
pg_dump personal_agent > backup_$(date +%Y%m%d).sql
```

**Restore**:
```bash
psql personal_agent < backup_20251226.sql
```

### Cloud Production

#### AWS RDS
- **Automated**: Daily snapshots with 7-day retention (configured in RDS settings)
- **Point-in-Time Recovery**: Restore to any second within retention window
- **Manual Snapshot**:
  ```bash
  aws rds create-db-snapshot \
    --db-instance-identifier personal-agent-db \
    --db-snapshot-identifier manual-backup-20251226
  ```

#### Supabase
- **Automated**: Daily backups included in paid tiers
- **Manual**: Use `pg_dump` via connection string

#### Google Cloud SQL
- **Automated**: Daily backups with 7-day retention
- **Point-in-Time Recovery**: Available with binary logging enabled

---

## Security Best Practices

### Local Development
- ✅ Use `.env` file (gitignored)
- ✅ Generate strong random keys
- ✅ Use localhost-only database access
- ❌ Never commit secrets to git

### Cloud Production
- ✅ Use cloud secret manager (AWS Secrets Manager, Google Secret Manager)
- ✅ Enable database SSL/TLS connections
- ✅ Use VPC/private networking for database access
- ✅ Rotate encryption keys periodically (if multi-user)
- ✅ Enable database connection pooling
- ✅ Use managed identity/service accounts (avoid hardcoded credentials)

---

## Performance Tuning

### Local Development
- **Connection Pool**: 5 connections (sufficient for single user)
- **PostgreSQL Settings**: Default settings are adequate
- **No optimization needed**: Performance will exceed targets

### Cloud Production
- **Connection Pool**: 10-20 connections (adjust based on load)
- **PostgreSQL Settings** (for managed databases):
  ```sql
  -- Increase shared_buffers for better caching
  ALTER SYSTEM SET shared_buffers = '256MB';

  -- Enable parallel queries
  ALTER SYSTEM SET max_parallel_workers_per_gather = 2;
  ```
- **Enable Query Logging**: Monitor slow queries (> 100ms)
- **Connection Pooling**: Use PgBouncer for high-concurrency scenarios

---

## Monitoring & Observability

### Local Development
- **Logs**: Console output (stdout)
- **Health Check**: `curl http://localhost:8000/health`
- **Database**: Direct `psql` access for debugging

### Cloud Production
- **Application Logs**:
  - AWS: CloudWatch Logs
  - Google Cloud: Cloud Logging
  - Railway: Built-in log viewer

- **Database Metrics**:
  - AWS RDS: CloudWatch metrics (CPU, connections, latency)
  - Supabase: Dashboard metrics
  - Google Cloud SQL: Cloud Monitoring

- **Alerts** (Recommended):
  - Database CPU > 80%
  - Connection pool exhaustion
  - API latency > 500ms (p95)
  - Error rate > 1%

---

## Cost Estimation

### Local Development
- **Total**: $0/month

### Cloud Production (Small Scale)

| Component | Provider | Tier | Cost/Month |
|-----------|----------|------|------------|
| Database | Supabase | Free | $0 |
| Application | Railway | Starter | $5 |
| **Total** | | | **$5** |

### Cloud Production (Production Grade)

| Component | Provider | Tier | Cost/Month |
|-----------|----------|------|------------|
| Database | AWS RDS | db.t4g.micro | $15 |
| Application | AWS Fargate | 0.25 vCPU | $10 |
| Secret Manager | AWS | 2 secrets | $1 |
| **Total** | | | **$26** |

---

## Troubleshooting

### Local: "Database connection failed"
```bash
# Check PostgreSQL is running
brew services list | grep postgresql

# Check database exists
psql -l | grep personal_agent

# Verify connection string
echo $DATABASE_URL
```

### Cloud: "Connection timeout"
- Verify security group allows inbound traffic on port 5432
- Check VPC networking (app and database in same VPC)
- Verify connection string (host, port, credentials)

### "Encryption key invalid"
- Ensure `ENCRYPTION_KEY` is base64-encoded 32-byte string
- Regenerate if lost (WARNING: existing encrypted data will be unreadable)

---

## References

- [PostgreSQL 16 Documentation](https://www.postgresql.org/docs/16/)
- [SQLAlchemy Async Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [AWS RDS Best Practices](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_BestPractices.html)
- [Supabase Documentation](https://supabase.com/docs)
- [Railway Documentation](https://docs.railway.app/)

---

**Version**: 1.0.0
**Status**: Active
**Applies to**: All components requiring database/secrets configuration
