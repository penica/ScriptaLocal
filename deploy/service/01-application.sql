SELECT format('CREATE ROLE scriptalocal_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
              btrim(pg_read_file('/run/secrets/app_password'), E' \t\r\n')) \gexec
CREATE DATABASE scriptalocal OWNER scriptalocal_app;
REVOKE ALL ON DATABASE scriptalocal FROM PUBLIC;
\connect scriptalocal
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO scriptalocal_app;
