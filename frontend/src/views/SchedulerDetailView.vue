<template>
  <section class="page" data-module="query_scheduler_detail">
    <header class="page-head">
      <div>
        <h2>查询调度 · 计划详情</h2>
        <p class="page-desc">
          分区刷新范围只按已核对的前置关系计算；图版本仍有缺边或出处待核时，计划不会被视为就绪。
        </p>
      </div>
      <div class="page-actions">
        <label class="exchange-field inline">
          <span>计划 ID</span>
          <input v-model.number="planId" data-testid="plan-id" type="number" min="1" @change="load" />
        </label>
        <button class="btn" data-testid="reload-detail" type="button" @click="load">刷新</button>
        <RouterLink class="btn ghost" to="/query_scheduler">返回调度列表</RouterLink>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="detail-error">{{ errorMessage }}</p>

    <template v-if="scope">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">计划</span>
          <strong class="stat-value" data-testid="detail-plan-name">{{ scope.plan_name }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">当前状态</span>
          <strong class="stat-value">{{ scope.plan_status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">就绪</span>
          <strong class="stat-value" data-testid="detail-ready">{{ scope.ready ? '是' : '否' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">刷新分区数</span>
          <strong class="stat-value" data-testid="detail-partition-count">{{ scope.partition_count }}</strong>
        </article>
      </div>

      <template v-if="scope.blockers.length">
        <h3>未就绪原因</h3>
        <ul class="exchange-list error-text" data-testid="detail-blockers">
          <li v-for="(blocker, index) in scope.blockers" :key="index">
            <strong>{{ blocker.type }}</strong>：{{ blocker.message }}
          </li>
        </ul>
      </template>

      <h3>已核对的前置关系</h3>
      <table class="data-table" data-testid="predecessors">
        <thead><tr><th>前驱节点</th><th>节点</th><th>核对</th><th>来源版本</th><th>分区</th></tr></thead>
        <tbody>
          <tr v-for="item in scope.predecessors" :key="item.token">
            <td><code>{{ item.token }}</code></td>
            <td>{{ item.node_name }}</td>
            <td>{{ item.verified ? '已核' : '未核' }}</td>
            <td>{{ item.source_version }}</td>
            <td>{{ item.partitions.join('、') || '—' }}</td>
          </tr>
          <tr v-if="!scope.predecessors.length">
            <td colspan="5" class="empty-state">计划未登记可核的前驱节点</td>
          </tr>
        </tbody>
      </table>

      <h3>分区刷新范围（实际重算数据）</h3>
      <div class="exchange-grid">
        <dl class="exchange-detail" data-testid="partition-range">
          <dt>事实表</dt><dd>{{ scope.fact_table || '—' }}</dd>
          <dt>分区字段</dt><dd>{{ scope.partition_field }}</dd>
          <dt>刷新范围</dt>
          <dd>{{ scope.range.start || '—' }} ~ {{ scope.range.end || '—' }}</dd>
          <dt>分区列表</dt><dd>{{ scope.partitions.join('、') || '—' }}</dd>
          <dt>范围外旧分区</dt><dd>{{ scope.skipped_partitions.join('、') || '无' }}</dd>
          <dt>上游节点数</dt><dd>{{ scope.recompute.node_count }}</dd>
        </dl>
        <div class="exchange-card">
          <h3>上游节点与分区</h3>
          <table class="data-table" data-testid="upstream-closure">
            <thead><tr><th>节点 ID</th><th>节点</th><th>事实表</th><th>分区</th></tr></thead>
            <tbody>
              <tr v-for="node in scope.upstream_closure" :key="node.node_id">
                <td>{{ node.node_id }}</td>
                <td>{{ node.node_name }}</td>
                <td>{{ node.fact_table || '—' }}</td>
                <td>{{ node.partitions.join('、') || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <h3>血缘引用由来</h3>
      <p class="exchange-note">{{ scope.lineage_reference_origin }}</p>
      <table class="data-table" data-testid="lineage-references">
        <thead><tr><th>图版本</th><th>版本状态</th><th>来源系统</th><th>节点</th><th>引用类型</th><th>记录时间</th></tr></thead>
        <tbody>
          <tr v-for="ref in scope.lineage_references" :key="ref.version_id + ref.recorded_at">
            <td>{{ ref.version_id }}</td>
            <td>{{ ref.version_status }}</td>
            <td>{{ ref.source_system }}</td>
            <td>{{ ref.node_ids.join('、') }}</td>
            <td>{{ ref.reference_kind }}</td>
            <td>{{ ref.recorded_at }}</td>
          </tr>
          <tr v-if="!scope.lineage_references.length">
            <td colspan="6" class="empty-state">尚无载入的图版本引用该计划</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { loadPartitionScope } from '@/api/lineage-exchange'
import type { PartitionScope } from '@/data/lineage-exchange'

const route = useRoute()
const planId = ref(Number(route.params.planId ?? 6))
const scope = ref<PartitionScope | null>(null)
const errorMessage = ref('')

async function load() {
  errorMessage.value = ''
  try {
    const result = await loadPartitionScope(planId.value)
    if (!result.ok) throw new Error(result.message ?? '计划详情读取失败')
    scope.value = result
  } catch (error) {
    scope.value = null
    errorMessage.value = error instanceof Error ? error.message : '计划详情读取失败'
  }
}

watch(planId, load)
onMounted(load)
</script>

<style scoped>
.exchange-field.inline { display: flex; flex-direction: column; gap: 4px; }
.exchange-field.inline span { font-size: 12px; color: var(--muted); }
.exchange-field.inline input { width: 96px; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
.exchange-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.exchange-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.exchange-card h3 { margin: 0 0 8px; font-size: 14px; }
.exchange-detail { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: grid; grid-template-columns: 120px 1fr; gap: 6px 12px; margin: 0; }
.exchange-detail dt { color: var(--muted); font-size: 12px; }
.exchange-detail dd { margin: 0; font-size: 13px; }
.exchange-list { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px 10px 28px; font-size: 13px; }
.exchange-note { color: var(--muted); font-size: 12px; }
@media (max-width: 960px) { .exchange-grid { grid-template-columns: 1fr; } }
</style>
