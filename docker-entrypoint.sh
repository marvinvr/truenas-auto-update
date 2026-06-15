#!/bin/bash

if [ -n "$TZ" ]; then
    if [ ! -f "/usr/share/zoneinfo/$TZ" ]; then
        echo "Invalid timezone: $TZ"
        exit 1
    fi

    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

if [ -n "$CRON_SCHEDULE" ] || [ -n "$APP_SCHEDULES" ]; then
    # Set up environment variables for cron. Overwrite (not append) so repeated
    # container restarts do not accumulate stale/duplicate entries.
    printenv \
        | grep -v "no_proxy" \
        | grep -v "^APP_SCHEDULES=" \
        | grep -v "^CRON_SCHEDULE=" \
        | grep -v "^SCHEDULE_INCLUDE_APP_IDS=" \
        | grep -v "^SCHEDULE_EXCLUDE_APP_IDS=" \
        > /etc/environment

    # Generate the crontab from the global and per-app schedules.
    if ! python /app/generate_crontab.py > /etc/cron.d/app-cron; then
        exit 1
    fi
    chmod 0644 /etc/cron.d/app-cron

    # Install cron job
    crontab /etc/cron.d/app-cron

    if [ "$RUN_ON_START" = "true" ]; then
        echo "RUN_ON_START=true, running script once before starting cron..."
        python main.py
    fi

    # Start cron service
    service cron start

    echo "Cron job installed"
    echo "Global cron schedule: ${CRON_SCHEDULE:-none}"
    if [ -n "$APP_SCHEDULES" ]; then
        echo "Per-app schedules installed from APP_SCHEDULES"
    fi
    echo "Cron timezone: ${TZ:-UTC}"
    echo "Watching logs..."
    tail -f /var/log/cron.log
else
    echo "No CRON_SCHEDULE set, running script once..."
    python main.py
fi
