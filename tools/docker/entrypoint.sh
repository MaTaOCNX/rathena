#!/bin/bash
# Entrypoint of the rAthena image (see /Dockerfile and /docker-compose.yml).
# Usage: rathena-entrypoint login|char|map
# Rewrites the network/database settings in conf/import from the environment,
# waits for the database and then starts the requested server.
set -e

cd /rathena

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-ragnarok}"
DB_PASS="${DB_PASS:-ragnarok}"
DB_NAME="${DB_NAME:-ragnarok}"
LOG_DB_NAME="${LOG_DB_NAME:-$DB_NAME}"
LOGIN_HOST="${LOGIN_HOST:-login}"
CHAR_HOST="${CHAR_HOST:-char}"
# Address the game client uses to reach the char and map servers
PUBLIC_IP="${PUBLIC_IP:-127.0.0.1}"

# del_conf <file> <key>: drop every active "key:" line
del_conf() {
	sed -i "/^[[:space:]]*$2[[:space:]]*:/d" "$1"
}

# set_conf <file> <key> <value>: replace the active "key:" lines by a new one
set_conf() {
	del_conf "$1" "$2"
	printf '%s: %s\n' "$2" "$3" >> "$1"
}

inter=conf/import/inter_conf.txt
echo >> "$inter"
for prefix in login_server ipban_db char_server map_server log_db; do
	set_conf "$inter" "${prefix}_ip" "$DB_HOST"
	set_conf "$inter" "${prefix}_port" "$DB_PORT"
	set_conf "$inter" "${prefix}_id" "$DB_USER"
	set_conf "$inter" "${prefix}_pw" "$DB_PASS"
	set_conf "$inter" "${prefix}_db" "$DB_NAME"
done
set_conf "$inter" log_db_db "$LOG_DB_NAME"

case "$1" in
	login)
		conf=conf/import/login_conf.txt
		echo >> "$conf"
		del_conf "$conf" bind_ip
		# Lets players register from the client by logging in as <userid>_M / <userid>_F
		[ -n "$NEW_ACCOUNT" ] && set_conf "$conf" new_account "$NEW_ACCOUNT"
		;;
	char)
		conf=conf/import/char_conf.txt
		echo >> "$conf"
		del_conf "$conf" bind_ip
		set_conf "$conf" login_ip "$LOGIN_HOST"
		set_conf "$conf" char_ip "$PUBLIC_IP"
		;;
	map)
		conf=conf/import/map_conf.txt
		echo >> "$conf"
		del_conf "$conf" bind_ip
		set_conf "$conf" char_ip "$CHAR_HOST"
		set_conf "$conf" map_ip "$PUBLIC_IP"
		;;
	*)
		# Anything else (e.g. "bash") is run as is
		exec "$@"
		;;
esac

# Inter-server account (must match the row with sex 'S' in the login table)
if [ "$1" != login ]; then
	[ -n "$SERVER_USERID" ] && set_conf "$conf" userid "$SERVER_USERID"
	[ -n "$SERVER_PASSWD" ] && set_conf "$conf" passwd "$SERVER_PASSWD"
fi

echo "Waiting for database at $DB_HOST:$DB_PORT..."
until (exec 3<>"/dev/tcp/$DB_HOST/$DB_PORT") 2>/dev/null; do
	sleep 1
done

exec "./$1-server"
