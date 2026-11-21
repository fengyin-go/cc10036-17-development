"""跨系统血缘交换与调度详情接口。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from app.services import service_for

router = APIRouter(prefix="/api", tags=["血缘交换"])


def lineage_service():
    return service_for("data_lineage")


def scheduler_service():
    return service_for("query_scheduler")


@router.get("/lineage/exchange/sample")
def sample_exchange(kind: str = Query(default="conflict")) -> dict[str, Any]:
    """生成三节点示例交接文件：本地重合的订单日表 + 外域同名表。"""
    return lineage_service().sample_handoff(kind)


@router.get("/lineage/exchange/export")
def export_exchange(
    node_ids: str | None = Query(default=None, description="逗号分隔的节点 ID，留空导出全部在线节点"),
    source_system: str = Query(default="local"),
) -> dict[str, Any]:
    """按血缘节点导出交接文件，文件内记录事实表出处与前驱节点键。"""
    parsed = None
    if node_ids:
        try:
            parsed = [int(value) for value in node_ids.split(",") if value.strip()]
        except ValueError:
            return {"ok": False, "message": "node_ids 必须是逗号分隔的整数"}
    return lineage_service().export_handoff(parsed)


@router.post("/lineage/exchange/import")
def import_exchange(
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    """接收交接文件，核对事实表出处并落已确认图版本。"""
    return lineage_service().import_handoff(payload)


@router.get("/lineage/exchange/versions")
def list_exchange_versions() -> dict[str, Any]:
    return lineage_service().list_exchange_versions()


@router.get("/lineage/exchange/versions/{version_id}")
def get_exchange_version(version_id: str) -> dict[str, Any]:
    return lineage_service().get_exchange_version(version_id)


@router.get("/scheduler/ready")
def scheduler_ready() -> dict[str, Any]:
    """待调度且前置血缘已核过、无未齐依赖的计划。"""
    service = scheduler_service()
    items = service.get_ready_plans()
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/scheduler/{plan_id}/detail")
def scheduler_detail(plan_id: int) -> dict[str, Any]:
    return scheduler_service().get_plan_detail(plan_id)


@router.get("/scheduler/{plan_id}/partition-scope")
def scheduler_partition_scope(plan_id: int) -> dict[str, Any]:
    """仅用核过的前置关系计算分区刷新范围。"""
    return scheduler_service().compute_partition_refresh_scope(plan_id)
