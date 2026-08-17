#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    chown -R morning:morning /app/data
    exec gosu morning "$@"
fi

exec "$@"
