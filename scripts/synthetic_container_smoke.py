"""Verify isolated CI containers using explicitly synthetic, unlicensed data.

Start the services first. This replaces API/dashboard containers and imports
two fixture minutes into their volume. Run only in an isolated CI project.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen


def compose(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *arguments],
        check=True,
        text=True,
        capture_output=True,
        timeout=180,
    )
    return result.stdout


def fetch(path: str) -> bytes:
    with urlopen(f"http://127.0.0.1:3000{path}", timeout=10) as response:
        return bytes(response.read())


def assert_dataset(dataset_id: str) -> None:
    result = json.loads(fetch(f"/api/datasets/{dataset_id}"))
    assert result["candle_count"] == 2 and result["replayable"] is True
    assert result["provenance"]["data_kind"] == "synthetic"
    candles = json.loads(fetch(f"/api/datasets/{dataset_id}/candles?timeframe=1m"))
    assert len(candles) == 2
    assert candles[0]["opened_at"] == "2025-03-10T13:00:00+00:00"
    assert candles[1]["close_ticks"] == 29001


def main() -> None:
    if os.environ.get("CI") != "true" or os.environ.get("COMPOSE_PROJECT_NAME") != "aurum-ci":
        raise SystemExit("run only in the isolated aurum-ci project with CI=true")
    with TemporaryDirectory(prefix="synthetic-ci-", dir="data") as directory:
        fixture = Path(directory)
        csv_path = fixture / "synthetic.csv"
        csv_path.write_text(
            "timestamp,contract,open,high,low,close,volume\n"
            "2025-03-10T13:00Z,MGC-2025-04,2900,2900.2,2899.9,2900.1,2\n"
            "2025-03-10T13:01Z,MGC-2025-04,2900,2900.2,2899.9,2900.1,3\n"
        )
        # The unprivileged worker needs read access through its read-only bind mount.
        fixture.chmod(0o755)
        schedule: dict[str, Any] = {
            "schema_version": 2,
            "instrument": "MGC",
            "source": "synthetic isolated container CI fixture",
            "version": "synthetic-v1",
            "coverage": {"start": "2025-03-10T13:00Z", "end": "2025-03-10T13:02Z"},
            "sessions": [
                {
                    "trade_date": "2025-03-10",
                    "windows": [{"start": "2025-03-10T13:00Z", "end": "2025-03-10T13:02Z"}],
                }
            ],
        }
        (fixture / "schedule.json").write_text(json.dumps(schedule))
        imported = json.loads(
            compose(
                "run",
                "--rm",
                "worker",
                "import-csv",
                f"/imports/{fixture.name}/synthetic.csv",
                "--calendar",
                f"/imports/{fixture.name}/schedule.json",
                "--provider",
                "synthetic isolated CI",
                "--contract",
                "MGC-2025-04",
                "--source-timezone",
                "UTC",
                "--timestamp-label",
                "open",
                "--data-kind",
                "synthetic",
                "--price-basis",
                "unadjusted",
            )
        )
        dataset_id = imported["dataset_id"]
        assert_dataset(dataset_id)
        compose(
            "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "api", "dashboard"
        )
        assert_dataset(dataset_id)
        audit = json.loads(compose("run", "--rm", "worker", "verify-store", "--verify-normalized"))
        assert audit["normalized"]["datasets_verified"] == 1
        page = fetch("/").decode()
        token = os.environ["AURUM_API_TOKEN"]
        assert token not in page
        assets = [
            path
            for path in re.findall(r'<script[^>]+src="([^"]+)"', page)
            if path.startswith("/_next/static/")
        ]
        assert assets
        for asset in assets:
            body = fetch(asset)
            assert body and token.encode() not in body
        try:
            urlopen("http://127.0.0.1:8765/api/health", timeout=10)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("direct API accepted a request without its token")
        print(
            json.dumps(
                {
                    "synthetic_minutes": 2,
                    "persisted_after_replacement": True,
                    "javascript_assets_verified": len(assets),
                    "direct_api_status": 401,
                }
            )
        )


if __name__ == "__main__":
    main()
