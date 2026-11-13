<template>
  <section class="page" data-module="cost">
    <header class="page-head">
      <div>
        <h2>存储成本优化管理</h2>
        <p class="page-desc">维护存储成本的扫描范围、冷数据识别与优化建议追踪，为存储成本优化提供可审计的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记成本分析报告</button>
        <button class="btn" type="button" @click="exportRows">导出存储成本优化清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="pendingKey === `${row.id}:${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无存储成本优化数据，可先登记成本分析报告</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条存储成本优化记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/cost'
const columns = ["报告编号", "分析范围", "存储总量", "冷数据占比", "目标分层", "优化建议", "预计节省", "执行结论", "执行状态", "生成时间"]
const actions = ["开始分析", "生成报告", "执行优化"]
const statuses = ["草稿", "分析中", "已生成", "已执行"]

const session = useSessionStore()
const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([
  { label: '成本分析报告总数', value: 0 },
  { label: '已生成报告', value: 0 },
  { label: '已执行报告', value: 0 },
])
const errorMessage = ref('')
const noticeMessage = ref('')
const pendingKey = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function currentQuery(): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value && value.trim()) params.set(key, value.trim())
  }
  return params.toString()
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  // 导出必须跟随当前筛选范围，打包下载的文件与列表来自同一口径。
  const query = currentQuery()
  const url = `${ENDPOINT}/export?format=zip${query ? `&${query}` : ''}`
  window.open(url, '_blank')
}

function openCreate() {
  errorMessage.value = '成本分析报告登记入口尚未接入审批流'
}

function newRequestId(): string {
  // 幂等键：同一次点击只生成一次，重复到达由后端按此键去重。
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  const key = `${String(row.id)}:${action}`
  if (pendingKey.value) return
  pendingKey.value = key
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          action,
          operator: session.operator,
          request_id: newRequestId(),
          expected_status: row.status ?? row['执行状态'],
        },
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? payload.detail ?? '存储成本优化动作未生效，请稍后重试')
    }
    noticeMessage.value = payload.message ?? `成本分析报告已${action}`
    await reload()
    await loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '存储成本优化操作失败'
  } finally {
    pendingKey.value = ''
  }
}

async function reload() {
  errorMessage.value = ''
  const query = currentQuery()
  try {
    const response = await request(`${ENDPOINT}${query ? `?${query}` : ''}`)
    if (!response.ok) {
      throw new Error('成本分析报告列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '存储成本优化列表读取失败'
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}?size=200`)
    if (!response.ok) return
    const payload = await response.json()
    const all: Row[] = payload.items ?? []
    stats.value = [
      { label: '成本分析报告总数', value: payload.total ?? all.length },
      { label: '已生成报告', value: all.filter((row) => row['执行状态'] === '已生成').length },
      { label: '已执行报告', value: all.filter((row) => row['执行状态'] === '已执行').length },
    ]
  } catch {
    // 统计卡片失败不阻塞列表
  }
}

onMounted(() => {
  void reload()
  void loadStats()
})
</script>
