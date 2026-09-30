# Database migrations

`001_initial.sql` is the authoritative idempotent initial schema. Application and CLI startup apply it and record schema_version 1. Back up with the SQLite backup API before any future migration. A restored database must be reconciled with opt-outs received after the backup before resuming; never replace a newer suppression history with an older snapshot.
