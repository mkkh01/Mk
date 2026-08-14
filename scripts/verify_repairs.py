import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_dashboard_auth
from app.dashboard_endpoints import router as dashboard_router
from app.workflow_endpoints import router as workflow_router


def main() -> None:
    os.environ["DASHBOARD_API_TOKEN"] = "test-token"
    test_app = FastAPI()
    test_app.include_router(
        dashboard_router,
        prefix="/dashboard-test",
    )
    test_app.include_router(
        workflow_router,
        prefix="/workflow-test",
    )

    with TestClient(test_app) as client:
        unauth = client.get("/workflow-test/api/workflow/status/BTCUSDT")
        assert unauth.status_code == 401, unauth.text

    assert any(dep.dependency is require_dashboard_auth for dep in dashboard_router.dependencies)
    assert any(dep.dependency is require_dashboard_auth for dep in workflow_router.dependencies)
    print("auth_router_checks_ok")


if __name__ == "__main__":
    main()
