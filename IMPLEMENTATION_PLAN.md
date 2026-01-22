# PS82-DB2-to-Postgres Migration Tool - Implementation Plan

## Overview
Full implementation of a PeopleSoft 8.2 database migration tool from DB2 z/OS to PostgreSQL with dynamic parallelism, checkpoint/restart capability, and comprehensive validation.

## User Requirements
- **Database Access**: Mock-based initially, design for easy DB2 integration when available
- **Scope**: Full implementation with all features (dynamic parallelism, error handling, validation)
- **Testing Strategy**: Start with small tables, progressively test larger ones

## Implementation Phases

### Phase 1: Foundation & Project Setup (Days 1-3)

**Goal**: Establish project infrastructure, configuration, and utilities

**Files to Create**:
- `pyproject.toml` - Project dependencies (ibm-db, psycopg[binary], pydantic-settings, psutil, rich, structlog)
- `README.md` - User documentation and setup instructions
- `.gitignore` - Exclude venv, logs, staging data, checkpoints
- `.env.example` - Template for environment variables

**Configuration**:
- `config/settings.py` - Pydantic settings with DB2Settings, PostgresSettings, MigrationSettings
- `config/db2_connection.py` - DB2 connection configuration with ibm_db
- `config/postgres_connection.py` - PostgreSQL connection configuration with psycopg3

**Utilities**:
- `src/utils/logging_config.py` - Structured logging with structlog (JSON output, file rotation)
- `src/utils/ps_types.py` - PeopleSoft type constants and DB2→PostgreSQL type mappings
- `src/utils/progress.py` - Checkpoint management with atomic writes

**Key Design Decisions**:
- Use pydantic-settings for environment variable validation
- Structlog for structured, parseable logs
- Type hints throughout (Python 3.11+)
- Mock database connections initially, easy swap to real connections

**Testing**: Unit tests for configuration parsing, type mappings, checkpoint save/load

---

### Phase 2: Schema Extraction & Conversion (Days 4-7)

**Goal**: Extract PeopleSoft schema from catalog tables and convert to PostgreSQL DDL

**Files to Create**:
- `src/schema/__init__.py` - Package exports
- `src/schema/extractor.py` - Query PSRECDEFN, PSRECFIELD, PSDBFIELD
- `src/schema/converter.py` - DB2 to PostgreSQL type conversion logic
- `src/schema/generator.py` - Generate CREATE TABLE and CREATE INDEX statements
- `src/schema/__main__.py` - CLI interface for schema extraction

**Key Implementation Details**:

**extractor.py**:
```python
# Extract table definitions
SELECT RECNAME, SQLTABLENAME, RECDESCR FROM PSRECDEFN WHERE RECTYPE = 0

# Extract field definitions with order
SELECT RECNAME, FIELDNAME, USEEDIT, SUBRECORD FROM PSRECFIELD ORDER BY RECNAME, FIELDNUM

# Extract physical field types
SELECT FIELDNAME, FIELDTYPE, LENGTH, DECIMALPOS FROM PSDBFIELD
```

**converter.py** - Type mappings:
- CHAR(n) → VARCHAR(n)
- VARCHAR(n) → VARCHAR(n)
- DECIMAL(p,s) → NUMERIC(p,s)
- DATE → DATE (handle 1900-01-01 null convention)
- TIMESTAMP → TIMESTAMP
- CLOB/PSLONGCHAR → TEXT
- BLOB/PSIMAGE → BYTEA

**generator.py** - Output structure:
```
schema/postgres/
├── all_tables.sql
├── all_indexes.sql
├── tables/PS_VOUCHER.sql
└── indexes/PS_VOUCHER.sql
```

**Testing**:
- Unit tests with mock DB2 cursor returning sample PSRECDEFN data
- Verify generated DDL is valid PostgreSQL syntax
- Test edge cases (subrecords, long names, PeopleSoft special types)

---

### Phase 3: Data Extraction Engine (Days 8-12)

**Goal**: Extract data from DB2 with chunking, cursor management, and serialization

**Files to Create**:
- `src/extraction/__init__.py`
- `src/extraction/chunker.py` - Table partitioning strategies
- `src/extraction/cursor.py` - DB2 cursor management with HOLD and retry logic
- `src/extraction/serializer.py` - Convert DB2 rows to PostgreSQL COPY-compatible CSV

**chunker.py** - Implement multiple strategies:
```python
# Size categories
- Tiny (< 1,000 rows): No chunking
- Small (< 100,000 rows): No chunking, full extraction
- Medium (< 10M rows): Checkpointed extraction
- Large (>= 10M rows): Chunked by key ranges

# Chunking strategies
- DateChunkStrategy: For tables with EFFDT (chunk by date ranges)
- NumericRangeStrategy: For tables with integer keys (chunk by key ranges)
- OffsetLimitStrategy: Fallback for tables without good keys
```

**cursor.py** - DB2-specific handling:
```python
# Use WITH UR (uncommitted read) for snapshot consistency
# Use WITH HOLD to prevent cursor closure
# Retry logic: 3 attempts with exponential backoff
# Fetch in batches (10,000 rows) to manage memory
```

**serializer.py** - CSV format:
- PostgreSQL COPY-compatible (NULL as \N)
- UTF-8 encoding
- Proper escaping of quotes, newlines, special characters
- Streaming writes (never buffer entire table)
- Handle BLOBs separately as binary files

**progress.py** - Checkpoint structure:
```json
{
  "table": "PS_VOUCHER",
  "status": "in_progress",
  "last_key": {"BUSINESS_UNIT": "US001", "VOUCHER_ID": "00123456"},
  "rows_extracted": 2500000,
  "rows_loaded": 2500000,
  "chunk_id": 5,
  "started_at": "2025-01-22T10:00:00Z",
  "updated_at": "2025-01-22T10:30:00Z",
  "completed_at": null,
  "error": null
}
```

**Testing**:
- Unit tests with mock DB2 connections
- Test each chunking strategy with sample data
- Verify chunk boundaries don't overlap or miss rows
- Test CSV serialization round-trip
- Test checkpoint save/load and resume logic

---

### Phase 4: PostgreSQL Loading & Validation (Days 13-15)

**Goal**: Bulk load data using COPY and validate integrity

**Files to Create**:
- `src/loading/__init__.py`
- `src/loading/bulk_loader.py` - PostgreSQL COPY operations
- `src/loading/validator.py` - Row count and checksum validation

**bulk_loader.py** - COPY implementation:
```python
# Use psycopg3 COPY protocol
with cursor.copy(f"COPY {table} FROM STDIN WITH (FORMAT CSV, NULL '\\N')") as copy:
    for line in staging_file:
        copy.write(line)

# Optimization strategies:
- Drop indexes before load, rebuild after
- Use COPY ... WITH (FREEZE) for empty tables
- Increase maintenance_work_mem for session
- Transaction per table or per chunk
```

**validator.py** - Validation modes:
```python
# Fast mode: Row count comparison
SELECT COUNT(*) FROM db2_table WITH UR
SELECT COUNT(*) FROM postgres_table

# Thorough mode: Sample-based checksum
# Sample 1% of rows and compare checksums
SELECT md5(string_agg(col::text, '|' ORDER BY pk))
FROM (SELECT * FROM table TABLESAMPLE BERNOULLI(1)) sample
```

**Error Handling**:
- Save rejected rows to error files
- Log constraint violations
- Continue migration on table failures

**Testing**:
- Unit tests with small CSV files
- Test NULL handling, special characters
- Verify error scenarios (constraint violations)
- Test validation with known mismatches

---

### Phase 5: Resource Monitoring & Dynamic Parallelism (Days 16-18)

**Goal**: Monitor system resources and dynamically adjust worker pool

**Files to Create**:
- `src/resource_monitor.py` - System resource monitoring and control algorithm

**Key Metrics to Monitor**:
- CPU utilization (target 70-80%)
- Memory pressure (back off at 85%)
- DB2 query latency (detect mainframe saturation)
- PostgreSQL COPY throughput (detect target bottleneck)

**Control Algorithm**:
```python
def recommended_workers(self) -> int:
    metrics = self.collect_metrics()

    # Back off if overloaded
    if metrics.cpu > 85 or metrics.memory > 85:
        return max(self.min_workers, self.current_workers - 2)

    # Increase if resources available
    if metrics.cpu < 60 and metrics.memory < 70 and metrics.db2_latency < threshold:
        return min(self.max_workers, self.current_workers + 1)

    return self.current_workers
```

**Configuration**:
- MIN_WORKERS=2, MAX_WORKERS=20
- Adjustment interval: every 30 seconds
- Smooth adjustments to avoid thrashing

**Testing**:
- Unit tests for control logic with mocked metrics
- Verify bounds enforcement (min/max workers)
- Test edge cases (rapid resource changes)

---

### Phase 6: Orchestration & Worker Management (Days 19-22)

**Goal**: Coordinate workers, manage lifecycle, and provide progress visualization

**Files to Create**:
- `src/worker.py` - Extraction worker process implementation
- `src/orchestrator.py` - Main coordination logic
- `src/__main__.py` - CLI entry point

**worker.py** - Worker lifecycle:
```python
# Each worker is a multiprocessing.Process with:
1. Own DB2 connection
2. Receives tasks from queue
3. Determines chunking strategy
4. Extracts data with checkpointing
5. Serializes to staging
6. Reports completion to result queue
7. Handles errors gracefully
```

**orchestrator.py** - Core responsibilities:
```python
class Orchestrator:
    def build_work_queue(self):
        # Query PSRECDEFN for table list
        # Estimate table sizes
        # Prioritize: tiny tables first, then small, then medium/large in parallel

    def spawn_workers(self, count: int):
        # Create multiprocessing.Process instances
        # Each worker gets task_queue and result_queue

    def adjust_parallelism(self):
        # Query resource_monitor every 30 seconds
        # Dynamically add/remove workers

    def monitor_progress(self):
        # Collect results from result_queue
        # Update Rich progress display
        # Save checkpoints

    def resume_from_checkpoint(self):
        # Load checkpoint files
        # Rebuild work queue excluding completed tables
        # Resume in-progress tables from last checkpoint
```

**Progress Display** (using Rich):
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

**__main__.py** - CLI interface:
```bash
# Full migration
python -m src.orchestrator

# Resume from checkpoint
python -m src.orchestrator --resume

# Migrate specific tables
python -m src.orchestrator --tables PS_VOUCHER,PS_VCHR_LINE

# Schema extraction only
python -m src.schema.extractor --output schema/extracted/
python -m src.schema.converter --input schema/extracted/ --output schema/postgres/
```

**Testing**:
- Unit test work queue building
- Integration test with mock workers
- Test resume functionality
- Test graceful shutdown (SIGINT/SIGTERM)
- Test worker crash recovery

---

### Phase 7: Testing & Documentation (Days 23-28)

**Goal**: Comprehensive testing and production readiness

**Test Files to Create**:
- `tests/test_type_converter.py` - All type mappings
- `tests/test_chunker.py` - Chunking strategies
- `tests/test_serializer.py` - Data serialization
- `tests/test_checkpoint.py` - Checkpoint logic
- `tests/test_resource_monitor.py` - Control algorithm
- `tests/integration/test_schema_extraction.py` - End-to-end schema
- `tests/integration/test_data_migration.py` - End-to-end data (when DB2 available)

**Test Coverage Goals**:
- >80% code coverage
- All edge cases and error conditions
- PeopleSoft-specific quirks (EFFDT nulls, subrecords, long names)

**Performance Testing**:
- Benchmark extraction throughput
- Benchmark load throughput
- Test with various worker counts
- Profile CPU and memory usage

**Error Scenario Testing**:
- DB2 connection loss during extraction
- PostgreSQL connection loss during load
- Disk full in staging area
- Worker process crash
- Graceful shutdown
- Resume from checkpoint

**Documentation**:
- Update README.md with complete user guide
- Add docstrings to all public functions
- Include usage examples
- Document configuration options

---

## Directory Structure

```
d:\Development\db2postgres\
├── CLAUDE.md                          # Project specification (exists)
├── IMPLEMENTATION_PLAN.md             # This file
├── README.md                          # User documentation (to create)
├── pyproject.toml                     # Dependencies (to create)
├── .gitignore                         # Git ignore rules (to create)
├── .env.example                       # Environment template (to create)
├── config\
│   ├── __init__.py
│   ├── settings.py                    # Pydantic settings
│   ├── db2_connection.py              # DB2 config
│   └── postgres_connection.py         # PostgreSQL config
├── src\
│   ├── __init__.py
│   ├── __main__.py                    # CLI entry point
│   ├── orchestrator.py                # Main coordination
│   ├── worker.py                      # Worker process
│   ├── resource_monitor.py            # Dynamic parallelism
│   ├── schema\
│   │   ├── __init__.py
│   │   ├── __main__.py                # Schema CLI
│   │   ├── extractor.py               # Extract from PSRECDEFN
│   │   ├── converter.py               # Type conversion
│   │   └── generator.py               # DDL generation
│   ├── extraction\
│   │   ├── __init__.py
│   │   ├── chunker.py                 # Table partitioning
│   │   ├── cursor.py                  # DB2 cursor management
│   │   └── serializer.py              # CSV serialization
│   ├── loading\
│   │   ├── __init__.py
│   │   ├── bulk_loader.py             # PostgreSQL COPY
│   │   └── validator.py               # Data validation
│   └── utils\
│       ├── __init__.py
│       ├── ps_types.py                # PeopleSoft types
│       ├── progress.py                # Checkpoints
│       └── logging_config.py          # Structured logging
├── data\
│   ├── staging\                       # CSV files (gitignored)
│   └── checkpoints\                   # Restart state (gitignored)
├── logs\
│   └── migration.log                  # Logs (gitignored)
├── schema\
│   ├── extracted\                     # DB2 schema (gitignored)
│   └── postgres\                      # PostgreSQL DDL (gitignored)
└── tests\
    ├── __init__.py
    ├── test_type_converter.py
    ├── test_chunker.py
    ├── test_serializer.py
    ├── test_checkpoint.py
    ├── test_resource_monitor.py
    └── integration\
        ├── __init__.py
        ├── test_schema_extraction.py
        └── test_data_migration.py
```

---

## Critical Files (Backbone of System)

1. **config\settings.py** - All configuration and environment variable handling
2. **src\orchestrator.py** - Core coordination, work queue, worker management, progress tracking
3. **src\worker.py** - Extraction worker with own DB2 connection and error handling
4. **src\extraction\chunker.py** - Table partitioning strategies for memory management
5. **src\schema\extractor.py** - Schema extraction from PeopleSoft catalog tables

---

## Key Technical Decisions

### Parallelism
- **Multiprocessing** (not threading) for true parallelism and isolation
- **Dynamic worker pool**: 2-20 workers based on resource utilization
- **Queue-based task distribution**: multiprocessing.Queue for thread-safety

### Staging
- **CSV format** for text data (PostgreSQL COPY-compatible)
- **Separate binary files** for BLOBs to avoid encoding issues
- **Streaming writes** to avoid memory issues

### Checkpoints
- **Per-table granularity** with chunk tracking
- **Atomic writes** (write temp, then rename)
- **JSON format** for human readability

### Error Handling
- **Retry transient errors**: 3 attempts with exponential backoff
- **Log and continue**: Don't fail entire migration on single table error
- **Save rejected rows**: Track data quality issues

### Database Connections
- **Mock-based initially**: Easy to develop without DB2 access
- **Connection pooling**: Each worker has dedicated DB2 connection
- **WITH UR and WITH HOLD**: DB2 snapshot consistency

---

## Dependencies (pyproject.toml)

```toml
[project]
name = "ps82-to-postgres"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "ibm-db>=3.2.0",           # DB2 z/OS driver
    "psycopg[binary]>=3.1.0",  # PostgreSQL driver
    "pydantic-settings>=2.0.0", # Configuration management
    "psutil>=5.9.0",           # System resource monitoring
    "rich>=13.0.0",            # Progress display
    "structlog>=23.0.0",       # Structured logging
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "pytest-mock>=3.12.0",
    "ruff>=0.1.0",
]
```

---

## Environment Variables (.env.example)

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
STAGING_DIR=d:\Development\db2postgres\data\staging
CHECKPOINT_DIR=d:\Development\db2postgres\data\checkpoints
LOG_DIR=d:\Development\db2postgres\logs
```

---

## Risk Mitigation

### High-Risk Areas
1. **DB2 Connectivity**: Robust retry logic, WITH HOLD cursors, frequent checkpoints
2. **Memory Management**: Streaming data, chunking, backpressure monitoring
3. **PeopleSoft Quirks**: Extensive logging, data quality checks, thorough testing
4. **Checkpoint Consistency**: Atomic writes, checksum validation, extensive resume testing
5. **Dynamic Parallelism**: Start with fixed, add dynamic as enhancement

### Testing Strategy
- **Mock-based development**: All components testable without DB2 initially
- **Progressive testing**: Start with tiny tables, then small, medium, large
- **Integration testing**: When DB2 available, test end-to-end with real data
- **Performance testing**: Benchmark with realistic data volumes

---

## Verification Plan (End-to-End Testing)

### Phase 1: Schema Verification
```bash
# 1. Run schema extraction (with mock or real DB2)
python -m src.schema.extractor --output schema/extracted/

# 2. Convert to PostgreSQL DDL
python -m src.schema.converter --input schema/extracted/ --output schema/postgres/

# 3. Validate generated DDL
psql -h localhost -d ps82_archive -f schema/postgres/all_tables.sql --dry-run

# 4. Apply DDL to test database
psql -h localhost -d ps82_archive -f schema/postgres/all_tables.sql

# Expected: All tables created successfully
```

### Phase 2: Small Table Migration
```bash
# 1. Create test tables in PostgreSQL
# 2. Run migration on specific small tables
python -m src.orchestrator --tables PS_XLATTABLE,PS_INSTALLATION

# 3. Validate row counts
python -m src.loading.validator --mode counts --tables PS_XLATTABLE,PS_INSTALLATION

# Expected: 100% row count match
```

### Phase 3: Chunked Table Migration
```bash
# 1. Run migration on medium table
python -m src.orchestrator --tables PS_JRNL_LN

# 2. Verify checkpoints created
ls data/checkpoints/

# 3. Validate data
python -m src.loading.validator --mode counts --tables PS_JRNL_LN

# Expected: Multiple checkpoints, 100% row count match
```

### Phase 4: Resume Capability
```bash
# 1. Start migration
python -m src.orchestrator --tables PS_VOUCHER

# 2. Interrupt mid-migration (Ctrl+C)

# 3. Resume from checkpoint
python -m src.orchestrator --resume --tables PS_VOUCHER

# Expected: Resumes from last checkpoint, completes successfully
```

### Phase 5: Full Migration
```bash
# 1. Run full migration with dynamic parallelism
python -m src.orchestrator

# 2. Monitor progress (real-time display)
# 3. Validate all tables after completion
python -m src.loading.validator --mode counts

# Expected: All tables migrated, row counts match
```

### Phase 6: Performance Testing
```bash
# 1. Run with different worker counts
python -m src.orchestrator --min-workers 5 --max-workers 5  # Fixed 5 workers
python -m src.orchestrator --min-workers 10 --max-workers 10  # Fixed 10 workers

# 2. Compare throughput and completion times
# 3. Monitor CPU/memory usage with performance tools

# Expected: Identify optimal worker count for environment
```

---

## Implementation Order Recommendation

### Week 1: Foundation → Schema
1. Project setup, dependencies, configuration
2. Logging, utilities, type mappings
3. Schema extractor, converter, generator
4. Test schema extraction with mocks

### Week 2: Extraction Engine
5. Chunking strategies
6. DB2 cursor manager
7. Data serializer
8. Progress/checkpoint management
9. Test extraction components

### Week 3: Loading → Orchestration
10. Bulk loader implementation
11. Data validator
12. Worker process
13. Orchestrator core (fixed parallelism)
14. Progress display

### Week 4: Dynamic Features → Testing
15. Resource monitor
16. Integrate dynamic parallelism
17. CLI entry point
18. Integration testing (when DB2 available)
19. Performance testing
20. Error scenario testing

### Week 5: Polish
21. Documentation (README, docstrings)
22. Additional test coverage
23. Bug fixes and optimization
24. Production readiness validation

---

## Success Criteria

✅ Schema extraction works with mock/real DB2
✅ DDL generation produces valid PostgreSQL syntax
✅ Data extraction handles small, medium, and large tables
✅ Chunking prevents memory issues
✅ Bulk loading achieves high throughput
✅ Checkpoint/resume works correctly
✅ Dynamic parallelism adjusts to resources
✅ Validation confirms data integrity
✅ >80% test coverage
✅ Clear documentation and error messages
