"""Measure InfraWatch deployment request duration.

This measures the platform-facing deployment duration, not the whole human
manual baseline. In real Kubernetes mode, `/deploy` includes kubectl apply,
rollout status, and ready/available replica verification.
"""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main() -> None:
    """POST one deployment request and print elapsed time plus API response."""

    parser = argparse.ArgumentParser(description="Measure InfraWatch deployment duration.")
    parser.add_argument("--api-base", default="http://localhost:8000", help="FastAPI base URL.")
    parser.add_argument("--name", default="measurement-api", help="Service name.")
    parser.add_argument("--image", default="docker.io/parthchn178/infrawatch-backend:latest", help="Container image.")
    parser.add_argument("--replicas", type=int, default=1, help="Replica count.")
    parser.add_argument("--port", type=int, default=8000, help="Container/service port.")
    args = parser.parse_args()

    payload = {
        "name": args.name,
        "image": args.image,
        "replicas": args.replicas,
        "port": args.port,
        "environment": {"ENVIRONMENT": "measurement"},
    }
    request = Request(
        f"{args.api_base.rstrip('/')}/deploy",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.perf_counter()
    try:
        with urlopen(request, timeout=600) as response:
            body = json.loads(response.read().decode("utf-8"))
            status = response.status
    except HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        status = exc.code
    elapsed = time.perf_counter() - start

    print(
        json.dumps(
            {
                "api_base": args.api_base,
                "service": args.name,
                "image": args.image,
                "http_status": status,
                "elapsed_seconds": round(elapsed, 2),
                "deployment_status": body.get("deployment", {}).get("status"),
                "message": body.get("deployment", {}).get("message") or body.get("detail"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
