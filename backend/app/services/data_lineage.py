"""数据血缘领域服务：图结构计算、影响分析、上下游追踪。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app.analytics_engine import LineageGraph, LineageNode
from app.services.lineage_exchange import lineage_exchange


class DataLineageService(BaseModuleService):
    MODULE = "data_lineage"
    ENTITY = "血缘节点"
    OPERATION_TERM = "校验"
    LIST_FIELDS = [
        "节点名称", "节点类型", "上游列表", "下游列表", "事实表", "来源系统",
        "分区字段", "分区列表", "数据量级", "更新频率", "负责人",
    ]
    REQUIRED_FIELDS = ["节点名称", "节点类型", "上游列表"]
    STATUS_ORDER = ["在线", "已下线", "已停用"]
    ACTION_RULES = {"标记下线": "已下线", "停用节点": "已停用"}
    NEGATIVE_ACTIONS = ["停用节点"]

    # ---- 业务方法 ----

    def build_graph_from_store(self) -> LineageGraph:
        """从Store中构建血缘图。"""
        graph = LineageGraph()
        rows = store.rows(self.MODULE)
        for row in rows:
            if row.get("status") != "在线":
                continue
            node_id = str(row.get("id", ""))
            upstream_raw = str(row.get("上游列表", ""))
            downstream_raw = str(row.get("下游列表", ""))
            node = LineageNode(
                node_id=node_id,
                node_name=str(row.get("节点名称", "")),
                node_type=str(row.get("节点类型", "")),
                upstream_ids=[u.strip() for u in upstream_raw.split(",") if u.strip()],
                downstream_ids=[d.strip() for d in downstream_raw.split(",") if d.strip()],
            )
            graph.add_node(node)
        return graph

    def compute_upstream(self, node_id: str) -> dict[str, Any]:
        """计算上游血缘链。"""
        graph = self.build_graph_from_store()
        entry = store.find(self.MODULE, int(node_id))
        if entry is None:
            return {"ok": False, "message": f"节点 {node_id} 不存在"}
        chains = graph.upstream_chain(node_id)
        return {
            "ok": True,
            "node_name": str(entry.get("节点名称", "")),
            "node_type": str(entry.get("节点类型", "")),
            "upstream_chains": chains,
            "chain_count": len(chains),
            "max_depth": max((len(c) for c in chains), default=0),
        }

    def compute_downstream(self, node_id: str) -> dict[str, Any]:
        """计算下游影响链。"""
        graph = self.build_graph_from_store()
        entry = store.find(self.MODULE, int(node_id))
        if entry is None:
            return {"ok": False, "message": f"节点 {node_id} 不存在"}
        chains = graph.downstream_chain(node_id)
        return {
            "ok": True,
            "node_name": str(entry.get("节点名称", "")),
            "node_type": str(entry.get("节点类型", "")),
            "downstream_chains": chains,
            "chain_count": len(chains),
            "max_depth": max((len(c) for c in chains), default=0),
        }

    def impact_analysis(self, node_id: str) -> dict[str, Any]:
        """变更影响分析：找出所有下游节点。"""
        graph = self.build_graph_from_store()
        entry = store.find(self.MODULE, int(node_id))
        if entry is None:
            return {"ok": False, "message": f"节点 {node_id} 不存在"}
        impacted = graph.impact_analysis(node_id)
        return {
            "ok": True,
            "node_name": str(entry.get("节点名称", "")),
            "impacted_node_ids": list(impacted),
            "impacted_count": len(impacted),
            "severity": "高" if len(impacted) > 5 else "中" if len(impacted) > 2 else "低",
        }

    def get_online_nodes(self) -> list[dict[str, Any]]:
        """获取所有在线节点。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "在线"
        ]

    def export_lineage_graph(self) -> dict[str, Any]:
        """导出完整血缘图结构（供数据范围消费）。"""
        graph = self.build_graph_from_store()
        adj = graph.to_adjacency()
        return {
            "ok": True,
            "node_count": len(adj["nodes"]),
            **adj,
        }

    # ---- 跨系统交接 ----

    def export_handoff(self, node_ids: list[int] | None = None) -> dict[str, Any]:
        """按血缘节点导出交接文件，记下事实表出处和前驱节点键。"""
        return lineage_exchange.export_nodes(node_ids)

    def import_handoff(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收交接文件：核对事实表出处、映射节点、落图版本。"""
        return lineage_exchange.import_exchange(payload)

    def list_exchange_versions(self) -> dict[str, Any]:
        return lineage_exchange.list_versions()

    def get_exchange_version(self, version_id: str) -> dict[str, Any]:
        return lineage_exchange.get_version(version_id)

    def sample_handoff(self, kind: str = "conflict") -> dict[str, Any]:
        return lineage_exchange.sample_file(kind)

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "数据血缘数据集"
        online = sum(1 for row in store.rows(self.MODULE) if row.get("status") == "在线")
        base["online_nodes"] = online
        return base