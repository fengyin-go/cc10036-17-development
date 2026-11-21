"""查询调度领域服务：任务编排、依赖链、超时控制、分区刷新范围。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app.services.lineage_exchange import (
    STATUS_CONFIRMED,
    exchange_registry,
    node_partitions,
    parse_int,
    split_tokens,
)


class QuerySchedulerService(BaseModuleService):
    MODULE = "query_scheduler"
    ENTITY = "调度计划"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["计划名称", "查询语句", "执行周期", "依赖任务", "前驱节点", "超时分钟", "失败重试", "优先级"]
    REQUIRED_FIELDS = ["计划名称", "查询语句", "执行周期"]
    STATUS_ORDER = ["待调度", "执行中", "已完成", "调度失败", "已暂停"]
    ACTION_RULES = {"开始执行": "执行中", "完成调度": "已完成", "标记失败": "调度失败", "暂停计划": "已暂停"}
    NEGATIVE_ACTIONS = ["标记失败"]

    # ---- 业务方法 ----

    def build_dependency_graph(self) -> dict[str, Any]:
        """构建调度计划的依赖图（数据血缘→查询调度消费）。"""
        rows = store.rows(self.MODULE)
        graph: dict[str, list[str]] = {}  # plan_name -> [dependent_plan_names]
        for row in rows:
            name = str(row.get("计划名称", ""))
            deps_raw = str(row.get("依赖任务", ""))
            deps = [d.strip() for d in deps_raw.split(",") if d.strip()]
            graph[name] = deps
        return {"ok": True, "nodes": len(rows), "graph": graph}

    def topological_order(self) -> dict[str, Any]:
        """按依赖拓扑排序调度计划（血缘关系→调度顺序）。"""
        deps = self.build_dependency_graph().get("graph", {})
        in_degree: dict[str, int] = {name: 0 for name in deps}
        for name, dep_list in deps.items():
            for dep in dep_list:
                if dep in in_degree:
                    in_degree[dep] = in_degree.get(dep, 0) + 1

        from collections import deque
        queue: deque[str] = deque(name for name, deg in in_degree.items() if deg == 0)
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            if node in deps:
                for dep in deps[node]:
                    in_degree[dep] -= 1
                    if in_degree[dep] == 0:
                        queue.append(dep)

        has_cycle = len(order) != len(deps)
        return {
            "ok": True,
            "plan_count": len(deps),
            "execution_order": order,
            "has_cycle": has_cycle,
            "cycle_nodes": [n for n, d in in_degree.items() if d > 0],
        }

    def get_ready_plans(self) -> list[dict[str, Any]]:
        """获取待调度、依赖任务已完成且前置血缘已核过的计划。

        送来的血缘图缺边时，依赖这批边的计划会挂在 pending_lineage 上，
        不会因为“依赖任务字段为空”或“依赖名恰好匹配不到”而被当作就绪。
        """
        rows = store.rows(self.MODULE)
        ready: list[dict[str, Any]] = []
        for row in rows:
            if row.get("status") != "待调度":
                continue
            deps_raw = str(row.get("依赖任务", ""))
            if deps_raw.strip():
                dep_names = [d.strip() for d in deps_raw.split(",") if d.strip()]
                matched = [
                    r for r in rows
                    if str(r.get("计划名称", "")) in dep_names
                ]
                if not dep_names or not matched:
                    continue
                if not all(_is_completed(r) for r in matched):
                    continue
                missing = [name for name in dep_names if name not in {
                    str(r.get("计划名称", "")) for r in matched
                }]
                if missing:
                    continue
            scope = self.compute_partition_refresh_scope(int(row.get("id", 0)))
            if not scope.get("ready"):
                continue
            ready.append(row)
        return ready

    def compute_partition_refresh_scope(self, plan_id: int) -> dict[str, Any]:
        """只用核过的前置关系推导分区刷新范围。

        - 数字前驱按本地节点 ID 解析；交换文件里的节点键按图版本映射解析。
        - 加载后仍缺边、出处存疑的图版本会阻止相关计划就绪。
        """
        plan = store.find(self.MODULE, plan_id)
        if plan is None:
            return {"ok": False, "message": f"调度计划 {plan_id} 不存在", "ready": False}

        blockers: list[dict[str, Any]] = []
        versions = exchange_registry.versions()
        confirmed = exchange_registry.latest_confirmed()
        pending = self._active_pending(versions)

        tokens = split_tokens(plan.get("前驱节点"))
        if not tokens:
            blockers.append({
                "type": "缺少前驱",
                "message": "计划未登记前驱节点，无法核对前置关系",
            })

        direct_nodes: list[dict[str, Any]] = []
        for token in tokens:
            node = self._resolve_predecessor(token, confirmed)
            if node is None:
                node = self._resolve_predecessor(token, versions[-1] if versions else None)
            if node is None:
                blockers.append({
                    "type": "前驱未解析",
                    "token": token,
                    "message": (
                        f"前驱 {token} 尚未载入已确认的血缘图版本"
                        if parse_int(token) is None
                        else f"前驱节点 {token} 不存在或已下线"
                    ),
                })
                continue
            version = self._version_for_node(token, versions)
            direct_nodes.append({
                "node_id": node["id"],
                "node_name": node.get("节点名称", ""),
                "token": token,
                "verified": version is None or version["status"] == STATUS_CONFIRMED,
                "source_version": version["version_id"] if version else "本地血缘",
                "partitions": node_partitions(node),
            })

        closure = self._upstream_closure(direct_nodes, confirmed)
        closure_ids = {int(item["node_id"]) for item in closure}

        # 待确认版本里缺的边落在本计划引用的节点上时，计划不得就绪。
        plan_fact = str(plan.get("事实表") or "").strip()
        for version in pending:
            missing = version.get("missing_edges", [])
            missing_keys = {
                edge["node_key"] for edge in missing
            } | {
                edge["predecessor_key"] for edge in missing
            }
            mapping = version.get("node_mapping", {})
            # 交接文件里的前驱键按节点映射换算成本地节点，再和本计划前驱比对。
            plan_node_ids = set()
            for token in tokens:
                node_id = parse_int(token)
                if node_id is not None:
                    plan_node_ids.add(node_id)
                elif token in mapping and mapping[token].get("target_node_id") is not None:
                    plan_node_ids.add(int(mapping[token]["target_node_id"]))
            missing_target_ids = {
                int(entry["target_node_id"]) for key, entry in mapping.items()
                if key in missing_keys and entry.get("target_node_id") is not None
            }
            touched = set()
            if plan_node_ids & missing_target_ids:
                touched.add("计划前驱")
            if missing_target_ids & closure_ids:
                touched.add("上游链")
            unverified = {
                item["token"] for item in direct_nodes
                if item.get("source_version") == version["version_id"]
                and item["token"] in missing_keys
            }
            touched |= unverified
            conflicted_facts = {
                entry.get("fact_table") for entry in mapping.values()
                if entry.get("verdict") in ("同名异源", "同表异源")
            }
            if plan_fact and plan_fact in conflicted_facts:
                conflicts = [
                    conflict for conflict in version.get("conflicts", [])
                    if conflict.get("incoming_fact_table") == plan_fact
                ]
                blockers.append({
                    "type": "出处待核",
                    "version_id": version["version_id"],
                    "message": "事实表出处存在同名异源冲突，需人工确认",
                    "conflicts": conflicts,
                })
            if touched:
                blockers.append({
                    "type": "未齐依赖",
                    "version_id": version["version_id"],
                    "version_status": version["status"],
                    "affected": sorted(touched),
                    "missing_edges": missing,
                    "message": (
                        f"图版本 {version['version_id']} 仍有 "
                        f"{len(missing)} 条边未解析，相关计划不得就绪"
                    ),
                })

        upstream_partitions: set[str] = set()
        for item in closure:
            upstream_partitions |= set(item["partitions"])

        plan_partitions = set(split_tokens(plan.get("分区列表")))
        refresh_partitions = (
            sorted(plan_partitions & upstream_partitions)
            if plan_partitions else sorted(upstream_partitions)
        )
        skipped = sorted(plan_partitions - upstream_partitions) if plan_partitions else []

        relation_verified = bool(direct_nodes) and all(
            item["verified"] for item in direct_nodes
        )
        ready = (
            str(plan.get("status")) == "待调度"
            and relation_verified
            and not blockers
        )
        if not relation_verified and not blockers:
            blockers.append({
                "type": "前置未核",
                "message": "存在未核过的前置关系",
            })

        return {
            "ok": True,
            "plan_id": plan_id,
            "plan_name": str(plan.get("计划名称", "")),
            "plan_status": str(plan.get("status", "")),
            "fact_table": plan_fact,
            "partition_field": str(plan.get("分区字段") or "dt"),
            "ready": ready,
            "blockers": blockers,
            "predecessors": direct_nodes,
            "verified_predecessor_count": sum(
                1 for item in direct_nodes if item["verified"]
            ),
            "upstream_closure": closure,
            "upstream_partitions": sorted(upstream_partitions),
            "partitions": refresh_partitions,
            "partition_count": len(refresh_partitions),
            "range": {
                "start": refresh_partitions[0] if refresh_partitions else "",
                "end": refresh_partitions[-1] if refresh_partitions else "",
            },
            "skipped_partitions": skipped,
            "recompute": {
                "node_count": len(closure),
                "partition_count": len(refresh_partitions),
                "partitions": refresh_partitions,
                "nodes": closure,
            },
            "lineage_references": list(plan.get("血缘引用") or []),
            "lineage_reference_origin": str(
                plan.get("血缘引用由来") or "计划登记时指定的前驱节点"
            ),
        }

    def get_plan_detail(self, plan_id: int) -> dict[str, Any]:
        plan = store.find(self.MODULE, plan_id)
        if plan is None:
            return {"ok": False, "message": f"调度计划 {plan_id} 不存在"}
        scope = self.compute_partition_refresh_scope(plan_id)
        return {
            "ok": True,
            "plan": plan,
            "partition_scope": scope,
            "lineage_references": list(plan.get("血缘引用") or []),
            "lineage_reference_origin": str(
                plan.get("血缘引用由来") or "计划登记时指定的前驱节点"
            ),
        }

    def _active_pending(self, versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """每个来源系统只保留最新一版待补齐图，旧版本缺边不应永久拖住调度。"""
        latest: dict[str, dict[str, Any]] = {}
        for version in versions:
            if version["status"] == STATUS_CONFIRMED:
                latest.pop(version["source_system"], None)
            else:
                latest[version["source_system"]] = version
        return list(latest.values())

    def _resolve_predecessor(
        self, token: str, confirmed: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        node_id = parse_int(token)
        if node_id is not None:
            row = store.find("data_lineage", node_id)
            if row is None or row.get("status") != "在线":
                return None
            return row
        if confirmed is None:
            return None
        entry = confirmed.get("node_mapping", {}).get(token)
        if entry is None or entry.get("target_node_id") is None:
            return None
        return store.find("data_lineage", int(entry["target_node_id"]))

    def _version_for_node(
        self, token: str, versions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if parse_int(token) is not None:
            return None
        for version in reversed(versions):
            if token in version.get("node_mapping", {}):
                return version
        return None

    def _upstream_closure(
        self, direct_nodes: list[dict[str, Any]], confirmed: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """沿已核过的前置边向上收集节点与分区，不跨过未核边。"""
        upstream_of: dict[int, set[int]] = {}
        for row in store.rows("data_lineage"):
            if row.get("status") != "在线":
                continue
            node_id = int(row.get("id", 0))
            for token in split_tokens(row.get("上游列表")):
                upstream_id = parse_int(token)
                if upstream_id is not None:
                    upstream_of.setdefault(node_id, set()).add(upstream_id)
        if confirmed is not None:
            for edge in confirmed.get("edges", []):
                if edge.get("verified"):
                    upstream_of.setdefault(edge["successor_id"], set()).add(
                        edge["predecessor_id"]
                    )

        seen: set[int] = set()
        queue = [int(node["node_id"]) for node in direct_nodes]
        ordered: list[int] = []
        while queue:
            node_id = queue.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            ordered.append(node_id)
            for upstream_id in sorted(upstream_of.get(node_id, set())):
                if upstream_id not in seen:
                    queue.append(upstream_id)

        closure: list[dict[str, Any]] = []
        for node_id in ordered:
            row = store.find("data_lineage", node_id)
            if row is None:
                continue
            closure.append({
                "node_id": node_id,
                "node_name": str(row.get("节点名称", "")),
                "fact_table": str(row.get("事实表") or ""),
                "partitions": node_partitions(row),
            })
        return closure

    def retry_failed(self) -> dict[str, Any]:
        """重试失败的调度计划。"""
        rows = store.rows(self.MODULE)
        retried = 0
        for row in rows:
            if row.get("status") == "调度失败":
                max_retries = _parse_int(row.get("失败重试"), 3)
                retry_count = row.get("_retry_count", 0)
                if retry_count < max_retries:
                    row["_retry_count"] = retry_count + 1
                    self.run_action(int(row["id"]), "开始执行")
                    retried += 1
        return {"ok": True, "retried_count": retried}

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "查询调度数据集"
        base["ready_count"] = len(self.get_ready_plans())
        return base


def _is_completed(row: dict[str, Any]) -> bool:
    return row.get("status") == "已完成"


def _parse_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default
