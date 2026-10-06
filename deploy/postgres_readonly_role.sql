-- Create a login role that can only read the three application tables.
-- Run as the database owner after `policypilot seed --postgres <owner DSN>`.
-- Replace the placeholder password through your secret manager; never commit it.
CREATE ROLE policypilot_reader LOGIN PASSWORD :'reader_password';
ALTER ROLE policypilot_reader SET default_transaction_read_only = on;
ALTER ROLE policypilot_reader SET statement_timeout = '5s';
REVOKE ALL ON SCHEMA public FROM policypilot_reader;
GRANT USAGE ON SCHEMA public TO policypilot_reader;
GRANT SELECT ON customers, vehicles, claims TO policypilot_reader;
