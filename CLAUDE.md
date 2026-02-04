# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Architecture

This is a simple Python application that automatically updates TrueNAS SCALE apps using the TrueNAS API. The application is containerized and can run either once or on a cron schedule.

### Core Components

- `app/main.py`: Main application that connects to TrueNAS API, checks for app updates, and performs upgrades
- `docker-entrypoint.sh`: Entry script that handles both one-time runs and cron scheduling
- `run-script.sh`: Wrapper script for cron execution
- `crontab`: Template for cron schedule (uses environment variable substitution)

### Key Architecture Points

- Uses TrueNAS JSON-RPC 2.0 API via WebSocket (`/api/current`) with the official `truenas-api-client` library
- Implements notification system via Apprise library for multiple notification services
- Supports both immediate execution and scheduled execution via cron
- Job waiting is handled automatically by the API client when using `job=True` parameter
- Configurable SSL verification for TrueNAS API calls (disabled by default for self-signed certificates)

## Environment Configuration

Required:
- `BASE_URL`: TrueNAS instance URL
- `API_KEY`: TrueNAS API key

Optional:
- `API_USERNAME`: Username for API key authentication (default: root). Required for TrueNAS 25.04+ where API keys are user-linked.
- `CRON_SCHEDULE`: Cron expression for scheduling (if not set, runs once)
- `APPRISE_URLS`: Comma-separated notification URLs
- `NOTIFY_ON_SUCCESS`: Enable success notifications (default: false)
- `SSL_VERIFY`: Enable SSL certificate verification (default: false)

## Development Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run the application locally (requires environment variables)
cd app && python main.py
```

### Docker Development
```bash
# Build the container
docker build -t truenas-auto-update .

# Run container (one-time execution)
docker run --rm \
  -e BASE_URL=https://your-truenas-url \
  -e API_KEY=your-api-key \
  -e API_USERNAME=admin \
  truenas-auto-update

# Run container with cron schedule
docker run --rm \
  -e BASE_URL=https://your-truenas-url \
  -e API_KEY=your-api-key \
  -e API_USERNAME=admin \
  -e CRON_SCHEDULE="0 4 * * *" \
  truenas-auto-update
```

## API Integration Details

- Uses TrueNAS JSON-RPC 2.0 API over WebSocket (official `truenas-api-client` library)
- Connects via `wss://` or `ws://` to `/api/current` endpoint
- Authenticates using `auth.login_ex` with `API_KEY_PLAIN` mechanism (required for TrueNAS 25.04+)
- Uses `app.query` method to list apps and check `upgrade_available` flag
- Triggers upgrades via `app.upgrade` method with app ID and `job=True` for automatic job completion waiting
- SSL verification is configurable via the client's `verify_ssl` parameter