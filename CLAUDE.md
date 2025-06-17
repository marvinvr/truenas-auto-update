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

- Uses TrueNAS SCALE v2.0 API (`/api/v2.0`) with Bearer token authentication
- Implements notification system via Apprise library for multiple notification services
- Supports both immediate execution and scheduled execution via cron
- Job waiting mechanism for async upgrade operations using TrueNAS job system
- SSL verification disabled for TrueNAS API calls (common for self-signed certificates)

## Environment Configuration

Required:
- `BASE_URL`: TrueNAS instance URL
- `API_KEY`: TrueNAS API key

Optional:
- `CRON_SCHEDULE`: Cron expression for scheduling (if not set, runs once)
- `APPRISE_URLS`: Comma-separated notification URLs
- `NOTIFY_ON_SUCCESS`: Enable success notifications (default: false)

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
  truenas-auto-update

# Run container with cron schedule
docker run --rm \
  -e BASE_URL=https://your-truenas-url \
  -e API_KEY=your-api-key \
  -e CRON_SCHEDULE="0 4 * * *" \
  truenas-auto-update
```

## API Integration Details

- Uses TrueNAS `/app` endpoint to list apps and check `upgrade_available` flag
- Triggers upgrades via `/app/upgrade` endpoint with app ID
- Monitors upgrade progress using `/core/job_wait` endpoint for job completion
- All API calls use Bearer token authentication and disable SSL verification