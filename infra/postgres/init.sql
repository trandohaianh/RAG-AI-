-- Medallion warehouse cho RAG DE pipeline
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS bronze.raw_documents (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    filename VARCHAR(512) NOT NULL,
    file_path TEXT NOT NULL,
    file_size_bytes BIGINT,
    content_hash VARCHAR(64) NOT NULL,
    raw_text_length INTEGER,
    source_type VARCHAR(20),
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    dag_run_id VARCHAR(128),
    UNIQUE (user_id, filename, content_hash)
);

CREATE TABLE IF NOT EXISTS bronze.ingest_runs (
    id SERIAL PRIMARY KEY,
    dag_run_id VARCHAR(128),
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status VARCHAR(32),
    files_processed INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS silver.document_chunks (
    id SERIAL PRIMARY KEY,
    bronze_id INTEGER REFERENCES bronze.raw_documents(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    source VARCHAR(512) NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    char_count INTEGER NOT NULL,
    validated_at TIMESTAMPTZ,
    validation_passed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source, content_hash)
);

CREATE TABLE IF NOT EXISTS gold.document_summary (
    user_id INTEGER NOT NULL,
    source VARCHAR(512) NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    total_chars BIGINT NOT NULL DEFAULT 0,
    last_ingested_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, source)
);

CREATE TABLE IF NOT EXISTS gold.daily_ingest_stats (
    metric_date DATE PRIMARY KEY,
    files_processed INTEGER DEFAULT 0,
    chunks_created INTEGER DEFAULT 0,
    validation_failures INTEGER DEFAULT 0,
    users_active INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bronze_user ON bronze.raw_documents(user_id);
CREATE INDEX IF NOT EXISTS idx_silver_user_source ON silver.document_chunks(user_id, source);
