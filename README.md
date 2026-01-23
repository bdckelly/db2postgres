# PS82-DB2-to-Postgres Migration Tool

A Python-based migration tool for transferring PeopleSoft 8.2 databases from DB2 z/OS to PostgreSQL. Features dynamic parallelism, checkpoint/restart capability, and comprehensive data validation.

## Features

- **Dynamic Parallelism**: Automatically adjusts worker count based on CPU, memory, and database load
- **Checkpoint/Restart**: Resume interrupted migrations without reprocessing completed tables
- **Chunked Extraction**: Handles large tables (10M+ rows) by partitioning into manageable chunks
- **Schema Conversion**: Automatically converts DB2 DDL to PostgreSQL with type mappings
- **Data Validation**: Verifies row counts and checksums to ensure data integrity
- **Structured Logging**: JSON or console output with full context for troubleshooting
- **PeopleSoft-Aware**: Handles EFFDT null conventions, subrecords, and other PS quirks

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│           Orchestrator (Main Process)                   │
│  - Builds work queue from PSRECDEFN                     │
│  - Manages worker pool (2-20 processes)                 │
│  - Monitors resources (CPU, memory, DB latency)         │
│  - Tracks progress and checkpoints                      │
└────────────────────┬────────────────────────────────────┘
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
    ┌────────┐  ┌────────┐  ┌────────┐
    │Worker 1│  │Worker 2│  │Worker N│
    └────┬───┘  └────┬───┘  └────┬───┘
         │           │           │
         └───────────┼───────────┘
                     ▼
         ┌───────────────────────┐
         │  DB2 z/OS (Source)    │
         └───────────┬───────────┘
                     ▼
         ┌───────────────────────┐
         │  Staging (CSV files)  │
         └───────────┬───────────┘
                     ▼
         ┌───────────────────────┐
         │  PostgreSQL (Target)  │
         └───────────────────────┘
```

## Prerequisites

- Python 3.11 or higher
- Access to DB2 z/OS database (PeopleSoft 8.2)
- PostgreSQL 12 or higher (target database)
- DB2 client libraries (for ibm_db)
- Sufficient disk space for staging files

## Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd db2postgres
```

### 2. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -e .
```

For development with testing tools:

```bash
pip install -e ".[dev]"
```

### 4. Install DB2 client libraries

The `ibm_db` package requires DB2 client libraries. Follow the [ibm_db installation guide](https://github.com/ibmdb/python-ibmdb#installation) for your platform.

On Linux:
```bash
# Download and extract IBM Data Server Driver Package
# Set environment variables
export IBM_DB_HOME=/path/to/dsdriver
export LD_LIBRARY_PATH=$IBM_DB_HOME/lib:$LD_LIBRARY_PATH
```

On Windows:
- Install IBM Data Server Driver Package
- Add `<install_dir>\bin` to PATH

### 5. Configure environment variables

Copy the example environment file and edit with your credentials:

```bash
cp .env.example .env
# Edit .env with your database credentials and settings
```

Required environment variables:

```bash
# DB2 z/OS Connection
DB2_DATABASE=your_db
DB2_HOSTNAME=mainframe.example.com
DB2_PORT=446
DB2_UID=your_user
DB2_PWD=your_password

# PostgreSQL Connection
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=ps82_archive
PG_USER=postgres
PG_PASSWORD=your_password

# Migration Settings
MIN_WORKERS=2
MAX_WORKERS=20
CHUNK_SIZE=500000
```

## Quick Start

For a complete step-by-step guide, see [QUICKSTART.md](QUICKSTART.md).

**Minimal example** (with mock databases for testing):

```bash
# 1. Install and configure
pip install -e .
cp .env.example .env
# Edit .env with your settings

# 2. Extract and convert schema
python -m src.schema --full

# 3. Run migration
python -m src --tables PS_XLATTABLE  # Start with small table
```

## Usage

### Phase 1: Schema Extraction and Conversion

Extract PeopleSoft schema from DB2 catalog tables and convert to PostgreSQL DDL:

```bash
# Option 1: All-in-one command (extract + convert)
python -m src.schema full --output schema/postgres/

# Option 2: Step-by-step
# Extract schema from PeopleSoft catalog
python -m src.schema extract --output schema/extracted/

# Convert to PostgreSQL DDL
python -m src.schema convert --input schema/extracted/ --output schema/postgres/

# Review generated DDL
cat schema/postgres/all_tables.sql
cat schema/postgres/tables/PS_VOUCHER.sql

# Apply DDL to PostgreSQL
psql -h localhost -d ps82_archive -f schema/postgres/all_tables.sql
```

**Example output** from schema extraction:

```
Extracting PeopleSoft Schema
========================================
Connected to DB2: PROD_PS82
Querying PSRECDEFN...
Found 1,847 tables

Extracting table definitions:
[1/1847] PS_XLATTABLE (500 rows estimated)
[2/1847] PS_INSTALLATION (1 row estimated)
[3/1847] PS_VOUCHER (5,000,000 rows estimated)
...

Schema extracted to: schema/extracted/
Converting to PostgreSQL DDL...
Generated: schema/postgres/all_tables.sql (1,847 tables)
Generated: schema/postgres/all_indexes.sql (4,523 indexes)
```

### Phase 2: Data Migration

Run the full migration with dynamic parallelism:

```bash
# Full migration (all tables)
python -m src

# Migrate specific tables only
python -m src --tables PS_VOUCHER,PS_VCHR_LINE,PS_PAYMENT_TBL

# Resume from checkpoint after interruption
python -m src --resume

# Fixed worker count (disable dynamic parallelism)
python -m src --min-workers 5 --max-workers 5
```

Progress display during migration:

```
┌─────────────────────────────────────────────────────────┐
│ PS82 to PostgreSQL Migration                            │
├─────────────────────────────────────────────────────────┤
│ Progress: 234/1,847 tables (12.7%)                      │
│ Workers: 8 active                                       │
│ CPU: 72.5%                                              │
│ Memory: 45.2%                                           │
│ Total Rows: 125,430,500                                 │
└─────────────────────────────────────────────────────────┘

[10:15:23] table_completed table=PS_VOUCHER rows=5000000 duration=182.5s worker=3
[10:16:45] table_completed table=PS_VCHR_LINE rows=15000000 duration=425.2s worker=5
[10:17:12] chunk_completed table=PS_JRNL_LN chunk=8/15 rows=500000 worker=2
```

### Phase 3: Validation

Validate data integrity after migration:

```bash
# Fast validation: row count comparison
python -m src.loading validate --mode counts

# Thorough validation: sample-based checksums
python -m src.loading validate --mode checksums --sample-rate 0.01

# Validate specific tables
python -m src.loading validate --mode counts --tables PS_VOUCHER,PS_VCHR_LINE
```

**Example validation output:**

```
Validating Migration
========================================
Mode: counts
Tables: 1,847

[✓] PS_XLATTABLE: 500 rows (DB2: 500, PG: 500)
[✓] PS_VOUCHER: 5,000,000 rows (DB2: 5,000,000, PG: 5,000,000)
[✗] PS_JRNL_LN: MISMATCH (DB2: 25,000,000, PG: 24,999,950)
[✓] PS_VCHR_LINE: 15,000,000 rows (DB2: 15,000,000, PG: 15,000,000)

Summary:
  Total: 1,847 tables
  Passed: 1,846 (99.9%)
  Failed: 1 (0.1%)

Check logs/migration.log for details on failures.
```

## Example Scenarios

### Scenario 1: Migrate a Small Table

Test the tool with a small translate table:

```bash
# 1. Extract schema for specific table
python -m src.schema extract --tables PS_XLATTABLE

# 2. Apply DDL
psql -h localhost -d ps82_archive -f schema/postgres/tables/PS_XLATTABLE.sql

# 3. Migrate data
python -m src --tables PS_XLATTABLE

# Expected output:
# Building work queue...
# Total tables: 1
# Starting migration with 2 workers...
# [10:30:15] table_completed table=PS_XLATTABLE rows=500 duration=0.8s worker=0
# Migration complete: 1/1 tables (100%)

# 4. Validate
python -m src.loading validate --mode counts --tables PS_XLATTABLE

# Expected output:
# [✓] PS_XLATTABLE: 500 rows (DB2: 500, PG: 500)
```

### Scenario 2: Migrate a Large Chunked Table

Migrate a multi-million row table with chunking:

```bash
# 1. Check table size first
python -c "
from config.settings import Settings
from src.schema.extractor import SchemaExtractor

settings = Settings()
extractor = SchemaExtractor(settings.db2)
table_def = extractor.extract_table_definition('JRNL_LN')
print(f'Estimated rows: {table_def.estimated_rows:,}')
print(f'Key fields: {[f.field_name for f in table_def.fields if f.is_key]}')
"

# Expected output:
# Estimated rows: 25,000,000
# Key fields: ['BUSINESS_UNIT', 'JOURNAL_ID', 'JOURNAL_LINE']

# 2. Run migration (will auto-chunk based on size)
python -m src --tables PS_JRNL_LN

# Expected output with chunking:
# [10:35:20] chunking_strategy_determined table=PS_JRNL_LN chunks=50 strategy=NumericRangeChunkStrategy
# [10:35:22] chunk_completed table=PS_JRNL_LN chunk_id=0 chunk_rows=500000 total_rows=500000
# [10:35:55] chunk_completed table=PS_JRNL_LN chunk_id=1 chunk_rows=500000 total_rows=1000000
# ...

# 3. Check staging files (one per chunk)
ls -lh data/staging/PS_JRNL_LN/

# Expected:
# PS_JRNL_LN_chunk_000.csv  (42M)
# PS_JRNL_LN_chunk_001.csv  (42M)
# ...

# 4. Check checkpoint
cat data/checkpoints/PS_JRNL_LN.json

# Expected:
# {
#   "table": "PS_JRNL_LN",
#   "status": "completed",
#   "rows_extracted": 25000000,
#   "chunk_id": 49,
#   "completed_at": "2025-01-22T10:45:30Z"
# }
```

### Scenario 3: Resume After Interruption

Simulate interruption and resume:

```bash
# 1. Start migration
python -m src

# 2. Interrupt with Ctrl+C after a few tables complete
# Expected output:
# [yellow]Shutdown requested. Finishing current tasks...[/yellow]
# Shutting down workers...
# Migration Summary:
#   Completed: 15/1847 tables
#   Failed: 0

# 3. Check what was completed
ls data/checkpoints/ | grep -c "json"
# Expected: 15

cat data/checkpoints/PS_VOUCHER.json
# {
#   "table": "PS_VOUCHER",
#   "status": "completed",
#   ...
# }

# 4. Resume from checkpoint
python -m src --resume

# Expected output:
# Loading checkpoints...
# Found 15 completed tables, 0 in progress, 0 failed
# Filtered 15 completed tables
# Remaining tables: 1,832
# Starting migration with 2 workers...
```

### Scenario 4: Handle Failed Tables

What to do when tables fail:

```bash
# 1. Run migration
python -m src

# 2. Check for failures in summary
# Expected output:
# Migration Summary:
#   Completed: 1,845/1847 tables
#   Failed: 2
#
# Failed Tables:
#   - PS_PROBLEM_TABLE: Connection timeout after 3 retries
#   - PS_ANOTHER_TABLE: Invalid data format in BLOB field

# 3. Check failed checkpoints
cat data/checkpoints/PS_PROBLEM_TABLE.json

# {
#   "table": "PS_PROBLEM_TABLE",
#   "status": "failed",
#   "error": "Connection timeout after 3 retries",
#   "rows_extracted": 250000,
#   ...
# }

# 4. Retry failed tables only
python -m src --tables PS_PROBLEM_TABLE,PS_ANOTHER_TABLE

# 5. Or reset and retry
rm data/checkpoints/PS_PROBLEM_TABLE.json
python -m src --tables PS_PROBLEM_TABLE
```

### Scenario 5: Monitor Resource Usage

Watch resource adaptation in action:

```bash
# Terminal 1: Run migration with verbose logging
LOG_LEVEL=DEBUG python -m src

# Terminal 2: Monitor system resources
watch -n 1 'ps aux | grep python; free -h; mpstat 1 1'

# Expected log output showing adaptation:
# [10:50:00] metrics_collected cpu=45.2 memory=38.5
# [10:50:00] resources_available_increasing current_workers=2 recommended=3
# [10:50:00] workers_spawned count=1 total_workers=3
# ...
# [10:52:30] metrics_collected cpu=88.5 memory=72.1
# [10:52:30] resource_overload_backing_off current_workers=8 recommended=6
# [10:52:30] workers_terminated count=2 remaining_workers=6
```

## Reading Logs and Checkpoints

### Log Files

Logs are written to `logs/migration.log` with structured JSON:

```bash
# View all logs
tail -f logs/migration.log

# Filter for specific table
grep 'PS_VOUCHER' logs/migration.log | jq '.'

# Find all errors
grep '"level":"error"' logs/migration.log | jq '.error'

# Check worker performance
grep 'table_completed' logs/migration.log | jq '{table: .table, rows: .rows, duration: .duration, worker: .worker}'
```

**Example log entries:**

```json
{"timestamp": "2025-01-22T10:15:23Z", "level": "info", "event": "table_completed", "table": "PS_VOUCHER", "rows": 5000000, "duration": 182.5, "worker": 3}
{"timestamp": "2025-01-22T10:16:01Z", "level": "error", "event": "table_failed", "table": "PS_BAD_TABLE", "error": "Connection timeout", "worker": 5}
{"timestamp": "2025-01-22T10:16:15Z", "level": "info", "event": "worker_count_updated", "old_count": 6, "new_count": 8}
```

### Checkpoint Files

Each table has a checkpoint file in `data/checkpoints/`:

```bash
# List all checkpoints
ls data/checkpoints/

# Check specific table status
cat data/checkpoints/PS_VOUCHER.json | jq '{table, status, rows: .rows_extracted}'

# Count by status
for status in completed in_progress failed pending; do
  count=$(grep "\"status\": \"$status\"" data/checkpoints/*.json 2>/dev/null | wc -l)
  echo "$status: $count"
done

# Find incomplete migrations
grep -l '"status": "in_progress"' data/checkpoints/*.json
```

### Staging Files

CSV staging files are in `data/staging/{table}/`:

```bash
# Check staging for specific table
ls -lh data/staging/PS_VOUCHER/

# View first few rows of CSV
head -20 data/staging/PS_VOUCHER/PS_VOUCHER.csv

# Count rows in CSV file
wc -l data/staging/PS_VOUCHER/PS_VOUCHER.csv

# Check for NULL values
grep '\\N' data/staging/PS_VOUCHER/PS_VOUCHER.csv | head
```

## Advanced Usage

### Custom Chunking

Override automatic chunking strategy:

```python
# In src/extraction/chunker.py, modify determine_chunking_strategy()
# Or set custom chunk size for specific tables

# Example: Force date-based chunking for audit tables
python -m src --tables PS_AUDIT_TBL --chunk-size 100000
```

### Selective Migration

Migrate tables by pattern:

```bash
# All voucher-related tables
python -m src --tables $(python -c "
from src.schema.extractor import SchemaExtractor
from config.settings import Settings
extractor = SchemaExtractor(Settings().db2)
tables = [t for t in extractor.extract_table_list() if 'VCHR' in t or 'VOUCHER' in t]
print(','.join(tables))
")
```

### Performance Profiling

Enable detailed performance tracking:

```bash
# Run with profiling
LOG_LEVEL=DEBUG python -m src --tables PS_LARGE_TABLE > profile.log 2>&1

# Analyze timing
grep 'duration' profile.log | jq '{table: .table, duration: .duration}' | sort -k2 -n

# Check resource usage over time
grep 'metrics_collected' profile.log | jq '{time: .timestamp, cpu: .cpu, memory: .memory}'
```

### Parallel Schema Extraction

Speed up schema extraction for large databases:

```python
# Extract schema with multiple workers
# Create custom script: scripts/parallel_schema_extract.py

from concurrent.futures import ProcessPoolExecutor
from src.schema.extractor import SchemaExtractor

def extract_table(table_name):
    extractor = SchemaExtractor(settings.db2)
    return extractor.extract_table_definition(table_name)

with ProcessPoolExecutor(max_workers=10) as executor:
    table_defs = list(executor.map(extract_table, table_list))
```

### Custom Validation Rules

Add domain-specific validation:

```python
# Create scripts/custom_validation.py

from src.loading.validator import DataValidator

class CustomValidator(DataValidator):
    def validate_business_rules(self, table: str):
        """Custom PeopleSoft business rule validation."""
        if 'VOUCHER' in table:
            # Check GROSS_AMT > 0
            query = "SELECT COUNT(*) FROM {table} WHERE GROSS_AMT <= 0"
            # Run and report
```

### Batch Processing

Process tables in logical groups:

```bash
#!/bin/bash
# scripts/batch_migrate.sh

# Group 1: Reference tables (small, fast)
python -m src --tables PS_XLATTABLE,PS_INSTALLATION,PS_COUNTRY_TBL
echo "Reference tables complete"

# Group 2: Master data (medium)
python -m src --tables PS_VENDOR,PS_CUSTOMER,PS_ITEM
echo "Master data complete"

# Group 3: Transactional (large, chunked)
python -m src --tables PS_VOUCHER,PS_VCHR_LINE,PS_JRNL_LN
echo "Transactional data complete"

# Validate each group
python -m src.loading validate --mode counts
```

### Integration with CI/CD

Example GitHub Actions workflow:

```yaml
# .github/workflows/migration.yml
name: DB2 to PostgreSQL Migration

on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly at 2 AM Sunday

jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -e .

      - name: Run migration
        env:
          DB2_DATABASE: ${{ secrets.DB2_DATABASE }}
          DB2_HOSTNAME: ${{ secrets.DB2_HOSTNAME }}
          DB2_UID: ${{ secrets.DB2_UID }}
          DB2_PWD: ${{ secrets.DB2_PWD }}
        run: python -m src

      - name: Validate
        run: python -m src.loading validate --mode counts

      - name: Upload logs
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: migration-logs
          path: logs/
```

## Configuration

### Parallelism Settings

- `MIN_WORKERS`: Minimum worker processes (default: 2)
- `MAX_WORKERS`: Maximum worker processes (default: 20)
- `CHUNK_SIZE`: Rows per chunk for large tables (default: 500,000)

The orchestrator dynamically adjusts worker count based on:
- CPU utilization (target 70-80%)
- Memory pressure (back off at 85%)
- DB2 response time (detect mainframe saturation)
- PostgreSQL throughput (detect target bottleneck)

### Chunking Strategy

Tables are processed based on size:
- **Tiny** (< 1,000 rows): Single extraction, no chunking
- **Small** (< 100,000 rows): Single extraction with checkpoint
- **Medium** (< 10M rows): Checkpointed extraction
- **Large** (>= 10M rows): Chunked by key ranges (EFFDT, numeric IDs, or offset)

### Logging Configuration

Control logging via environment variables:

```bash
# Log level
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL

# Log format
LOG_FORMAT=console  # console (colored) or json (production)
```

Logs are written to:
- Console (stdout)
- File: `logs/migration.log`

## Checkpoints and Recovery

Checkpoints are saved to `data/checkpoints/` as JSON files:

```json
{
  "table": "PS_VOUCHER",
  "status": "in_progress",
  "rows_extracted": 2500000,
  "rows_loaded": 2500000,
  "chunk_id": 5,
  "last_key": {"BUSINESS_UNIT": "US001", "VOUCHER_ID": "00123456"},
  "started_at": "2025-01-22T10:00:00Z",
  "updated_at": "2025-01-22T10:30:00Z"
}
```

To resume an interrupted migration:

```bash
python -m src.orchestrator --resume
```

This will:
1. Load all checkpoints
2. Skip completed tables
3. Resume in-progress tables from last chunk
4. Retry failed tables

## PeopleSoft-Specific Handling

### EFFDT Null Convention

PeopleSoft uses `1900-01-01` to represent null effective dates. The tool:
- Detects EFFDT fields automatically
- Preserves the date value by default
- Can optionally convert to NULL (configure in converter)

### Subrecords

PeopleSoft subrecords are flattened during extraction. The schema extractor handles this automatically via PSRECFIELD.

### Long Names

PeopleSoft 8.2 on DB2 z/OS uses truncated table names. The tool maps via PSRECDEFN.SQLTABLENAME to get full names.

### Special Field Types

- `PSIMAGE` (BLOB) → PostgreSQL `BYTEA`
- `PSLONGCHAR` (CLOB) → PostgreSQL `TEXT`

## Performance Tuning

### DB2 Side

- Work with mainframe team to allow sufficient parallel sessions
- Use off-peak hours for large table extraction
- Monitor DB2 connection pool limits

### PostgreSQL Side

Before bulk loading:
```sql
-- Drop indexes (rebuild after load)
-- Increase work memory
SET maintenance_work_mem = '2GB';
SET work_mem = '256MB';
```

After bulk loading:
```sql
-- Rebuild indexes
-- Analyze tables
ANALYZE;
```

### Staging Directory

Ensure sufficient disk space:
- Estimate: ~2x size of largest table
- Use fast local storage (SSD preferred)
- Clean staging files after successful load

## Troubleshooting

### Connection Issues

**DB2 connection fails:**
- Verify DB2 client libraries installed
- Check environment variables (IBM_DB_HOME, LD_LIBRARY_PATH)
- Test with: `python -c "import ibm_db; print('OK')"`
- Verify network connectivity to mainframe
- Check credentials and permissions

**PostgreSQL connection fails:**
- Verify psycopg3 installed: `pip list | grep psycopg`
- Test connection: `psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE`
- Check pg_hba.conf for access rules

### Performance Issues

**Slow extraction from DB2:**
- Reduce MAX_WORKERS to avoid overwhelming mainframe
- Check DB2 query response times in logs
- Consider off-peak hours
- Verify network throughput

**Slow loading to PostgreSQL:**
- Drop indexes before bulk load
- Increase PostgreSQL memory settings
- Check disk I/O on PostgreSQL server
- Consider UNLOGGED tables for initial load

### Data Validation Failures

**Row count mismatch:**
- Check logs for extraction errors
- Verify no concurrent changes to source
- Re-run migration for affected tables
- Check staging files for completeness

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov=config --cov-report=html

# Run specific test file
pytest tests/test_type_converter.py -v
```

### Code Style

The project uses Ruff for linting and formatting:

```bash
# Format code
ruff format .

# Lint code
ruff check .

# Fix auto-fixable issues
ruff check --fix .
```

### Adding New Features

1. Create feature branch
2. Implement with type hints
3. Add unit tests (>80% coverage)
4. Update documentation
5. Run tests and linting
6. Submit pull request

## Project Structure

```
db2postgres/
├── config/              # Configuration management
│   ├── settings.py
│   ├── db2_connection.py
│   └── postgres_connection.py
├── src/
│   ├── orchestrator.py  # Main coordinator
│   ├── worker.py        # Worker processes
│   ├── resource_monitor.py  # Dynamic parallelism
│   ├── schema/          # Schema extraction and conversion
│   ├── extraction/      # Data extraction from DB2
│   ├── loading/         # Bulk loading to PostgreSQL
│   └── utils/           # Utilities and helpers
├── data/
│   ├── staging/         # CSV staging files
│   └── checkpoints/     # Restart checkpoints
├── logs/                # Application logs
├── schema/              # Generated DDL
└── tests/               # Unit and integration tests
```

## License

[Your license here]

## Support

For issues and questions:
- Check [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for design details
- Review logs in `logs/migration.log`
- Open an issue on GitHub

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request
