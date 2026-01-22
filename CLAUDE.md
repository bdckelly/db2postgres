# PS82-DB2-to-Postgres Migration Tool

## Project Overview

This tool migrates a full PeopleSoft 8.2 database from DB2 z/OS to PostgreSQL for archival purposes. It performs a one-time snapshot extraction with dynamic parallelism that adapts to available system resources.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Orchestrator (main.py)                            │
│  - Reads PSRECDEFN to build work queue                                      │
│  - Monitors system resources (CPU, memory, network)                         │
│  - Dynamically adjusts worker pool size                                     │
│  - Tracks progress and handles restarts                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
            ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
            │  Worker 1   │   │  Worker 2   │   │  Worker N   │
            │  (Process)  │   │  (Process)  │   │  (Process)  │
            └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
                   │                 │                 │
                   ▼                 ▼                 ▼
            ┌─────────────────────────────────────────────────┐
            │              DB2 z/OS (ibm_db)                  │
            │              Direct Connection                   │
            └─────────────────────────────────────────────────┘
                                      │
                                      ▼
            ┌─────────────────────────────────────────────────┐
            │           Staging (CSV or Binary)               │
            │           /data/staging/{table}/                │
            └─────────────────────────────────────────────────┘
                                      │
                                      ▼
            ┌─────────────────────────────────────────────────┐
            │              PostgreSQL (COPY)                  │
            │              Bulk Load                          │
            └─────────────────────────────────────────────────┘
```

## Directory Structure

```
ps82-to-postgres/
├── CLAUDE.md                 # This file
├── README.md                 # User documentation
├── pyproject.toml            # Project dependencies
├── config/
│   ├── settings.py           # Configuration management
│   ├── db2_connection.py     # DB2 z/OS connection settings
│   └── postgres_connection.py
├── src/
│   ├── __init__.py
│   ├── orchestrator.py       # Main coordination logic
│   ├── worker.py             # Extraction worker process
│   ├── resource_monitor.py   # Dynamic parallelism controller
│   ├── schema/
│   │   ├── __init__.py
│   │   ├── extractor.py      # Pull DDL from PSRECDEFN/PSRECFIELD/PSDBFIELD
│   │   ├── converter.py      # DB2 -> PostgreSQL type mapping
│   │   └── generator.py      # Generate CREATE TABLE statements
│   ├── extraction/
│   │   ├── __init__.py
│   │   ├── chunker.py        # Large table partitioning strategies
│   │   ├── cursor.py         # DB2 cursor management with HOLD
│   │   └── serializer.py     # Data serialization for staging
│   ├── loading/
│   │   ├── __init__.py
│   │   ├── bulk_loader.py    # PostgreSQL COPY operations
│   │   └── validator.py      # Row count and checksum validation
│   └── utils/
│       ├── __init__.py
│       ├── ps_types.py       # PeopleSoft-specific type handling
│       ├── progress.py       # Progress tracking and restart support
│       └── logging_config.py
├── data/
│   ├── staging/              # Intermediate CSV/binary files
│   └── checkpoints/          # Restart checkpoint files
├── logs/
│   └── migration.log
└── tests/
    ├── __init__.py
    ├── test_schema_extractor.py
    ├── test_type_converter.py
    └── test_chunker.py
```

## Key Design Decisions

### Dynamic Parallelism

The resource monitor adjusts worker count based on:
- **CPU utilization**: Target 70-80% on the migration host
- **Memory pressure**: Back off when available memory drops below threshold
- **DB2 response time**: Detect mainframe saturation via query latency
- **PostgreSQL COPY throughput**: Identify target bottlenecks

```python
# resource_monitor.py concept
class ResourceMonitor:
    def __init__(self, min_workers=2, max_workers=20):
        self.min_workers = min_workers
        self.max_workers = max_workers
        self.current_workers = min_workers
    
    def recommended_workers(self) -> int:
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        
        if cpu > 85 or mem > 85:
            return max(self.min_workers, self.current_workers - 2)
        elif cpu < 60 and mem < 70:
            return min(self.max_workers, self.current_workers + 1)
        return self.current_workers
```

### Table Processing Strategy

Tables are prioritized by size category:
1. **Tiny** (< 1,000 rows): Process in batches, single worker
2. **Small** (< 100,000 rows): Single worker, full extraction
3. **Medium** (< 10,000,000 rows): Single worker with progress checkpoints
4. **Large** (>= 10,000,000 rows): Chunked by key ranges, multiple workers

### Chunking for Large Tables

```python
# chunker.py concept
def generate_chunks(table_name: str, key_columns: list[str], chunk_size: int = 500000):
    """
    For tables with EFFDT: chunk by date ranges
    For tables with numeric keys: chunk by key ranges
    For tables without good keys: use ROWID equivalent or offset/limit
    """
```

### Data Type Mappings

| PeopleSoft/DB2 | PostgreSQL | Notes |
|----------------|------------|-------|
| CHAR(n) | CHAR(n) or VARCHAR(n) | Consider VARCHAR for space |
| VARCHAR(n) | VARCHAR(n) | Direct mapping |
| DECIMAL(p,s) | NUMERIC(p,s) | Direct mapping |
| INTEGER | INTEGER | Direct mapping |
| SMALLINT | SMALLINT | Direct mapping |
| DATE | DATE | Watch for 1900-01-01 nulls |
| TIMESTAMP | TIMESTAMP | Direct mapping |
| CLOB | TEXT | PeopleSoft PSLONGCHAR |
| BLOB | BYTEA | PeopleSoft PSIMAGE |

### PeopleSoft-Specific Handling

- **EFFDT null convention**: DB2 stores `1900-01-01` for null effective dates; decide whether to preserve or convert to NULL
- **Subrecords**: Flattened at extraction; verify PSRECFIELD handles correctly
- **Long names**: PS 8.2 on DB2 z/OS uses truncated names; map via PSRECDEFN.SQLTABLENAME
- **XLAT values**: Extract PS_XLATTABLE for reference data integrity

### Restart and Recovery

Checkpoints are saved per-table with:
- Table name
- Last successfully processed key value (for chunked tables)
- Row count extracted
- Timestamp

```python
# checkpoints/PS_VOUCHER.json
{
    "table": "PS_VOUCHER",
    "status": "in_progress",
    "last_key": {"BUSINESS_UNIT": "US001", "VOUCHER_ID": "00123456"},
    "rows_extracted": 2500000,
    "rows_loaded": 2500000,
    "updated_at": "2025-01-22T10:30:00Z"
}
```

## Configuration

### Environment Variables

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

# Parallelism Bounds
MIN_WORKERS=2
MAX_WORKERS=20
CHUNK_SIZE=500000

# Paths
STAGING_DIR=/data/staging
CHECKPOINT_DIR=/data/checkpoints
LOG_DIR=/logs
```

### settings.py Structure

```python
from pydantic_settings import BaseSettings

class DB2Settings(BaseSettings):
    database: str
    hostname: str
    port: int = 446
    uid: str
    pwd: str
    
    class Config:
        env_prefix = "DB2_"

class PostgresSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    database: str
    user: str
    password: str
    
    class Config:
        env_prefix = "PG_"

class MigrationSettings(BaseSettings):
    min_workers: int = 2
    max_workers: int = 20
    chunk_size: int = 500000
    staging_dir: str = "/data/staging"
    checkpoint_dir: str = "/data/checkpoints"
```

## Dependencies

```toml
[project]
name = "ps82-to-postgres"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "ibm-db>=3.2.0",
    "psycopg[binary]>=3.1.0",
    "pydantic-settings>=2.0.0",
    "psutil>=5.9.0",
    "rich>=13.0.0",        # Progress display
    "structlog>=23.0.0",   # Structured logging
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "ruff>=0.1.0",
]
```

## Usage

### Phase 1: Schema Extraction and Conversion

```bash
# Extract schema from PeopleSoft catalog tables
python -m src.schema.extractor --output schema/extracted/

# Convert to PostgreSQL DDL
python -m src.schema.converter --input schema/extracted/ --output schema/postgres/

# Review and apply DDL to target
psql -h localhost -d ps82_archive -f schema/postgres/all_tables.sql
```

### Phase 2: Data Migration

```bash
# Full migration with dynamic parallelism
python -m src.orchestrator

# Resume from checkpoint after interruption
python -m src.orchestrator --resume

# Migrate specific tables only
python -m src.orchestrator --tables PS_VOUCHER,PS_VCHR_LINE,PS_PAYMENT_TBL
```

### Phase 3: Validation

```bash
# Validate row counts
python -m src.loading.validator --mode counts

# Validate checksums (slower, samples large tables)
python -m src.loading.validator --mode checksums --sample-rate 0.01
```

## Development Guidelines

### Code Style

- Use type hints throughout
- Format with `ruff format`
- Lint with `ruff check`
- Target Python 3.11+

### Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_type_converter.py -v
```

### Logging

Use structlog for consistent, parseable logs:

```python
import structlog

log = structlog.get_logger()

log.info("table_extraction_started", table="PS_VOUCHER", estimated_rows=5000000)
log.info("chunk_completed", table="PS_VOUCHER", chunk=3, rows=500000, elapsed_seconds=45.2)
log.error("extraction_failed", table="PS_VOUCHER", error=str(e), chunk=3)
```

### Error Handling

- Retry transient DB2 connection errors (3 attempts with exponential backoff)
- Log and skip tables with persistent errors; continue migration
- Save checkpoint before any long operation
- Provide clear error context for debugging

## Known PeopleSoft 8.2 Quirks

1. **PSIMAGE fields**: May contain application-specific binary formats; extract as raw bytes
2. **PSLONGCHAR**: Can exceed 32KB; ensure TEXT type on PostgreSQL side
3. **Effective-dated tables**: EFFDT + EFFSEQ logic must be preserved exactly
4. **Tree structures**: PS_TREE_NODE and related tables have self-referential keys
5. **Translate values**: FIELDVALUE in PS_XLATTABLE may have trailing spaces

## Performance Tuning

### DB2 Side
- Work with mainframe team to allow sufficient parallel sessions
- Use `WITH UR` (uncommitted read) for extraction queries
- Consider off-peak hours for large table extraction

### PostgreSQL Side
- Disable indexes during bulk load, rebuild after
- Increase `maintenance_work_mem` for index creation
- Use `COPY ... WITH (FREEZE)` if loading into empty tables
- Consider unlogged tables for staging, convert after validation

### Network
- Monitor throughput; DB2 Connect over WAN can bottleneck
- Consider compression if bandwidth-limited
- Batch staging files if latency is high

## Monitoring

The orchestrator provides real-time progress via Rich console:

```
┌─────────────────────────────────────────────────────────────────────┐
│ PS82 to PostgreSQL Migration                                        │
├─────────────────────────────────────────────────────────────────────┤
│ Progress: 234/1,847 tables (12.7%)                                  │
│ Workers: 8 active (target: 10, CPU: 72%, Mem: 45%)                 │
│ Current: PS_VOUCHER (chunk 5/12), PS_JRNL_HEADER, PS_AP_PAYMENT... │
│ Rate: 125,000 rows/sec | ETA: 4h 23m                               │
└─────────────────────────────────────────────────────────────────────┘
```

## Contacts and Resources

- **PeopleSoft Documentation**: Oracle PeopleBooks (8.2 vintage)
- **DB2 z/OS**: IBM Knowledge Center
- **PostgreSQL COPY**: https://www.postgresql.org/docs/current/sql-copy.html
- **ibm_db**: https://github.com/ibmdb/python-ibmdb
