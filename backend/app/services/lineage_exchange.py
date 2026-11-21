"""跨系统血缘交换：按节点导出交接文件、目标端核对事实表出处并载入图版本。

交换文件同时记录每个节点的事实表出处（物理表 + 来源系统）和前驱节点键，
目标端据此做节点映射，而不是只按节点名称合并。并发载入同一份文件时，
摘要相同的内容只会生成一个图版本，后到的请求复用先到的版本。
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

from app.store import store

EXCHANGE_FORMAT = "lineage-exchange"
EXCHANGE_VERSION = 1
STATUS_CONFIRMED = "已确认"
STATUS_PENDING = "待补齐"
LOCAL_SOURCE = "local"

PROVENANCE_LOCAL_MATCH = "本地重合"
PROVENANCE_EXTERNAL_EXISTING = "外域已存在"
PROVENANCE_EXTERNAL_NEW = "外域新增"
PROVENANCE_NAME_CONFLICT = "同名异源"
PROVENANCE_MISMATCH = "出处不符"
PROVENANCE_LOCAL_MISSING = "本地出处缺失"
PROVENANCE_MISSING_FACT = "事实表缺失"
PROVENANCE_SAME_FACT_OTHER_SOURCE = "同表异源"
PROVENANCE_KEY_REUSED = "节点键已映射"

BLOCKING_VERDICTS = {
    PROVENANCE_MISMATCH,
    PROVENANCE_LOCAL_MISSING,
    PROVENANCE_MISSING_FACT,
    PROVENANCE_SAME_FACT_OTHER_SOURCE,
}


def split_tokens(value: Any) -> list[str]:
    return _split(value)


def parse_int(value: Any) -> int | None:
    return _as_int(value)


def node_partitions(row: dict[str, Any]) -> list[str]:
    return _node_partitions(row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _split(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _node_fact(row: dict[str, Any]) -> str:
    fact = str(row.get("事实表") or "").strip()
    if fact:
        return fact
    # 历史节点只登记了名称，表类节点把名称视为物理表名。
    if str(row.get("节点类型") or "") in ("table", "column"):
        return str(row.get("节点名称") or "").strip()
    return ""


def _node_origin(row: dict[str, Any]) -> str:
    return str(row.get("来源系统") or LOCAL_SOURCE).strip() or LOCAL_SOURCE


def _node_partitions(row: dict[str, Any]) -> list[str]:
    if isinstance(row.get("分区列表"), list):
        return sorted({str(item).strip() for item in row["分区列表"] if str(item).strip()})
    return sorted(set(_split(row.get("分区列表"))))


def _is_local_origin(origin: str) -> bool:
    return origin.casefold() in {LOCAL_SOURCE, "本地", "local"}


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """去掉导出时间等易变字段，保证同一内容反复导出得到同一摘要。"""
    nodes = []
    for node in payload.get("nodes", []):
        nodes.append({
            "node_key": node.get("node_key"),
            "name": node.get("name"),
            "type": node.get("type"),
            "fact_table": node.get("fact_table"),
            "fact_origin": node.get("fact_origin"),
            "predecessors": sorted(node.get("predecessors") or []),
            "partition_field": node.get("partition_field"),
            "partitions": sorted(node.get("partitions") or []),
        })
    nodes.sort(key=lambda item: str(item.get("node_key")))
    return {
        "format": payload.get("format"),
        "format_version": payload.get("format_version"),
        "source_system": payload.get("source_system"),
        "nodes": nodes,
    }


def payload_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        _canonical_payload(payload), sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_exchange(payload: dict[str, Any]) -> dict[str, Any]:
    """接受中英文键名的交换文件，统一成内部结构。"""
    source_system = str(
        payload.get("source_system") or payload.get("来源系统") or LOCAL_SOURCE
    ).strip() or LOCAL_SOURCE
    raw_nodes = payload.get("nodes") or payload.get("节点列表") or payload.get("节点") or []
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    nodes: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            continue
        predecessors = (
            raw.get("predecessors")
            if "predecessors" in raw
            else raw.get("前驱") if "前驱" in raw else raw.get("上游列表")
        )
        partitions = raw.get("partitions") or raw.get("分区列表") or []
        node_key = str(
            raw.get("node_key") or raw.get("节点键") or f"node-{index + 1}"
        ).strip()
        nodes.append({
            "node_key": node_key,
            "name": str(raw.get("name") or raw.get("节点名称") or node_key).strip(),
            "type": str(raw.get("type") or raw.get("节点类型") or "table").strip(),
            "fact_table": str(
                raw.get("fact_table") or raw.get("事实表") or ""
            ).strip(),
            "fact_origin": str(
                raw.get("fact_origin")
                or raw.get("事实表出处")
                or raw.get("来源系统")
                or source_system
            ).strip() or source_system,
            "predecessors": _split(predecessors),
            "partition_field": str(
                raw.get("partition_field") or raw.get("分区字段") or "dt"
            ).strip(),
            "partitions": sorted(set(_split(partitions))),
            "raw": raw,
        })
    return {
        "format": str(payload.get("format") or EXCHANGE_FORMAT),
        "format_version": int(payload.get("format_version") or EXCHANGE_VERSION),
        "source_system": source_system,
        "exchange_id": str(payload.get("exchange_id") or "").strip(),
        "exported_at": str(payload.get("exported_at") or "").strip(),
        "fact_tables": payload.get("fact_tables") or [],
        "nodes": nodes,
    }


class LineageExchangeRegistry:
    """图版本与摘要索引；用一把可重入锁保证并发载入只落一个版本。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._versions: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._by_digest: dict[str, str] = {}
        self._external_keys: dict[str, int] = {}
        self._counter = 0

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def reset(self) -> None:
        with self._lock:
            self._versions.clear()
            self._order.clear()
            self._by_digest.clear()
            self._external_keys.clear()
            self._counter = 0

    def next_version_id(self) -> str:
        self._counter += 1
        return f"lgv-{self._counter:04d}"

    def find_by_digest(self, digest: str) -> dict[str, Any] | None:
        version_id = self._by_digest.get(digest)
        if version_id is None:
            return None
        return self._versions.get(version_id)

    def save(self, version: dict[str, Any], digest: str) -> None:
        self._versions[version["version_id"]] = version
        self._order.append(version["version_id"])
        self._by_digest[digest] = version["version_id"]

    def versions(self) -> list[dict[str, Any]]:
        return [self._versions[vid] for vid in self._order]

    def get(self, version_id: str) -> dict[str, Any] | None:
        return self._versions.get(version_id)

    def discard(self, version_id: str, digest: str) -> None:
        self._versions.pop(version_id, None)
        if version_id in self._order:
            self._order.remove(version_id)
        if self._by_digest.get(digest) == version_id:
            self._by_digest.pop(digest, None)

    def latest_confirmed(self) -> dict[str, Any] | None:
        for version in reversed(self.versions()):
            if version.get("status") == STATUS_CONFIRMED:
                return version
        return None

    def register_external_key(self, key: str, node_id: int) -> None:
        self._external_keys[key] = node_id

    def resolve_external_key(self, key: str) -> int | None:
        if key in self._external_keys:
            return self._external_keys[key]
        # local:<id> 或纯数字键直接指向本地节点。
        candidate = key.split(":", 1)[1] if ":" in key else key
        node_id = _as_int(candidate)
        if node_id is None:
            return None
        row = store.find("data_lineage", node_id)
        if row is None:
            return None
        if ":" in key:
            source = key.split(":", 1)[0]
            if source.casefold() not in ("local", "本地") and _node_origin(row) != source:
                return None
        return node_id


exchange_registry = LineageExchangeRegistry()


class LineageExchangeService:
    """按节点导出交接文件，并在目标端核对出处、映射节点、落图版本。"""

    # ---- 导出 ----

    def export_nodes(
        self, node_ids: list[int] | None = None, source_system: str = LOCAL_SOURCE,
    ) -> dict[str, Any]:
        rows = [row for row in store.rows("data_lineage") if row.get("status") != "已停用"]
        if node_ids:
            wanted = {int(value) for value in node_ids}
            rows = [row for row in rows if int(row.get("id", 0)) in wanted]
        if not rows:
            return {"ok": False, "message": "没有可导出的血缘节点"}

        key_by_id = {
            int(row.get("id", 0)): f"{_node_origin(row)}:{int(row.get('id', 0))}"
            for row in rows
        }
        # 上游节点不在导出范围时仍保留键，目标端若本地已有该节点可解析，
        # 解析不到就作为“未齐依赖”暴露出来，而不是悄悄丢掉一条边。
        nodes: list[dict[str, Any]] = []
        fact_tables: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            node_id = int(row.get("id", 0))
            predecessors = []
            for upstream in _split(row.get("上游列表")):
                upstream_id = _as_int(upstream)
                if upstream_id is None:
                    continue
                predecessors.append(
                    key_by_id.get(upstream_id, f"{source_system}:{upstream_id}")
                )
            fact = _node_fact(row)
            origin = _node_origin(row)
            partitions = _node_partitions(row)
            nodes.append({
                "node_key": key_by_id[node_id],
                "name": str(row.get("节点名称", "")),
                "type": str(row.get("节点类型", "")),
                "fact_table": fact,
                "fact_origin": origin,
                "predecessors": predecessors,
                "partition_field": str(row.get("分区字段") or "dt"),
                "partitions": partitions,
            })
            if fact:
                bucket = fact_tables.setdefault(
                    (fact, origin),
                    {"fact_table": fact, "fact_origin": origin, "node_keys": [], "partitions": []},
                )
                bucket["node_keys"].append(key_by_id[node_id])
                bucket["partitions"] = sorted(set(bucket["partitions"]) | set(partitions))

        nodes.sort(key=lambda item: item["node_key"])
        payload = {
            "format": EXCHANGE_FORMAT,
            "format_version": EXCHANGE_VERSION,
            "source_system": source_system,
            "exchange_id": "",
            "exported_at": _now(),
            "fact_tables": sorted(fact_tables.values(), key=lambda item: item["fact_table"]),
            "nodes": nodes,
        }
        digest = payload_digest(payload)
        payload["exchange_id"] = f"lex-{digest[:12]}"
        return {
            "ok": True,
            "file": payload,
            "digest": digest,
            "node_count": len(nodes),
            "edge_count": sum(len(node["predecessors"]) for node in nodes),
        }

    # ---- 载入 ----

    def import_exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        file_payload = payload.get("file") if isinstance(payload.get("file"), dict) else payload
        if not isinstance(file_payload, dict):
            return {"ok": False, "message": "交接文件格式不正确"}
        normalized = normalize_exchange(file_payload)
        if normalized["format"] != EXCHANGE_FORMAT:
            return {"ok": False, "message": f"不是血缘交换文件：{normalized['format']}"}
        if normalized["format_version"] != EXCHANGE_VERSION:
            return {
                "ok": False,
                "message": (
                    f"不支持的交换文件版本：{normalized['format_version']}，"
                    f"当前只支持 {EXCHANGE_VERSION}"
                ),
            }
        if not normalized["nodes"]:
            return {"ok": False, "message": "交接文件中没有血缘节点"}

        digest = payload_digest(normalized)
        with exchange_registry.lock:
            existing = exchange_registry.find_by_digest(digest)
            if existing is not None:
                # 并发或重试载入同一份文件：直接复用已确认/待补齐的版本。
                return {
                    "ok": True,
                    "duplicate": True,
                    "version_id": existing["version_id"],
                    "version_status": existing["status"],
                    "digest": digest,
                    "message": "相同交接文件已载入，复用已有图版本",
                    "node_mapping": existing["node_mapping"],
                    "fact_table_checks": existing["fact_table_checks"],
                    "conflicts": existing["conflicts"],
                    "missing_edges": existing["missing_edges"],
                    "recompute": existing["recompute"],
                    "plan_impacts": existing["plan_impacts"],
                    "partition_scope": existing.get("partition_scope", {}),
                    "task_refs": existing.get("task_refs", []),
                }

            report = self._load_new_version(normalized, digest)
            return report

    def _load_new_version(
        self, payload: dict[str, Any], digest: str,
    ) -> dict[str, Any]:
        local_rows = store.rows("data_lineage")
        by_name: dict[str, list[dict[str, Any]]] = {}
        by_fact: dict[str, list[dict[str, Any]]] = {}
        for row in local_rows:
            if row.get("status") == "已停用":
                continue
            by_name.setdefault(str(row.get("节点名称", "")), []).append(row)
            fact = _node_fact(row)
            if fact:
                by_fact.setdefault(fact, []).append(row)

        next_id = max((int(row.get("id", 0)) for row in local_rows), default=0) + 1
        mapping: dict[str, dict[str, Any]] = {}
        fact_checks: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        created_ids: set[int] = set()
        reused_ids: set[int] = set()

        for node in payload["nodes"]:
            key = node["node_key"]
            name = node["name"]
            fact = node["fact_table"]
            origin = node["fact_origin"]
            same_name = by_name.get(name, [])
            same_fact = by_fact.get(fact, []) if fact else []
            same_fact_same_origin = [
                row for row in same_fact if _node_origin(row) == origin
            ]
            same_name_same_origin = [
                row for row in same_name if _node_origin(row) == origin
            ]
            verdict = ""
            target_id: int | None = None
            blocking = False

            known_id = exchange_registry.resolve_external_key(key)
            known_row = (
                store.find("data_lineage", known_id) if known_id is not None else None
            )
            if known_row is not None and known_row.get("status") != "已停用":
                # 同一节点键再次送入：出处没变就复用上次映射，变了就当作出处不符。
                if (
                    _node_fact(known_row) == fact
                    and _node_origin(known_row) == origin
                ):
                    target_id = known_id
                    verdict = PROVENANCE_KEY_REUSED
                else:
                    verdict = PROVENANCE_MISMATCH
                    blocking = True
            elif not fact and node["type"] in ("table", "column"):
                verdict = PROVENANCE_MISSING_FACT
                blocking = True
            elif _is_local_origin(origin):
                if same_fact_same_origin:
                    target_id = int(same_fact_same_origin[0].get("id", 0))
                    verdict = PROVENANCE_LOCAL_MATCH
                elif same_name and fact and all(
                    _node_fact(row) != fact for row in same_name
                ):
                    verdict = PROVENANCE_MISMATCH
                    blocking = True
                elif same_fact:
                    verdict = PROVENANCE_MISMATCH
                    blocking = True
                else:
                    verdict = PROVENANCE_LOCAL_MISSING
                    blocking = True
            else:
                if same_fact_same_origin:
                    target_id = int(same_fact_same_origin[0].get("id", 0))
                    verdict = PROVENANCE_EXTERNAL_EXISTING
                elif same_fact:
                    verdict = PROVENANCE_SAME_FACT_OTHER_SOURCE
                    blocking = True
                elif same_name_same_origin:
                    # 报表/指标类节点没有事实表，按“同名 + 同来源系统”复用。
                    target_id = int(same_name_same_origin[0].get("id", 0))
                    verdict = PROVENANCE_EXTERNAL_EXISTING
                elif same_name:
                    verdict = PROVENANCE_NAME_CONFLICT
                else:
                    verdict = PROVENANCE_EXTERNAL_NEW

            fact_checks.append({
                "node_key": key,
                "node_name": name,
                "fact_table": fact,
                "fact_origin": origin,
                "verdict": verdict,
                "local_node_id": target_id,
                "blocking": blocking,
            })

            if verdict == PROVENANCE_NAME_CONFLICT:
                conflicts.append({
                    "type": "同名节点",
                    "node_name": name,
                    "incoming_node_key": key,
                    "incoming_fact_table": fact,
                    "incoming_origin": origin,
                    "local_node_ids": [int(row.get("id", 0)) for row in same_name],
                    "local_fact_tables": [_node_fact(row) for row in same_name],
                    "message": (
                        "同名节点事实表出处不同，按出处分别映射，未合并到本地同名节点"
                    ),
                })
                # 同名但出处不同：保留为独立节点，不和本地同名节点合并。
                target_id = self._create_node(node, next_id)
                next_id += 1
                created_ids.add(target_id)
                by_name.setdefault(name, []).append(
                    store.find("data_lineage", target_id)
                )
                if fact:
                    by_fact.setdefault(fact, []).append(
                        store.find("data_lineage", target_id)
                    )
            elif target_id is not None:
                reused_ids.add(target_id)
            elif not blocking:
                target_id = self._create_node(node, next_id)
                next_id += 1
                created_ids.add(target_id)
                by_name.setdefault(name, []).append(
                    store.find("data_lineage", target_id)
                )
                if fact:
                    by_fact.setdefault(fact, []).append(
                        store.find("data_lineage", target_id)
                    )

            mapping[key] = {
                "node_key": key,
                "node_name": name,
                "fact_table": fact,
                "fact_origin": origin,
                "target_node_id": target_id,
                "action": (
                    "复用已有节点" if target_id in reused_ids
                    else "新建节点" if target_id in created_ids
                    else "出处存疑未映射"
                ),
                "verdict": verdict,
            }
            if target_id is not None:
                exchange_registry.register_external_key(key, target_id)

        edges, missing_edges = self._map_edges(payload, mapping)
        # 新建节点把已核过的前驱写回图结构，方便血缘页直接看到交换进来的边。
        for key, entry in mapping.items():
            target_id = entry.get("target_node_id")
            if target_id is None or entry.get("action") != "新建节点":
                continue
            predecessors = sorted({
                str(edge["predecessor_id"]) for edge in edges
                if edge["successor_id"] == target_id
            }, key=int)
            row = store.find("data_lineage", int(target_id))
            if row is not None and predecessors:
                row["上游列表"] = ",".join(predecessors)

        blocking_checks = [check for check in fact_checks if check["blocking"]]
        status = STATUS_CONFIRMED
        if missing_edges or blocking_checks:
            status = STATUS_PENDING

        version_id = exchange_registry.next_version_id()
        version: dict[str, Any] = {
            "version_id": version_id,
            "status": status,
            "digest": digest,
            "source_system": payload["source_system"],
            "exchange_id": payload["exchange_id"],
            "exported_at": payload["exported_at"],
            "loaded_at": _now(),
            "node_count": len(payload["nodes"]),
            "edge_count": len(edges),
            "node_mapping": mapping,
            "fact_table_checks": fact_checks,
            "conflicts": conflicts,
            "missing_edges": missing_edges,
            "edges": edges,
            "payload": payload,
        }
        # 先登记版本再重算：调度详情读取分区范围时需要看到本版本的节点映射。
        exchange_registry.save(version, digest)
        # 记录每个受影响的调度计划引用了哪个图版本、引用的由来。
        version["task_refs"] = self._record_plan_references(version)
        try:
            recompute = self._recompute(version)
        except Exception:  # noqa: BLE001 - 重算失败不能留下半成品版本
            exchange_registry.discard(version_id, digest)
            raise
        version["recompute"] = recompute["recompute"]
        version["plan_impacts"] = recompute["plan_impacts"]
        version["partition_scope"] = recompute["partition_scope"]
        return {
            "ok": True,
            "duplicate": False,
            "version_id": version_id,
            "version_status": status,
            "digest": digest,
            "message": (
                "图版本已确认"
                if status == STATUS_CONFIRMED
                else "图版本已载入但关系未齐，调度不会将其视为就绪"
            ),
            "node_mapping": mapping,
            "fact_table_checks": fact_checks,
            "conflicts": conflicts,
            "missing_edges": missing_edges,
            "recompute": version["recompute"],
            "plan_impacts": version["plan_impacts"],
            "partition_scope": version.get("partition_scope", {}),
            "task_refs": version.get("task_refs", []),
        }

    def _create_node(self, node: dict[str, Any], node_id: int) -> int:
        row = {
            "id": node_id,
            "status": "在线",
            "pending": False,
            "abnormal": False,
            "节点名称": node["name"],
            "节点类型": node["type"],
            "上游列表": "",
            "下游列表": "",
            "数据量级": str(node["raw"].get("数据量级") or "待采集"),
            "更新频率": str(node["raw"].get("更新频率") or "待确认"),
            "负责人": str(node["raw"].get("负责人") or node["fact_origin"]),
            "事实表": node["fact_table"],
            "来源系统": node["fact_origin"],
            "分区字段": node["partition_field"],
            "分区列表": ",".join(node["partitions"]),
        }
        store.rows("data_lineage").append(row)
        return node_id

    def _map_edges(
        self, payload: dict[str, Any], mapping: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        edges: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for node in payload["nodes"]:
            successor = mapping.get(node["node_key"], {})
            successor_id = successor.get("target_node_id")
            for predecessor_key in node["predecessors"]:
                predecessor_id: int | None = None
                if predecessor_key in mapping:
                    predecessor_id = mapping[predecessor_key].get("target_node_id")
                else:
                    predecessor_id = exchange_registry.resolve_external_key(predecessor_key)
                if predecessor_id is None or successor_id is None:
                    missing.append({
                        "node_key": node["node_key"],
                        "node_name": node["name"],
                        "predecessor_key": predecessor_key,
                        "reason": (
                            "前驱节点不在交接文件中，且本地无法按出处解析"
                        ),
                    })
                    continue
                predecessor_row = store.find("data_lineage", predecessor_id)
                if predecessor_row is None or predecessor_row.get("status") != "在线":
                    missing.append({
                        "node_key": node["node_key"],
                        "node_name": node["name"],
                        "predecessor_key": predecessor_key,
                        "reason": "前驱节点已下线或不存在",
                    })
                    continue
                edges.append({
                    "predecessor_key": predecessor_key,
                    "successor_key": node["node_key"],
                    "predecessor_id": predecessor_id,
                    "successor_id": successor_id,
                    "verified": True,
                })
        return edges, missing

    # ---- 重算与计划影响 ----

    def _recompute(self, version: dict[str, Any]) -> dict[str, Any]:
        mapping = version["node_mapping"]
        payload_by_key = {node["node_key"]: node for node in version["payload"]["nodes"]}
        partitions: set[str] = set()
        partition_nodes: list[dict[str, Any]] = []
        for key, entry in mapping.items():
            node = payload_by_key.get(key, {})
            node_partitions = set(node.get("partitions") or [])
            target_id = entry.get("target_node_id")
            if target_id is not None:
                row = store.find("data_lineage", target_id)
                if row is not None:
                    node_partitions |= set(_node_partitions(row))
            if node_partitions:
                partitions |= node_partitions
                partition_nodes.append({
                    "node_key": key,
                    "node_name": entry["node_name"],
                    "partitions": sorted(node_partitions),
                })

        plan_impacts: list[dict[str, Any]] = []
        from app.services.query_scheduler import QuerySchedulerService

        scheduler = QuerySchedulerService()
        for ref in version.get("task_refs", []):
            scope = scheduler.compute_partition_refresh_scope(int(ref["plan_id"]))
            plan_row = store.find("query_scheduler", int(ref["plan_id"])) or {}
            plan_impacts.append({
                "plan_id": ref["plan_id"],
                "plan_name": ref["plan_name"],
                "plan_status": str(plan_row.get("status", "")),
                "reference_kind": ref["reference_kind"],
                "referenced_node_ids": ref["node_ids"],
                "fact_table": ref["fact_table"],
                "window": scope.get("partitions", []),
                "window_count": scope.get("partition_count", 0),
                "ready": scope.get("ready", False),
                "blockers": scope.get("blockers", []),
            })

        recompute = {
            "node_count": len(mapping),
            "mapped_node_count": sum(
                1 for entry in mapping.values() if entry.get("target_node_id") is not None
            ),
            "created_node_count": sum(
                1 for entry in mapping.values() if entry.get("action") == "新建节点"
            ),
            "reused_node_count": sum(
                1 for entry in mapping.values() if entry.get("action") == "复用已有节点"
            ),
            "edge_count": len(version["edges"]),
            "expected_edge_count": sum(
                len(node["predecessors"]) for node in version["payload"]["nodes"]
            ),
            "missing_edge_count": len(version["missing_edges"]),
            "partition_node_count": len(partition_nodes),
            "partition_count": len(partitions),
            "partitions": sorted(partitions),
            "partition_nodes": partition_nodes,
            "plans_recomputed": len(plan_impacts),
        }
        return {
            "recompute": recompute,
            "plan_impacts": plan_impacts,
            "partition_scope": {
                "partition_count": len(partitions),
                "partitions": sorted(partitions),
                "start": min(partitions) if partitions else "",
                "end": max(partitions) if partitions else "",
            },
        }

    def _record_plan_references(self, version: dict[str, Any]) -> list[dict[str, Any]]:
        """影响范围按载入后的节点映射算，避免先重算、后登记带来的顺序问题。"""
        mapping = version.get("node_mapping", {})
        mapped_ids = {
            int(entry["target_node_id"]) for entry in mapping.values()
            if entry.get("target_node_id") is not None
        }
        facts = {entry.get("fact_table") for entry in mapping.values() if entry.get("fact_table")}
        refs: list[dict[str, Any]] = []
        for row in store.rows("query_scheduler"):
            plan_id = int(row.get("id", 0))
            pred_ids: set[int] = set()
            for token in split_tokens(row.get("前驱节点")):
                node_id = parse_int(token)
                if node_id is not None:
                    pred_ids.add(node_id)
                elif mapping.get(token, {}).get("target_node_id") is not None:
                    pred_ids.add(int(mapping[token]["target_node_id"]))
            fact = str(row.get("事实表") or "").strip()
            if not (pred_ids & mapped_ids) and fact not in facts:
                continue
            reused = any(
                entry.get("action") == "复用已有节点"
                and entry.get("target_node_id") in pred_ids
                for entry in mapping.values()
            )
            reference = {
                "plan_id": plan_id,
                "plan_name": str(row.get("计划名称", "")),
                "reference_kind": "旧计划引用" if reused else "新计划上游依赖",
                "node_ids": sorted(pred_ids & mapped_ids),
                "fact_table": fact,
            }
            refs.append(reference)
            history = row.setdefault("血缘引用", [])
            if not isinstance(history, list):
                history = []
                row["血缘引用"] = history
            history.append({
                "version_id": version["version_id"],
                "version_status": version["status"],
                "source_system": version["source_system"],
                "node_ids": reference["node_ids"],
                "reference_kind": reference["reference_kind"],
                "origin": str(
                    row.get("血缘引用由来") or "计划登记时指定的前驱节点"
                ),
                "recorded_at": version["loaded_at"],
            })
        return refs


    # ---- 查询 ----

    def list_versions(self) -> dict[str, Any]:
        items = [
            {
                "version_id": version["version_id"],
                "status": version["status"],
                "source_system": version["source_system"],
                "exchange_id": version["exchange_id"],
                "digest": version["digest"],
                "loaded_at": version["loaded_at"],
                "node_count": version["node_count"],
                "edge_count": version["edge_count"],
                "conflict_count": len(version["conflicts"]),
                "missing_edge_count": len(version["missing_edges"]),
                "plan_ref_count": len(version.get("task_refs", [])),
            }
            for version in exchange_registry.versions()
        ]
        return {"ok": True, "items": items, "total": len(items)}

    def get_version(self, version_id: str) -> dict[str, Any]:
        version = exchange_registry.get(version_id)
        if version is None:
            return {"ok": False, "message": f"图版本 {version_id} 不存在"}
        return {
            "ok": True,
            "version_id": version["version_id"],
            "status": version["status"],
            "source_system": version["source_system"],
            "exchange_id": version["exchange_id"],
            "digest": version["digest"],
            "loaded_at": version["loaded_at"],
            "node_mapping": version["node_mapping"],
            "fact_table_checks": version["fact_table_checks"],
            "conflicts": version["conflicts"],
            "missing_edges": version["missing_edges"],
            "edges": version["edges"],
            "recompute": version.get("recompute", {}),
            "plan_impacts": version.get("plan_impacts", []),
            "task_refs": version.get("task_refs", []),
        }

    # ---- 示例文件 ----

    def sample_file(self, kind: str = "conflict") -> dict[str, Any]:
        """三节点交接文件：一张订单日表与本地重合，另一张同名表来自外域。"""
        nodes = [
            {
                "node_key": "crm:order-day-local",
                "name": "订单日表",
                "type": "table",
                "fact_table": "dwd_order_day",
                "fact_origin": "local",
                "predecessors": ["local:3"],
                "partition_field": "dt",
                "partitions": ["2026-11-18", "2026-11-19", "2026-11-20"],
            },
            {
                "node_key": "crm:order-day-ext",
                "name": "订单日表",
                "type": "table",
                "fact_table": "ext_order_daily",
                "fact_origin": "crm-domain",
                "predecessors": [],
                "partition_field": "dt",
                "partitions": ["2026-11-19", "2026-11-20", "2026-11-21"],
            },
            {
                "node_key": "crm:order-report",
                "name": "订单日报表",
                "type": "report",
                "fact_table": "",
                "fact_origin": "crm-domain",
                "predecessors": ["crm:order-day-local", "crm:order-day-ext"],
                "partition_field": "dt",
                "partitions": [],
            },
        ]
        if kind == "missing-edge":
            # 送来的第三个节点多出一条目标端无法解析的前驱边。
            nodes[2]["predecessors"] = [
                "crm:order-day-local", "crm:order-day-ext", "crm:orphan-seed",
            ]
        payload = {
            "format": EXCHANGE_FORMAT,
            "format_version": EXCHANGE_VERSION,
            "source_system": "crm-domain",
            "exchange_id": "",
            "exported_at": _now(),
            "fact_tables": [
                {
                    "fact_table": "dwd_order_day",
                    "fact_origin": "local",
                    "node_keys": ["crm:order-day-local"],
                    "partitions": ["2026-11-18", "2026-11-19", "2026-11-20"],
                },
                {
                    "fact_table": "ext_order_daily",
                    "fact_origin": "crm-domain",
                    "node_keys": ["crm:order-day-ext"],
                    "partitions": ["2026-11-19", "2026-11-20", "2026-11-21"],
                },
            ],
            "nodes": nodes,
        }
        digest = payload_digest(payload)
        payload["exchange_id"] = f"lex-sample-{kind}-{digest[:8]}"
        return {"ok": True, "file": payload, "digest": digest, "kind": kind}


# 跨模块共享的交接服务实例。
lineage_exchange = LineageExchangeService()
