#!/bin/sh
# Apply TUNER_CRON env var override (D-05: configurable schedule)
CRON="${TUNER_CRON:-0 0 * * *}"
sed -i "s|^0 0 \* \* \*|$CRON|" /etc/crontabs/root
exec crond -f -l 2
