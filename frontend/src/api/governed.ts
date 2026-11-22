/** 受控透视工作台：读者、授权 revision、受控网格、双窗口与下钻核对的类型与请求。 */
import { fetchJson, request } from './client'

export type Axis = {
  requested: string
  effective: string
  requested_level: number | null
  level: number | null
  kind: string
  open: boolean
  clipped: boolean
  reason: string
}

export type Authorization = {
  reader: string | null
  auth_revision: number | null
  result_authorization: string
  max_level: number
  level_name: string
  grant_digest: string
  snapshot_id?: string
  current_revision: number
  superseded: boolean
  authorization_changed: boolean
  revision_advanced: boolean
  message: string
  grant?: { display_name?: string; max_level?: number; scope?: string[] }
}

export type GridCell = {
  row: string
  column: string
  value: number | null
  store_count: number
  suppressed?: boolean
}

export type GovernedMatrix = {
  ok: boolean
  denied?: boolean
  blocked?: boolean
  reason?: string
  metric: string
  aggregation: string
  row_axis: Axis
  column_axis: Axis
  grid: GridCell[]
  row_totals: Record<string, number | null>
  column_totals: Record<string, number | null>
  grand_total: number | null
  row_count?: number
  column_count?: number
  cell_count?: number
  visible_store_count?: number
  singleton_suppressed?: boolean
  suppressed_rows?: string[]
  suppressed_cell_count?: number
  row_store_counts?: Record<string, number>
  config_id?: number
  config_name?: string
  authorization: Authorization
}

export type ReaderGrant = {
  reader: string
  display_name: string
  max_level: number
  level_name: string
  scope: string[]
  grant_digest: string
}

export type Revision = {
  revision: number
  created_at: string
  actor: string
  note: string
  reader_count: number
}

export type AllowedDimension = {
  dimension: string
  level: number | null
  level_name: string
  granted: boolean
  allowed: boolean
  status: string
  effective: string
  reason: string
}

export type AllowedDimensions = {
  ok: boolean
  config_id: number
  config_name: string
  reader: string
  max_level: number
  max_level_name: string
  row_axis: Axis
  column_axis: Axis
  allowed_dimensions: AllowedDimension[]
  subtotal_levels: { level: number; name: string }[]
  authorization: Authorization
}

export type DrillCheck = {
  ok: boolean
  denied: boolean
  allowed: boolean
  reason: string
  authorization: Authorization
  result_level: number
  result_level_name: string
  expected_level: number
  expected_level_name: string
  consistent: boolean
  reasons: string[]
  detail_level?: string
  detail?: {
    level: number
    level_name: string
    value: string
    subtotal: number | null
    store_count: number
    suppressed?: boolean
  }[]
  detail_total?: number | null
  suppressed_values?: string[]
  singleton_suppressed?: boolean
  store_count?: number
}

export type CellStage = {
  cell_id: string
  label: string
  stage: string
  reader: string
  row_dimension: string
  col_dimension: string
  row_axis: Axis
  column_axis: Axis
  auth_revision: number
  snapshot_id: string
  has_result: boolean
  authorization: Authorization
}

export type PrepareResult = {
  ok: boolean
  cell_id?: string
  stage?: string
  error?: string
  row_axis?: Axis
  column_axis?: Axis
  authorization?: Authorization
}

export type ComputeResult = {
  ok: boolean
  cell_id?: string
  stage?: string
  deduplicated?: boolean
  error?: string
  denied?: boolean
  auth_revision?: number
  result?: GovernedMatrix
  authorization?: Authorization
}

export type ScenarioWindow = {
  cell_id: string
  prepared_axes: { row: Axis; column: Axis }
  authorization_at_prepare: Authorization
  result: GovernedMatrix
  drill: DrillCheck
}

export type ScenarioResult = {
  ok: boolean
  step?: string
  detail?: unknown
  config?: {
    id: number
    name: string
    row_dimension: string
    column_dimension: string
    aggregation: string
  }
  windows?: { a: ScenarioWindow; b: ScenarioWindow }
  tighten?: {
    ok: boolean
    message: string
    revision: number
    reader: string
    before: { authorization: Authorization; result: GovernedMatrix } | null
    trace: Record<string, unknown>
  }
  comparison?: {
    windows: {
      cell_id: string
      label: string
      stage: string
      auth_revision: number
      row_axis: Axis
      column_axis: Axis
      grand_total: number | null
      visible_store_count: number | null
      authorization: Authorization
    }[]
    distinct_revisions: number[]
    distinct_levels: number[]
    same_authorization: boolean
    note: string
  }
  dimensions_on_reentry?: AllowedDimensions
  trace?: Record<string, unknown>
}

export async function loadReaders(): Promise<{
  revision: number
  level_names: Record<string, string>
  readers: ReaderGrant[]
}> {
  return fetchJson('/api/governed/readers')
}

export async function loadRevisions(): Promise<{
  current_revision: number
  revisions: Revision[]
}> {
  return fetchJson('/api/governed/revisions')
}

export async function loadConfigMatrix(
  configId: number,
  reader: string,
  revision?: number | null,
): Promise<GovernedMatrix> {
  const params = new URLSearchParams({ reader })
  if (revision != null) params.set('revision', String(revision))
  return fetchJson(`/api/governed/pivot/${configId}/matrix?${params.toString()}`)
}

export async function loadAllowedDimensions(
  configId: number,
  reader: string,
  revision?: number | null,
): Promise<AllowedDimensions> {
  const params = new URLSearchParams({ reader })
  if (revision != null) params.set('revision', String(revision))
  return fetchJson(`/api/governed/pivot/${configId}/dimensions?${params.toString()}`)
}

export async function prepareCell(body: {
  reader: string
  row_dimension: string
  col_dimension: string
  aggregation: string
  revision?: number | null
  label?: string
}): Promise<PrepareResult> {
  const response = await request('/api/governed/cells/prepare', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as PrepareResult
}

export async function computeCell(
  cellId: string,
  revision?: number | null,
): Promise<ComputeResult> {
  const response = await request(`/api/governed/cells/${cellId}/compute`, {
    method: 'POST',
    body: JSON.stringify({ revision: revision ?? null }),
  })
  return (await response.json()) as ComputeResult
}

export async function drillCell(cellId: string): Promise<DrillCheck> {
  const response = await request(`/api/governed/cells/${cellId}/drill`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
  return (await response.json()) as DrillCheck
}

export async function tightenAuthorization(body: {
  reader: string
  max_level: number
  scope?: string[] | null
  note?: string
  before_cell_id?: string
  after_cell_id?: string
}): Promise<{ ok: boolean; message: string; revision?: number; trace?: Record<string, unknown> }> {
  const response = await request('/api/governed/authorizations/tighten', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return await response.json()
}

export async function runConcurrentScenario(body: {
  reader: string
  config_id: number
  tighten_level: number
  note?: string
}): Promise<ScenarioResult> {
  const response = await request('/api/governed/scenario/concurrent-tighten', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as ScenarioResult
}
