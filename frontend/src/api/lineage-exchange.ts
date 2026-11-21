import { request } from './client'
import { fetchJson } from './client'
import type {
  ExchangeVersionSummary,
  ExportResult,
  ImportResult,
  PartitionScope,
} from '@/data/lineage-exchange'

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}，操作未生效`)
  return (await response.json()) as T
}

export function sampleExchange(kind: 'conflict' | 'missing-edge' = 'conflict') {
  return fetchJson<ExportResult>(`/api/lineage/exchange/sample?kind=${kind}`)
}

export function exportExchange(nodeIds: string, sourceSystem = 'local') {
  const query = new URLSearchParams()
  if (nodeIds.trim()) query.set('node_ids', nodeIds.trim())
  query.set('source_system', sourceSystem)
  return fetchJson<ExportResult>(`/api/lineage/exchange/export?${query.toString()}`)
}

export function importExchange(file: unknown) {
  return postJson<ImportResult>('/api/lineage/exchange/import', file)
}

export function listExchangeVersions() {
  return fetchJson<{ ok: boolean; items: ExchangeVersionSummary[]; total: number }>(
    '/api/lineage/exchange/versions',
  )
}

export function loadPartitionScope(planId: number) {
  return fetchJson<PartitionScope>(`/api/scheduler/${planId}/partition-scope`)
}
