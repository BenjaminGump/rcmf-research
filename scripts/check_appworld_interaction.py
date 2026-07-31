from __future__ import annotations

import argparse
import ast
import json
import time
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.utils.logging import redact
from rcmf.utils.serialization import atomic_write_json


def parse_printed_dict(observation: str) -> dict[str, Any]:
    text = observation.strip()
    if not text:
        return {}
    last_line = text.splitlines()[-1]
    try:
        parsed = json.loads(last_line)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    try:
        parsed = ast.literal_eval(last_line)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def supervisor_payload(supervisor: Any) -> dict[str, str]:
    return {
        "first_name": str(getattr(supervisor, "first_name", "")),
        "last_name": str(getattr(supervisor, "last_name", "")),
        "email": str(getattr(supervisor, "email", "")),
        "phone_number": str(getattr(supervisor, "phone_number", "")),
    }


def run_step(world: Any, name: str, code: str) -> dict[str, Any]:
    start = time.perf_counter()
    observation = str(world.execute(code))
    elapsed = time.perf_counter() - start
    parsed = parse_printed_dict(observation)
    return {
        "name": name,
        "code": code,
        "observation": redact(observation),
        "parsed": parsed,
        "elapsed_s": round(elapsed, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize an AppWorld task and verify real environment observations."
    )
    parser.add_argument("--task-id", default="0a9d82a_1")
    parser.add_argument("--experiment-name", default="rcmf_appworld_interaction_check")
    parser.add_argument("--output", default=None)
    parser.add_argument("--expected-streak", type=int, default=4)
    parser.add_argument("--no-complete-task", action="store_true")
    args = parser.parse_args()

    from appworld import AppWorld

    checks: dict[str, bool] = {}
    steps: list[dict[str, Any]] = []
    started = time.perf_counter()

    with AppWorld(task_id=args.task_id, experiment_name=args.experiment_name) as world:
        supervisor = supervisor_payload(world.task.supervisor)
        checks["task_loaded"] = bool(world.task.instruction.strip()) and bool(supervisor["email"])

        steps.append(
            run_step(
                world,
                "python_execution",
                "value = 1 + 2\nprint({'python_ok': value == 3, 'value': value})",
            )
        )
        checks["python_execution"] = steps[-1]["parsed"].get("python_ok") is True

        steps.append(
            run_step(
                world,
                "supervisor_password_lookup",
                """
passwords = apis.supervisor.show_account_passwords()
simple_note_password = next(
    item["password"] for item in passwords if item["account_name"] == "simple_note"
)
print({
    "password_found": bool(simple_note_password),
    "num_passwords": len(passwords),
    "simple_note_password_length": len(simple_note_password),
})
""".strip(),
            )
        )
        checks["supervisor_password_lookup"] = steps[-1]["parsed"].get("password_found") is True

        steps.append(
            run_step(
                world,
                "simple_note_login",
                f"""
login_result = apis.simple_note.login(
    username={supervisor["email"]!r},
    password=simple_note_password,
)
simple_note_access_token = login_result["access_token"]
print({{
    "login_success": bool(simple_note_access_token),
    "access_token_length": len(simple_note_access_token),
}})
""".strip(),
            )
        )
        checks["simple_note_login"] = steps[-1]["parsed"].get("login_success") is True

        steps.append(
            run_step(
                world,
                "habit_note_search",
                """
habit_notes = apis.simple_note.search_notes(
    access_token=simple_note_access_token,
    query="habit tracking posture",
)
print({
    "num_notes": len(habit_notes),
    "first_note_id": habit_notes[0]["note_id"] if habit_notes else None,
    "first_title": habit_notes[0]["title"] if habit_notes else None,
})
""".strip(),
            )
        )
        checks["habit_note_search"] = int(steps[-1]["parsed"].get("num_notes") or 0) > 0

        steps.append(
            run_step(
                world,
                "show_first_habit_note",
                """
recent_note = apis.simple_note.show_note(
    access_token=simple_note_access_token,
    note_id=habit_notes[0]["note_id"],
)
print({
    "has_content": bool(recent_note.get("content")),
    "contains_posture_field": "practiced_good_posture:" in recent_note.get("content", ""),
    "content_length": len(recent_note.get("content", "")),
})
""".strip(),
            )
        )
        checks["show_first_habit_note"] = (
            steps[-1]["parsed"].get("contains_posture_field") is True
        )

        steps.append(
            run_step(
                world,
                "count_habit_notes",
                """
all_habit_notes = []
page_index = 0
while True:
    notes_page = apis.simple_note.search_notes(
        access_token=simple_note_access_token,
        query="Habit Tracking Log",
        page_index=page_index,
    )
    if not notes_page:
        break
    all_habit_notes.extend(notes_page)
    page_index += 1
print({"num_habit_notes": len(all_habit_notes), "pages_read": page_index})
""".strip(),
            )
        )
        checks["count_habit_notes"] = int(steps[-1]["parsed"].get("num_habit_notes") or 0) > 0

        steps.append(
            run_step(
                world,
                "calculate_posture_streak",
                """
posture_data = []
for note in all_habit_notes:
    note_content = apis.simple_note.show_note(
        access_token=simple_note_access_token,
        note_id=note["note_id"],
    )
    content = note_content["content"]
    if "practiced_good_posture:" in content:
        value = content.split("practiced_good_posture:")[1].split("\\n")[0].strip()
        date = note["created_at"][:10]
        posture_data.append((date, value == "yes"))

current_streak = 0
max_streak = 0
for date, practiced in sorted(posture_data, key=lambda item: item[0]):
    if practiced:
        current_streak += 1
        max_streak = max(max_streak, current_streak)
    else:
        current_streak = 0

print({
    "posture_entries": len(posture_data),
    "max_streak": max_streak,
})
""".strip(),
            )
        )
        max_streak = steps[-1]["parsed"].get("max_streak")
        checks["calculate_posture_streak"] = isinstance(max_streak, int) and max_streak >= 0
        checks["expected_streak"] = max_streak == args.expected_streak

        if not args.no_complete_task:
            steps.append(
                run_step(
                    world,
                    "complete_task",
                    "apis.supervisor.complete_task(answer=max_streak)",
                )
            )
            checks["complete_task_observation"] = "Execution successful" in steps[-1]["observation"]
        else:
            checks["complete_task_observation"] = True

        checks["world_task_completed"] = bool(world.task_completed())
        try:
            evaluation = world.evaluate(suppress_errors=True).to_dict(stats_only=True)
        except Exception as exc:
            evaluation = {"success": False, "error": str(exc)}

    checks["evaluation_success"] = bool(evaluation.get("success", False))
    all_passed = all(checks.values())
    summary = {
        "task_id": args.task_id,
        "experiment_name": args.experiment_name,
        "all_passed": all_passed,
        "checks": checks,
        "evaluation": evaluation,
        "steps": steps,
        "wall_time_s": round(time.perf_counter() - started, 3),
    }

    output_path = Path(args.output or f"runs/appworld/{args.experiment_name}_{args.task_id}.json")
    atomic_write_json(output_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
