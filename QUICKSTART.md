# Quick Start Guide

Get up and running with PS82-to-Postgres migration in under 15 minutes.

## Prerequisites Checklist

Before starting, ensure you have:

- [ ] Python 3.11 or 3.12 installed (Python 3.13 has compatibility issues with ibm_db)
- [ ] Access to DB2 z/OS database (or use mock mode for testing)
- [ ] PostgreSQL 12+ running and accessible
- [ ] At least 10 GB free disk space for staging

> **Note:** The `ibm_db` package includes a bundled DB2 CLI driver - no separate installation required.

## 5-Minute Setup

### Step 1: Clone and Install (2 minutes)

```bash
# Clone repository
git clone <repository-url>
cd db2postgres

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Linux/Mac:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -e .
```

### Step 2: Configure Environment (2 minutes)

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your settings
# Use your favorite editor: nano, vim, code, etc.
nano .env
```

**Minimal configuration** for testing with mocks:

```bash
# .env
DB2_DATABASE=TEST_DB
DB2_HOSTNAME=localhost
DB2_PORT=50000
DB2_UID=testuser
DB2_PWD=testpass
DB2_USE_MOCK=true

PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=ps82_test
PG_USER=postgres
PG_PASSWORD=your_password
PG_USE_MOCK=true

MIN_WORKERS=2
MAX_WORKERS=5
CHUNK_SIZE=10000
```

**Production configuration** (with real databases):

```bash
# .env
DB2_DATABASE=PROD_PS82
DB2_HOSTNAME=mainframe.company.com
DB2_PORT=446
DB2_UID=migration_user
DB2_PWD=secure_password
DB2_USE_MOCK=false

PG_HOST=postgres-server.company.com
PG_PORT=5432
PG_DATABASE=ps82_archive
PG_USER=migration_user
PG_PASSWORD=secure_password
PG_USE_MOCK=false

MIN_WORKERS=2
MAX_WORKERS=20
CHUNK_SIZE=500000
```

### Step 3: Verify Installation (1 minute)

```bash
# Test Python imports
python -c "
from config.settings import Settings
from src.schema.extractor import SchemaExtractor
print('✓ All imports successful')
print('✓ Installation verified')
"

# Check configuration
python -c "
from config.settings import Settings
settings = Settings()
print(f'DB2: {settings.db2.hostname}:{settings.db2.port}/{settings.db2.database}')
print(f'PostgreSQL: {settings.postgres.host}:{settings.postgres.port}/{settings.postgres.database}')
print(f'Workers: {settings.migration.min_workers}-{settings.migration.max_workers}')
"
```

Expected output:
```
✓ All imports successful
✓ Installation verified
DB2: localhost:50000/TEST_DB
PostgreSQL: localhost:5432/ps82_test
Workers: 2-5
```

## 10-Minute Test Migration

Follow these steps to run your first test migration.

### Step 1: Prepare PostgreSQL Database

```bash
# Create test database
createdb ps82_test

# Or using psql:
psql -U postgres -c "CREATE DATABASE ps82_test;"

# Verify connection
psql -h localhost -d ps82_test -c "\l"
```

### Step 2: Extract Schema (Mock Mode)

For testing without DB2 access:

```bash
# The tool includes sample schema for testing
# Extract and convert in one step
python -m src.schema --full

# Check generated DDL
ls -l schema/postgres/
cat schema/postgres/tables/PS_XLATTABLE.sql
```

Expected output:
```
Extracting PeopleSoft Schema
========================================
Using mock DB2 connection
Found 5 sample tables

Extracting table definitions:
[1/5] PS_XLATTABLE (500 rows estimated)
[2/5] PS_INSTALLATION (1 row estimated)
[3/5] PS_VOUCHER (5,000,000 rows estimated)
[4/5] PS_JOB (2,000,000 rows estimated)
[5/5] PS_IMAGE (10,000 rows estimated)

Schema extracted to: schema/extracted/

Converting to PostgreSQL DDL...
Generated: schema/postgres/all_tables.sql
Generated: schema/postgres/all_indexes.sql
✓ Schema conversion complete
```

### Step 3: Apply DDL to PostgreSQL

```bash
# Apply all tables
psql -h localhost -d ps82_test -f schema/postgres/all_tables.sql

# Verify tables created
psql -h localhost -d ps82_test -c "\dt"
```

Expected output:
```
CREATE TABLE
CREATE TABLE
CREATE TABLE
...

       List of relations
 Schema |     Name      | Type  |  Owner
--------+---------------+-------+----------
 public | ps_xlattable  | table | postgres
 public | ps_voucher    | table | postgres
 public | ps_job        | table | postgres
 ...
```

### Step 4: Run Test Migration

Start with a small table:

```bash
# Migrate single small table
python -m src --tables PS_XLATTABLE

# Watch the progress display
```

Expected output:
```
Building work queue...
Extracting table definition: PS_XLATTABLE
Total tables: 1

Starting migration with 2 workers...

┌─────────────────────────────────────────────────┐
│ PS82 to PostgreSQL Migration                    │
├─────────────────────────────────────────────────┤
│ Progress: 0/1 tables (0.0%)                     │
│ Workers: 2 active                               │
│ CPU: 15.2%                                      │
│ Memory: 25.4%                                   │
│ Total Rows: 0                                   │
└─────────────────────────────────────────────────┘

[12:34:56] table_completed table=PS_XLATTABLE rows=500 duration=0.8s worker=0

Shutting down workers...

========================================================================
MIGRATION SUMMARY
========================================================================
Total Tables: 1
Completed: 1
Total Rows Extracted: 500

Performance Summary:
  CPU: 15.2%
  Memory: 25.4%
  Workers: 2
========================================================================
```

### Step 5: Validate Migration

```bash
# Verify row count matches
python -m src.loading validate --mode counts --tables PS_XLATTABLE
```

Expected output:
```
Validating Migration
========================================
Mode: counts
Tables: 1

[✓] PS_XLATTABLE: 500 rows (DB2: 500, PG: 500)

Summary:
  Total: 1 tables
  Passed: 1 (100%)
  Failed: 0 (0%)
```

### Step 6: Check Data in PostgreSQL

```bash
# View migrated data
psql -h localhost -d ps82_test -c "
SELECT * FROM ps_xlattable LIMIT 5;
"

# Check row count
psql -h localhost -d ps82_test -c "
SELECT COUNT(*) FROM ps_xlattable;
"
```

**Congratulations!** You've successfully completed a test migration. 🎉

## Next Steps

### Test with More Tables

```bash
# Migrate multiple small tables
python -m src --tables PS_XLATTABLE,PS_INSTALLATION

# Try a larger table with chunking
python -m src --tables PS_VOUCHER

# Run full migration
python -m src
```

### Test Resume Functionality

```bash
# Start migration
python -m src --tables PS_VOUCHER,PS_JOB,PS_IMAGE

# Press Ctrl+C to interrupt

# Resume from checkpoint
python -m src --resume
```

### Explore Features

```bash
# Check logs
tail -f logs/migration.log

# View checkpoints
ls data/checkpoints/
cat data/checkpoints/PS_VOUCHER.json

# View staging files
ls -lh data/staging/PS_VOUCHER/
```

## Production Migration Checklist

Before running in production:

### Pre-Migration

- [ ] Backup DB2 source database
- [ ] Create empty PostgreSQL database
- [ ] Test connectivity to both databases
- [ ] Estimate total migration time
- [ ] Schedule maintenance window if needed
- [ ] Disable application access to target DB
- [ ] Set up monitoring/alerting

### Configuration

- [ ] Set `DB2_USE_MOCK=false`
- [ ] Set `PG_USE_MOCK=false`
- [ ] Adjust `MAX_WORKERS` based on hardware
- [ ] Set `CHUNK_SIZE` based on table sizes
- [ ] Configure `LOG_LEVEL=INFO` (not DEBUG)
- [ ] Set `LOG_FORMAT=json` for production

### Migration

- [ ] Run schema extraction: `python -m src.schema --full`
- [ ] Review and apply DDL to PostgreSQL
- [ ] Test with small tables first
- [ ] Run full migration: `python -m src`
- [ ] Monitor progress and resource usage
- [ ] Save logs and checkpoints

### Post-Migration

- [ ] Run validation: `python -m src.loading validate --mode counts`
- [ ] Spot-check data manually
- [ ] Create indexes: `psql -f schema/postgres/all_indexes.sql`
- [ ] Run ANALYZE on all tables
- [ ] Test application queries
- [ ] Compare performance metrics
- [ ] Archive staging files
- [ ] Document any issues

## Common Issues and Solutions

### Issue: "No module named 'ibm_db'"

**Solution:**
```bash
# Install ibm_db (includes bundled DB2 CLI driver)
pip install ibm-db

# Or use mock mode for testing:
export DB2_USE_MOCK=true
```

### Issue: "DLL load failed while importing ibm_db" (Windows)

**Solution:**
This usually indicates a Python version compatibility issue.

```bash
# Check Python version - use 3.11 or 3.12, NOT 3.13
python --version

# If using Python 3.13, switch to 3.12:
# Using pyenv-win:
pyenv install 3.12.9
pyenv local 3.12.9
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .

# Verify driver loads correctly
python -c "from config.db2_connection import HAS_IBM_DB; print('ibm_db available:', HAS_IBM_DB)"
```

The project automatically configures DLL paths for the bundled driver on Windows.

### Issue: "Connection refused" to PostgreSQL

**Solution:**
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Check pg_hba.conf allows connections
sudo nano /etc/postgresql/14/main/pg_hba.conf

# Add line:
# host    all    all    0.0.0.0/0    md5

# Restart PostgreSQL
sudo systemctl restart postgresql
```

### Issue: "Permission denied" when creating directories

**Solution:**
```bash
# Create directories manually
mkdir -p data/staging data/checkpoints logs schema/extracted schema/postgres

# Or run with appropriate permissions
sudo chown -R $USER:$USER data/ logs/ schema/
```

### Issue: Migration is very slow

**Solutions:**
```bash
# 1. Increase worker count
export MAX_WORKERS=10

# 2. Increase chunk size for large tables
export CHUNK_SIZE=1000000

# 3. Drop indexes before load, rebuild after
# (automatically done by bulk loader)

# 4. Check DB2 network latency
ping mainframe.company.com

# 5. Monitor resource usage
htop  # Check CPU/memory
iotop  # Check disk I/O
```

### Issue: "Row count mismatch" in validation

**Solution:**
```bash
# 1. Check logs for errors during extraction
grep 'PS_PROBLEM_TABLE' logs/migration.log | grep error

# 2. Check if concurrent changes were made to source
# (Migration assumes stable source)

# 3. Re-run migration for specific table
rm data/checkpoints/PS_PROBLEM_TABLE.json
rm -rf data/staging/PS_PROBLEM_TABLE/
python -m src --tables PS_PROBLEM_TABLE

# 4. Compare source and target manually
psql -h localhost -d ps82_test -c "
SELECT MIN(id), MAX(id), COUNT(*) FROM ps_problem_table;
"
```

## Getting Help

If you encounter issues:

1. **Check logs**: `tail -100 logs/migration.log`
2. **Enable debug logging**: `export LOG_LEVEL=DEBUG`
3. **Review documentation**: [README.md](README.md)
4. **Check implementation plan**: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
5. **Open an issue**: Include logs, configuration, and error messages

## Performance Tips

### For Small Databases (< 100 tables, < 10 GB)

```bash
# Simple configuration
MIN_WORKERS=2
MAX_WORKERS=5
CHUNK_SIZE=100000

# Disable dynamic parallelism
python -m src --min-workers 3 --max-workers 3
```

### For Medium Databases (100-1000 tables, 10-100 GB)

```bash
# Balanced configuration
MIN_WORKERS=5
MAX_WORKERS=15
CHUNK_SIZE=500000

# Let dynamic parallelism optimize
python -m src
```

### For Large Databases (1000+ tables, 100+ GB)

```bash
# Aggressive configuration
MIN_WORKERS=10
MAX_WORKERS=20
CHUNK_SIZE=1000000

# Run during off-peak hours
# Consider batch processing by table groups
python -m src --tables $(cat table_groups/group_1.txt)
```

## Monitoring During Migration

### Terminal 1: Run migration with live display

```bash
python -m src
```

### Terminal 2: Monitor logs

```bash
tail -f logs/migration.log | grep -E 'table_completed|chunk_completed|error'
```

### Terminal 3: Monitor system resources

```bash
watch -n 2 '
echo "=== CPU and Memory ==="
ps aux | grep python | grep -v grep
echo ""
echo "=== Disk Usage ==="
df -h | grep -E "Filesystem|/data"
echo ""
echo "=== Network ==="
netstat -tn | grep -E "mainframe|postgres" | wc -l
'
```

### Terminal 4: Monitor PostgreSQL

```bash
watch -n 5 'psql -h localhost -d ps82_test -c "
SELECT
    schemaname,
    COUNT(*) as table_count,
    SUM(n_live_tup) as total_rows
FROM pg_stat_user_tables
GROUP BY schemaname;
"'
```

## Success Criteria

Your migration is successful when:

- ✅ All tables migrated (check summary)
- ✅ Row counts match (validation passes)
- ✅ No errors in logs
- ✅ Indexes rebuilt successfully
- ✅ Application queries work correctly
- ✅ Performance meets expectations

## What's Next?

- Explore [Advanced Usage](README.md#advanced-usage)
- Set up [CI/CD integration](README.md#integration-with-cicd)
- Customize [chunking strategies](README.md#custom-chunking)
- Add [custom validation rules](README.md#custom-validation-rules)

---

**Need help?** Open an issue or check the full [README](README.md) for detailed documentation.
