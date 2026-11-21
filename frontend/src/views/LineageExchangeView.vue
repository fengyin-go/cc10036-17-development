<template>
  <section class="page" data-module="data_lineage_exchange">
    <header class="page-head">
      <div>
        <h2>数据血缘 · 跨系统交接</h2>
        <p class="page-desc">
          按血缘节点导出交接文件给目标端，载入时核对事实表出处与前驱关系，形成可追溯的图版本。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="load-sample-conflict" type="button" @click="loadSample('conflict')">
          载入示例（同名冲突）
        </button>
        <button class="btn" data-testid="load-sample-missing" type="button" @click="loadSample('missing-edge')">
          载入示例（缺边）
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="exchange-error">{{ errorMessage }}</p>

    <div class="exchange-grid">
      <article class="exchange-card">
        <h3>1. 按节点导出交接文件</h3>
        <label class="exchange-field">
          <span>节点 ID（逗号分隔，留空导出全部在线节点）</span>
          <input v-model="exportNodeIds" data-testid="export-node-ids" placeholder="例如 3,10,11" />
        </label>
        <label class="exchange-field">
          <span>来源系统</span>
          <input v-model="sourceSystem" data-testid="export-source" />
        </label>
        <div class="page-actions">
          <button class="btn primary" data-testid="export-exchange" type="button" :disabled="busy" @click="doExport">
            导出交接文件
          </button>
        </div>
        <p v-if="exportResult" class="exchange-note" data-testid="export-summary">
          {{ exportResult.node_count }} 个节点 / {{ exportResult.edge_count }} 条边，摘要
          <code>{{ exportResult.digest.slice(0, 12) }}</code>
        </p>
        <pre v-if="exportPreview" class="exchange-pre" data-testid="export-preview">{{ exportPreview }}</pre>
      </article>

      <article class="exchange-card">
        <h3>2. 接收并载入交接文件</h3>
        <textarea
          v-model="importText"
          class="exchange-textarea"
          data-testid="import-text"
          placeholder="粘贴交接文件 JSON，或点击上方示例按钮自动填充"
        />
        <div class="page-actions">
          <button class="btn primary" data-testid="import-exchange" type="button" :disabled="busy" @click="doImport">
            核对出处并载入
          </button>
          <button class="btn ghost" type="button" @click="importText = ''">清空</button>
        </div>
      </article>
    </div>

    <template v-if="importResult && importResult.ok">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">图版本</span>
          <strong class="stat-value" data-testid="version-id">{{ importResult.version_id }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">版本状态</span>
          <strong class="stat-value" data-testid="version-status">{{ importResult.version_status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">命中已有版本</span>
          <strong class="stat-value">{{ importResult.duplicate ? '是' : '否' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">未齐依赖</span>
          <strong class="stat-value" data-testid="missing-edge-count">
            {{ importResult.missing_edges?.length ?? 0 }}
          </strong>
        </article>
      </div>

      <h3>节点映射</h3>
      <table class="data-table" data-testid="node-mapping">
        <thead>
          <tr><th>节点键</th><th>节点名称</th><th>事实表</th><th>出处</th><th>核对结论</th><th>映射目标</th><th>处理</th></tr>
        </thead>
        <tbody>
          <tr v-for="(entry, key) in importResult.node_mapping" :key="key">
            <td><code>{{ key }}</code></td>
            <td>{{ entry.node_name }}</td>
            <td>{{ entry.fact_table || '—' }}</td>
            <td>{{ entry.fact_origin }}</td>
            <td>{{ entry.verdict }}</td>
            <td>{{ entry.target_node_id ?? '—' }}</td>
            <td>{{ entry.action }}</td>
          </tr>
        </tbody>
      </table>

      <h3>事实表出处核对</h3>
      <table class="data-table" data-testid="fact-checks">
        <thead>
          <tr><th>节点</th><th>事实表</th><th>出处</th><th>结论</th><th>本地节点</th><th>是否阻断</th></tr>
        </thead>
        <tbody>
          <tr v-for="check in importResult.fact_table_checks" :key="check.node_key + check.fact_table">
            <td>{{ check.node_name }}</td>
            <td>{{ check.fact_table || '—' }}</td>
            <td>{{ check.fact_origin }}</td>
            <td>{{ check.verdict }}</td>
            <td>{{ check.local_node_id ?? '—' }}</td>
            <td>{{ check.blocking ? '阻断' : '通过' }}</td>
          </tr>
        </tbody>
      </table>

      <template v-if="importResult.conflicts?.length">
        <h3>同名异源冲突</h3>
        <ul class="exchange-list" data-testid="conflicts">
          <li v-for="conflict in importResult.conflicts" :key="conflict.incoming_node_key">
            <strong>{{ conflict.node_name }}</strong>：外域出处 {{ conflict.incoming_fact_table }}
            （{{ conflict.incoming_origin }}）与本地节点
            {{ conflict.local_node_ids.join('、') }}（{{ conflict.local_fact_tables.join('、') }}）同名。
            {{ conflict.message }}
          </li>
        </ul>
      </template>

      <template v-if="importResult.missing_edges?.length">
        <h3>未齐依赖</h3>
        <ul class="exchange-list error-text" data-testid="missing-edges">
          <li v-for="edge in importResult.missing_edges" :key="edge.node_key + edge.predecessor_key">
            {{ edge.node_name }} 的前驱 <code>{{ edge.predecessor_key }}</code>：{{ edge.reason }}。
            相关计划不会被当作就绪。
          </li>
        </ul>
      </template>

      <h3>实际重算数据</h3>
      <div class="exchange-grid" v-if="importResult.recompute">
        <dl class="exchange-detail" data-testid="recompute">
          <dt>节点 / 已映射</dt><dd>{{ importResult.recompute.node_count }} / {{ importResult.recompute.mapped_node_count }}</dd>
          <dt>新建 / 复用</dt><dd>{{ importResult.recompute.created_node_count }} / {{ importResult.recompute.reused_node_count }}</dd>
          <dt>边（已解析 / 应有）</dt>
          <dd>{{ importResult.recompute.edge_count }} / {{ importResult.recompute.expected_edge_count }}</dd>
          <dt>分区范围</dt>
          <dd>{{ importResult.recompute.partitions.join('、') || '—' }}（{{ importResult.recompute.partition_count }} 个）</dd>
          <dt>受影响计划</dt><dd>{{ importResult.recompute.plans_recomputed }}</dd>
        </dl>
        <div class="exchange-card">
          <h3>受影响的调度计划</h3>
          <table class="data-table">
            <thead><tr><th>计划</th><th>引用类型</th><th>刷新窗口</th><th>是否就绪</th></tr></thead>
            <tbody>
              <tr v-for="plan in importResult.plan_impacts" :key="plan.plan_id">
                <td><RouterLink :to="`/query_scheduler/${plan.plan_id}`">{{ plan.plan_name }}</RouterLink></td>
                <td>{{ plan.reference_kind }}</td>
                <td>{{ plan.window.join('、') || '—' }}</td>
                <td>{{ plan.ready ? '就绪' : (plan.blockers[0]?.type ?? '未就绪') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <h3>已载入图版本</h3>
    <table class="data-table" data-testid="exchange-versions">
      <thead>
        <tr><th>版本</th><th>状态</th><th>来源系统</th><th>节点</th><th>边</th><th>冲突</th><th>缺边</th><th>载入时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="version in versions" :key="version.version_id">
          <td>{{ version.version_id }}</td>
          <td>{{ version.status }}</td>
          <td>{{ version.source_system }}</td>
          <td>{{ version.node_count }}</td>
          <td>{{ version.edge_count }}</td>
          <td>{{ version.conflict_count }}</td>
          <td>{{ version.missing_edge_count }}</td>
          <td>{{ version.loaded_at }}</td>
        </tr>
        <tr v-if="!versions.length">
          <td colspan="8" class="empty-state">还没有载入过血缘交接文件</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  exportExchange,
  importExchange,
  listExchangeVersions,
  sampleExchange,
} from '@/api/lineage-exchange'
import type {
  ExchangeVersionSummary,
  ExportResult,
  ImportResult,
} from '@/data/lineage-exchange'

const busy = ref(false)
const errorMessage = ref('')
const exportNodeIds = ref('3,10,11')
const sourceSystem = ref('local')
const exportResult = ref<ExportResult | null>(null)
const exportPreview = ref('')
const importText = ref('')
const importResult = ref<ImportResult | null>(null)
const versions = ref<ExchangeVersionSummary[]>([])

async function refreshVersions() {
  try {
    versions.value = (await listExchangeVersions()).items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '图版本读取失败'
  }
}

async function doExport() {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await exportExchange(exportNodeIds.value, sourceSystem.value)
    if (!result.ok) throw new Error(result.message ?? '导出失败')
    exportResult.value = result
    exportPreview.value = JSON.stringify(result.file, null, 2)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  } finally {
    busy.value = false
  }
}

async function loadSample(kind: 'conflict' | 'missing-edge') {
  errorMessage.value = ''
  try {
    const sample = await sampleExchange(kind)
    importText.value = JSON.stringify(sample.file, null, 2)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '示例文件读取失败'
  }
}

async function doImport() {
  if (!importText.value.trim()) {
    errorMessage.value = '请先粘贴交接文件或载入示例'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const payload = JSON.parse(importText.value)
    const result = await importExchange(payload)
    importResult.value = result
    if (!result.ok) throw new Error(result.message)
    await refreshVersions()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '载入失败'
  } finally {
    busy.value = false
  }
}

onMounted(refreshVersions)
</script>

<style scoped>
.exchange-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.exchange-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.exchange-card h3 { margin: 0 0 8px; font-size: 14px; }
.exchange-field { display: block; margin-bottom: 10px; }
.exchange-field span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.exchange-field input { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; }
.exchange-textarea { width: 100%; min-height: 190px; font-family: monospace; font-size: 12px; border: 1px solid var(--border); border-radius: 6px; padding: 8px; }
.exchange-pre { background: #0f172a; color: #e2e8f0; border-radius: 6px; padding: 10px; font-size: 11px; max-height: 220px; overflow: auto; }
.exchange-note { font-size: 12px; color: var(--muted); }
.exchange-list { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px 10px 28px; font-size: 13px; }
.exchange-detail { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: grid; grid-template-columns: 140px 1fr; gap: 6px 12px; margin: 0; }
.exchange-detail dt { color: var(--muted); font-size: 12px; }
.exchange-detail dd { margin: 0; font-size: 13px; }
@media (max-width: 960px) { .exchange-grid { grid-template-columns: 1fr; } }
</style>
