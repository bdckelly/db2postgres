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

## Usage

### Phase 1: Schema Extraction and Conversion

Extract PeopleSoft schema from DB2 catalog tables and convert to PostgreSQL DDL:

```bash
# Extract schema from PeopleSoft catalog
python -m src.schema.extractor --output schema/extracted/

# Convert to PostgreSQL DDL
python -m src.schema.converter --input schema/extracted/ --output schema/postgres/

# Review generated DDL
cat schema/postgres/all_tables.sql

# Apply DDL to PostgreSQL
psql -h localhost -d ps82_archive -f schema/postgres/all_tables.sql
```

### Phase 2: Data Migration

Run the full migration with dynamic parallelism:

```bash
# Full migration (all tables)
python -m src.orchestrator

# Migrate specific tables only
python -m src.orchestrator --tables PS_VOUCHER,PS_VCHR_LINE,PS_PAYMENT_TBL

# Resume from checkpoint after interruption
python -m src.orchestrator --resume
```

Progress display during migration:

```
┌─────────────────────────────────────────────────────────┐
│ PS82 to PostgreSQL Migration                            │
├─────────────────────────────────────────────────────────┤
│ Progress: 234/1,847 tables (12.7%)                      │
│ Workers: 8 active (target: 10, CPU: 72%, Mem: 45%)     │
│ Current: PS_VOUCHER (chunk 5/12), PS_JRNL_HEADER...    │
│ Rate: 125,000 rows/sec | ETA: 4h 23m                   │
└─────────────────────────────────────────────────────────┘
```

### Phase 3: Validation

Validate data integrity after migration:

```bash
# Fast validation: row count comparison
python -m src.loading.validator --mode counts

# Thorough validation: sample-based checksums
python -m src.loading.validator --mode checksums --sample-rate 0.01

# Validate specific tables
python -m src.loading.validator --mode counts --tables PS_VOUCHER,PS_VCHR_LINE
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
