-- Cluster roles and application database. Passwords come from the environment via psql \getenv.
\getenv database PPAP_PG_DATABASE
\getenv migrator_user PPAP_PG_MIGRATOR_USER
\getenv migrator_password PPAP_PG_MIGRATOR_PASSWORD
\getenv api_user PPAP_PG_API_USER
\getenv api_password PPAP_PG_API_PASSWORD
\getenv worker_user PPAP_PG_WORKER_USER
\getenv worker_password PPAP_PG_WORKER_PASSWORD

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'migrator_user', :'migrator_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrator_user')
\gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'migrator_user', :'migrator_password')
\gexec

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'api_user', :'api_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'api_user')
\gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'api_user', :'api_password')
\gexec

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'worker_user', :'worker_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'worker_user')
\gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'worker_user', :'worker_password')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'database', :'migrator_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'database')
\gexec
