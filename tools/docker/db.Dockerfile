# MariaDB with the rAthena schema baked in (build context: /sql-files).
# The scripts only run on first start, while the data volume is still empty.
FROM mariadb:10.11

COPY main.sql /docker-entrypoint-initdb.d/01-main.sql
COPY logs.sql /docker-entrypoint-initdb.d/02-logs.sql
