<template>
  <section class="page governed-page" data-testid="governed-pivot">
    <header class="page-head">
      <div>
        <h2>受控透视</h2>
        <p class="page-desc">
          按读者授权层级生成透视网格：行轴、列轴、交叉格与合计使用同一层级裁剪，
          结果标明对应哪次授权。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="gov-refresh" type="button" @click="refreshAll">
          刷新授权与模型
        </button>
        <button class="btn primary" data-testid="gov-scenario" type="button" :disabled="busy" @click="runScenario">
          {{ busy ? '执行中…' : '运行双窗口收紧演示' }}
        </button>
      </div>
    </header>

    <div v-if="errorMessage" class="banner error-text" data-testid="gov-error">{{ errorMessage }}</div>

    <section class="gov-grid">
      <article class="gov-card">
        <h3>读者与授权层级</h3>
        <table class="data-table compact">
          <thead>
            <tr><th>读者</th><th>层级</th><th>可见范围</th><th>当前 revision</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="reader in readers"
              :key="reader.reader"
              :data-testid="`reader-${reader.reader}`"
              :class="{ selected: reader.reader === selectedReader }"
              @click="selectReader(reader.reader)"
            >
              <td>{{ reader.display_name }} <small>{{ reader.reader }}</small></td>
              <td>{{ reader.level_name }}（{{ reader.max_level }} 级）</td>
              <td>{{ reader.scope.join('、') }}</td>
              <td>{{ revision }}</td>
            </tr>
          </tbody>
        </table>

        <div class="gov-tighten">
          <label>
            <span>收紧到层级</span>
            <select v-model.number="tightenLevel" data-testid="gov-tighten-level">
              <option :value="3">门店</option>
              <option :value="2">区</option>
              <option :value="1">区域</option>
            </select>
          </label>
          <button class="btn" data-testid="gov-tighten" type="button" :disabled="busy" @click="tighten">
            收紧 {{ selectedReader }}
          </button>
        </div>

        <h3>授权 revision 历史</h3>
        <ul class="revision-list" data-testid="gov-revisions">
          <li v-for="item in revisions" :key="item.revision">
            <strong>r{{ item.revision }}</strong>
            <span>{{ item.created_at }} · {{ item.actor }}</span>
            <span>{{ item.note }}</span>
          </li>
        </ul>
      </article>

      <article class="gov-card">
        <h3>重新进入模型：核对获准维度</h3>
        <label class="inline-field">
          <span>透视配置</span>
          <select v-model.number="configId" data-testid="gov-config" @change="loadModel">
            <option v-for="config in configs" :key="config.id" :value="config.id">
              {{ config['配置名称'] }}
            </option>
          </select>
        </label>
        <div v-if="allowed" class="allowed-table">
          <p class="hint" data-testid="gov-allowed-summary">
            读者 <strong>{{ allowed.reader }}</strong> 可见
            <strong>{{ allowed.max_level_name }}</strong> 级；小计层级：
            {{ allowed.subtotal_levels.map((item) => item.name).join(' / ') }}
          </p>
          <table class="data-table compact">
            <thead><tr><th>维度</th><th>层级</th><th>是否获准</th><th>实际生效</th></tr></thead>
            <tbody>
              <tr v-for="dim in allowed.allowed_dimensions" :key="dim.dimension">
                <td>{{ dim.dimension }}</td>
                <td>{{ dim.level_name }}</td>
                <td :data-testid="`dim-allowed-${dim.dimension}`">{{ dim.status }}</td>
                <td>{{ dim.effective || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p class="hint" data-testid="gov-axis-summary">
            行轴：{{ allowed.row_axis.requested }} → {{ allowed.row_axis.effective || '未开放' }}
            （{{ allowed.row_axis.reason || '无需裁剪' }}）；
            列轴：{{ allowed.column_axis.requested }} → {{ allowed.column_axis.effective || '未开放' }}
            （{{ allowed.column_axis.reason || '无需裁剪' }}）
          </p>
        </div>
      </article>
    </section>

    <section class="gov-card" v-if="matrix">
      <h3>
        透视明细：{{ matrix.config_name || '即时网格' }}
        <span class="badge" data-testid="gov-matrix-auth">{{ matrix.authorization.result_authorization }}</span>
        <span class="badge level" data-testid="gov-matrix-level">{{ matrix.authorization.level_name }}</span>
      </h3>
      <p class="hint">{{ matrix.authorization.message }}</p>
      <p v-if="matrix.singleton_suppressed" class="hint" data-testid="gov-suppressed">
        已抑制单店分组：{{ (matrix.suppressed_rows || []).join('、') }} 只含一家门店，
        其格值、行小计、列小计与总计均不展示，避免反推门店值。
      </p>
      <div v-if="matrix.blocked || matrix.denied" class="banner error-text" data-testid="gov-matrix-blocked">
        {{ matrix.reason || '读者没有有效授权' }}
      </div>
      <div v-else class="matrix-scroll">
        <table class="data-table matrix">
          <thead>
            <tr>
              <th>{{ matrix.row_axis.effective }}</th>
              <th v-for="col in columnKeys" :key="col">{{ col }}</th>
              <th>行小计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rowKeys" :key="row">
              <td>{{ row }}</td>
              <td v-for="col in columnKeys" :key="col">{{ cellValue(row, col) }}</td>
              <td class="total" :data-testid="`gov-row-total-${row}`">{{ matrix.row_totals[row] ?? '—' }}</td>
            </tr>
            <tr v-if="suppressedKeys.length" class="suppressed-row">
              <td>已抑制单店</td>
              <td v-for="col in columnKeys" :key="col">—</td>
              <td>{{ suppressedKeys.join('、') }}</td>
            </tr>
            <tr class="total-row">
              <td>列小计</td>
              <td v-for="col in columnKeys" :key="col" :data-testid="`gov-col-total-${col}`">
                {{ matrix.column_totals[col] ?? '—' }}
              </td>
              <td class="total" data-testid="gov-grand-total">{{ matrix.grand_total ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="page-foot">
        <span>可见门店数：{{ matrix.visible_store_count ?? 0 }}</span>
        <span>授权摘要：{{ (matrix.authorization.grant_digest || '').slice(0, 12) }}…</span>
      </footer>
    </section>

    <section class="gov-card" v-if="scenario">
      <h3>双窗口与中途收紧门店权限</h3>
      <p class="hint" data-testid="gov-scenario-trace">
        窗口一结果来自 <strong>{{ scenario.trace?.window_a_result_authorization }}</strong>，
        窗口二结果来自 <strong>{{ scenario.trace?.window_b_result_authorization }}</strong>。
        {{ scenario.comparison?.note }}
      </p>
      <table class="data-table compact" data-testid="gov-window-compare">
        <thead>
          <tr>
            <th>窗口</th><th>阶段</th><th>准备时 revision</th><th>行轴</th><th>列轴</th>
            <th>总计</th><th>可见门店</th><th>是否被取代</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="window in scenario.comparison?.windows" :key="window.cell_id">
            <td>{{ window.label }}</td>
            <td>{{ window.stage }}</td>
            <td>{{ window.auth_revision }}</td>
            <td>{{ window.row_axis.effective }}</td>
            <td>{{ window.column_axis.effective }}</td>
            <td>{{ window.grand_total }}</td>
            <td>{{ window.visible_store_count }}</td>
            <td>{{ window.authorization.superseded ? '已被收紧取代' : '仍为当前授权' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="gov-columns">
        <article class="gov-panel" data-testid="gov-window-a">
          <h4>窗口一（收紧前准备）</h4>
          <p class="hint">{{ scenario.windows?.a.authorization_at_prepare.message }}</p>
          <table class="data-table compact">
            <tbody>
              <tr><th>行轴</th><td>{{ scenario.windows?.a.result.row_axis.effective }}</td></tr>
              <tr><th>列轴</th><td>{{ scenario.windows?.a.result.column_axis.effective }}</td></tr>
              <tr><th>总计</th><td>{{ scenario.windows?.a.result.grand_total }}</td></tr>
              <tr>
                <th>下钻核对</th>
                <td data-testid="gov-drill-a">
                  {{ scenario.windows?.a.drill.allowed ? '通过' : '拒绝' }} ·
                  声明 {{ scenario.windows?.a.drill.result_level_name }} /
                  期望 {{ scenario.windows?.a.drill.expected_level_name }}
                </td>
              </tr>
            </tbody>
          </table>
        </article>
        <article class="gov-panel" data-testid="gov-window-b">
          <h4>窗口二（收紧后准备）</h4>
          <p class="hint">{{ scenario.windows?.b.authorization_at_prepare.message }}</p>
          <table class="data-table compact">
            <tbody>
              <tr><th>行轴</th><td>{{ scenario.windows?.b.result.row_axis.effective }}</td></tr>
              <tr><th>列轴</th><td>{{ scenario.windows?.b.result.column_axis.effective }}</td></tr>
              <tr><th>总计</th><td>{{ scenario.windows?.b.result.grand_total }}</td></tr>
              <tr>
                <th>下钻核对</th>
                <td data-testid="gov-drill-b">
                  {{ scenario.windows?.b.drill.allowed ? '通过' : '拒绝' }} ·
                  声明 {{ scenario.windows?.b.drill.result_level_name }} /
                  期望 {{ scenario.windows?.b.drill.expected_level_name }}
                </td>
              </tr>
            </tbody>
          </table>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  loadAllowedDimensions,
  loadConfigMatrix,
  loadReaders,
  loadRevisions,
  runConcurrentScenario,
  tightenAuthorization,
} from '@/api/governed'
import type {
  AllowedDimensions,
  GovernedMatrix,
  ReaderGrant,
  Revision,
  ScenarioResult,
} from '@/api/governed'
import { listEntries } from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const readers = ref<ReaderGrant[]>([])
const revisions = ref<Revision[]>([])
const revision = ref(1)
const selectedReader = ref('reader_district')
const tightenLevel = ref(2)
const configs = ref<EntryRow[]>([])
const configId = ref(5)
const matrix = ref<GovernedMatrix | null>(null)
const allowed = ref<AllowedDimensions | null>(null)
const scenario = ref<ScenarioResult | null>(null)
const errorMessage = ref('')
const busy = ref(false)

const rowKeys = computed(() => Object.keys(matrix.value?.row_totals ?? {}))
const columnKeys = computed(() => Object.keys(matrix.value?.column_totals ?? {}))
const suppressedKeys = computed(() => matrix.value?.suppressed_rows ?? [])

function cellValue(row: string, col: string): number | string {
  const cell = matrix.value?.grid.find((item) => item.row === row && item.column === col)
  return cell?.value ?? '—'
}

async function refreshReaders() {
  const payload = await loadReaders()
  readers.value = payload.readers
  revision.value = payload.revision
  const history = await loadRevisions()
  revisions.value = history.revisions
}

async function loadConfigs() {
  const payload = await listEntries('pivot_table')
  configs.value = payload.items
  if (!configs.value.some((item) => Number(item.id) === configId.value)) {
    const usable = configs.value.find((item) => String(item['配置名称'] ?? '').includes('受控')) ?? configs.value[0]
    if (usable) configId.value = Number(usable.id)
  }
}

async function loadModel() {
  errorMessage.value = ''
  try {
    const [grid, allowedDims] = await Promise.all([
      loadConfigMatrix(configId.value, selectedReader.value),
      loadAllowedDimensions(configId.value, selectedReader.value),
    ])
    matrix.value = grid
    allowed.value = allowedDims
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '受控透视读取失败'
  }
}

async function selectReader(reader: string) {
  selectedReader.value = reader
  await loadModel()
}

async function tighten() {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await tightenAuthorization({
      reader: selectedReader.value,
      max_level: tightenLevel.value,
      note: '页面手动收紧',
    })
    if (!result.ok) throw new Error(result.message)
    await refreshReaders()
    await loadModel()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '收紧授权失败'
  } finally {
    busy.value = false
  }
}

async function runScenario() {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await runConcurrentScenario({
      reader: 'analyst_a',
      config_id: configId.value,
      tighten_level: 2,
      note: '复核期间收紧门店级权限',
    })
    if (!result.ok) throw new Error(`演示在第 ${result.step} 步失败`)
    scenario.value = result
    selectedReader.value = 'analyst_a'
    await refreshReaders()
    await loadModel()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '双窗口演示失败'
  } finally {
    busy.value = false
  }
}

async function refreshAll() {
  errorMessage.value = ''
  try {
    await refreshReaders()
    await loadConfigs()
    await loadModel()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新失败'
  }
}

onMounted(refreshAll)
</script>

<style scoped>
.gov-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.gov-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.gov-card h3 { margin: 0 0 8px; font-size: 14px; }
.gov-card h4 { margin: 0 0 6px; font-size: 13px; }
.data-table.compact th, .data-table.compact td { padding: 5px 8px; }
.selected td { background: #eef4ff; }
.gov-tighten { display: flex; gap: 10px; align-items: flex-end; margin: 10px 0; }
.gov-tighten label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); }
.gov-tighten select { padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; }
.revision-list { list-style: none; padding: 0; margin: 0; font-size: 12px; }
.revision-list li { display: flex; gap: 8px; padding: 4px 0; border-bottom: 1px solid #f0f2f5; }
.inline-field { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.inline-field select { padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; }
.hint { font-size: 12px; color: var(--muted); margin: 6px 0; }
.banner { border: 1px solid #fecaca; background: #fef2f2; border-radius: 6px; padding: 8px 10px; font-size: 12px; margin-bottom: 10px; }
.badge { display: inline-block; font-size: 11px; padding: 2px 8px; margin-left: 6px; border-radius: 999px; background: #eef4ff; color: #1f6feb; }
.badge.level { background: #ecfdf3; color: #15803d; }
.matrix-scroll { overflow-x: auto; }
.data-table.matrix td.total, .data-table.matrix .total-row .total { font-weight: 600; background: #f8fafc; }
.data-table.matrix .total-row td { background: #f8fafc; font-weight: 600; }
.suppressed-row td { color: #b42318; font-style: italic; }
.gov-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.gov-panel { border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.gov-panel th { width: 90px; }
</style>
