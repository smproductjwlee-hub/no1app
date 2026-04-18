from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    """다른 프로그램이 8000을 쓰는지 구분용. 반드시 service 키가 있어야 이 앱이다."""
    return {
        "status": "ok",
        "service": "workbridge-fastapi",
        "browser_urls": {
            "enter": "/enter",
            "login": "/login",
            "login_admin": "/login?role=admin",
            "login_worker": "/login?role=worker",
            "worker_gate": "/worker",
            "admin_app": "/admin",
        },
    }
