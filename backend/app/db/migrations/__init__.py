"""Migrations package.

The first business migration (0001_initial_schema) creates the pgvector
extension and the full Task 2 schema. The vector extension is required by
pgvector/pgvector:pg16; on a local PostgreSQL without pgvector installed the
CREATE EXTENSION step cannot run and must be reported as NOT RUN rather than
faking the schema via SQLite.
"""
