import requests
from datetime import datetime, date


BASE_URL = "https://api.track.toggl.com/api/v9"


class TogglClient:
    def __init__(self, api_token: str):
        self.session = requests.Session()
        self.session.auth = (api_token, "api_token")
        self._projects_cache: dict = {}
        self._tasks_cache: dict = {}
        self._user: dict | None = None

    def get_user(self) -> dict:
        if not self._user:
            resp = self.session.get(f"{BASE_URL}/me")
            resp.raise_for_status()
            self._user = resp.json()
        return self._user

    def get_time_entries(self, start_date: date, end_date: date) -> list:
        params = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        resp = self.session.get(f"{BASE_URL}/me/time_entries", params=params)
        resp.raise_for_status()
        return resp.json() or []

    def _get_project(self, workspace_id: int, project_id: int) -> dict:
        key = (workspace_id, project_id)
        if key not in self._projects_cache:
            resp = self.session.get(
                f"{BASE_URL}/workspaces/{workspace_id}/projects/{project_id}"
            )
            resp.raise_for_status()
            self._projects_cache[key] = resp.json()
        return self._projects_cache[key]

    def _get_task(self, workspace_id: int, project_id: int, task_id: int) -> dict:
        key = (workspace_id, project_id, task_id)
        if key not in self._tasks_cache:
            resp = self.session.get(
                f"{BASE_URL}/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}"
            )
            resp.raise_for_status()
            self._tasks_cache[key] = resp.json()
        return self._tasks_cache[key]

    def get_enriched_entries(self, start_date: date, end_date: date) -> list:
        """Returns time entries enriched with project/task names, sorted by start time."""
        raw = self.get_time_entries(start_date, end_date)
        user = self.get_user()
        member_name = user.get("fullname", "")

        result = []
        for entry in raw:
            # Skip running timers (duration < 0 means timer is still running)
            if entry.get("duration", 0) < 0:
                continue

            workspace_id = entry.get("workspace_id")
            project_id = entry.get("project_id")
            task_id = entry.get("task_id")

            project_name = ""
            task_name = ""

            if project_id and workspace_id:
                try:
                    proj = self._get_project(workspace_id, project_id)
                    project_name = proj.get("name", "")
                except Exception:
                    pass

            if task_id and project_id and workspace_id:
                try:
                    task = self._get_task(workspace_id, project_id, task_id)
                    task_name = task.get("name", "")
                except Exception:
                    pass

            start_dt = datetime.fromisoformat(entry["start"].replace("Z", "+00:00"))
            stop_dt = (
                datetime.fromisoformat(entry["stop"].replace("Z", "+00:00"))
                if entry.get("stop")
                else None
            )

            result.append(
                {
                    "start": start_dt,
                    "stop": stop_dt,
                    "project": project_name,
                    "task": task_name,
                    "description": entry.get("description", ""),
                    "duration_seconds": entry.get("duration", 0),
                    "member": member_name,
                }
            )

        return sorted(result, key=lambda x: x["start"])
