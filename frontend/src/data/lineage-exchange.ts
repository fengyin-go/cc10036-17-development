/** 跨系统血缘交换与调度详情的前端数据类型。 */

export type LineageNode = {
  node_key: string
  name: string
  type: string
  fact_table: string
  fact_origin: string
  predecessors: string[]
  partition_field: string
  partitions: string[]
}

export type LineageExchangeFile = {
  format: string
  format_version: number
  source_system: string
  exchange_id: string
  exported_at: string
  nodes: LineageNode[]
}

export type FactCheck = {
  node_key: string
  node_name: string
  fact_table: string
  fact_origin: string
  verdict: string
  local_node_id: number | null
  blocking: boolean
}

export type NodeMapping = {
  node_key: string
  node_name: string
  fact_table: string
  fact_origin: string
  target_node_id: number | null
  action: string
  verdict: string
}

export type MissingEdge = {
  node_key: string
  node_name: string
  predecessor_key: string
  reason: string
}

export type ExchangeConflict = {
  type: string
  node_name: string
  incoming_node_key: string
  incoming_fact_table: string
  incoming_origin: string
  local_node_ids: number[]
  local_fact_tables: string[]
  message: string
}

export type ImportResult = {
  ok: boolean
  message: string
  duplicate?: boolean
  version_id?: string
  version_status?: string
  digest?: string
  node_mapping?: Record<string, NodeMapping>
  fact_table_checks?: FactCheck[]
  conflicts?: ExchangeConflict[]
  missing_edges?: MissingEdge[]
  recompute?: {
    node_count: number
    mapped_node_count: number
    created_node_count: number
    reused_node_count: number
    edge_count: number
    expected_edge_count: number
    missing_edge_count: number
    partition_count: number
    partitions: string[]
    plans_recomputed: number
  }
  plan_impacts?: PlanImpact[]
}

export type ExportResult = {
  ok: boolean
  message?: string
  file: LineageExchangeFile
  digest: string
  node_count: number
  edge_count: number
}

export type PlanImpact = {
  plan_id: number
  plan_name: string
  plan_status: string
  reference_kind: string
  referenced_node_ids: number[]
  fact_table: string
  window: string[]
  window_count: number
  ready: boolean
  blockers: { type: string; message: string }[]
}

export type ScopeBlocker = {
  type: string
  message: string
  token?: string
  version_id?: string
}

export type PartitionScope = {
  ok: boolean
  plan_id: number
  plan_name: string
  plan_status: string
  fact_table: string
  partition_field: string
  ready: boolean
  blockers: ScopeBlocker[]
  predecessors: {
    node_id: number
    node_name: string
    token: string
    verified: boolean
    source_version: string
    partitions: string[]
  }[]
  verified_predecessor_count: number
  upstream_closure: {
    node_id: number
    node_name: string
    fact_table: string
    partitions: string[]
  }[]
  partitions: string[]
  partition_count: number
  range: { start: string; end: string }
  skipped_partitions: string[]
  recompute: {
    node_count: number
    partition_count: number
    partitions: string[]
  }
  lineage_references: {
    version_id: string
    version_status: string
    source_system: string
    node_ids: number[]
    reference_kind: string
    origin: string
    recorded_at: string
  }[]
  lineage_reference_origin: string
  message?: string
}

export type ExchangeVersionSummary = {
  version_id: string
  status: string
  source_system: string
  exchange_id: string
  digest: string
  loaded_at: string
  node_count: number
  edge_count: number
  conflict_count: number
  missing_edge_count: number
  plan_ref_count: number
}
