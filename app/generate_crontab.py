import json
import os
import re
import sys
from collections import OrderedDict

RUN_SCRIPT = "/app/run-script.sh"
LOG_REDIRECT = ">> /var/log/cron.log 2>&1"
APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def parse_app_schedules(raw_value):
    if not raw_value.strip():
        return {}

    try:
        schedules = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"APP_SCHEDULES must be valid JSON: {exc}") from exc

    if not isinstance(schedules, dict):
        raise ValueError("APP_SCHEDULES must be a JSON object mapping app ids to cron schedules")

    parsed = OrderedDict()
    for app_id, schedule in schedules.items():
        validate_app_id(app_id)
        validate_cron_schedule(schedule, f"APP_SCHEDULES[{app_id!r}]")
        parsed[app_id] = schedule.strip()

    return parsed


def validate_app_id(app_id):
    if not isinstance(app_id, str) or not app_id.strip():
        raise ValueError("APP_SCHEDULES app ids must be non-empty strings")
    if app_id != app_id.strip():
        raise ValueError(f"APP_SCHEDULES app id {app_id!r} must not have surrounding whitespace")
    if not APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError(
            f"APP_SCHEDULES app id {app_id!r} must only contain letters, numbers, dots, underscores, or hyphens"
        )


def validate_cron_schedule(schedule, label):
    if not isinstance(schedule, str):
        raise ValueError(f"{label} must be a string")
    if "\n" in schedule or "\r" in schedule:
        raise ValueError(f"{label} must not contain newlines")
    fields = schedule.strip().split()
    if len(fields) != 5:
        raise ValueError(f"{label} must be a 5-field cron expression")


def group_schedules(app_schedules):
    grouped = OrderedDict()
    for app_id, schedule in app_schedules.items():
        grouped.setdefault(schedule, []).append(app_id)
    return grouped


def build_crontab(cron_schedule, app_schedules):
    cron_schedule = cron_schedule.strip()
    if cron_schedule:
        validate_cron_schedule(cron_schedule, "CRON_SCHEDULE")

    lines = []
    for schedule, app_ids in group_schedules(app_schedules).items():
        include_ids = ",".join(app_ids)
        lines.append(
            f"{schedule} SCHEDULE_INCLUDE_APP_IDS={include_ids} {RUN_SCRIPT} {LOG_REDIRECT}"
        )

    if cron_schedule:
        custom_ids = ",".join(app_schedules.keys())
        env_prefix = f"SCHEDULE_EXCLUDE_APP_IDS={custom_ids} " if custom_ids else ""
        lines.append(f"{cron_schedule} {env_prefix}{RUN_SCRIPT} {LOG_REDIRECT}")

    if lines:
        lines.append("# An empty line is required at the end of this file for a valid cron file")

    return "\n".join(lines) + ("\n" if lines else "")


def main():
    try:
        app_schedules = parse_app_schedules(os.getenv("APP_SCHEDULES", ""))
        crontab = build_crontab(os.getenv("CRON_SCHEDULE", ""), app_schedules)
    except ValueError as exc:
        print(f"Invalid schedule configuration: {exc}", file=sys.stderr)
        return 1

    print(crontab, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
