# 多维分析查询与指标平台

支撑多维分析查询、指标计算、口径管理和数据服务发布的数据应用平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8015/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5195/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8015`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_definition` | 指标 | 指标名称、计算口径、统计粒度 |
| 维度建模 | `dimension_model` | 维度模型 | 模型名称、事实表、维度列表 |
| 查询构建器 | `query_builder` | 查询模板 | 模板名称、关联指标、维度组合 |
| 数据服务 | `data_service` | 服务接口 | 接口名称、关联指标或模型、请求方式 |
| 口径版本 | `caliber_version` | 口径版本 | 所属指标、版本号、口径描述 |
| 查询日志 | `query_journal` | 查询日志 | 查询时间、操作人、涉及指标 |
| 下钻分析 | `drill_down` | 下钻路径 | 路径名称、起始指标、下钻维度序列 |
| 报告模板 | `report_template` | 报告模板 | 模板名称、包含指标集、报告周期 |
| 实验分群 | `experiment_group` | 分群规则 | 规则名称、筛选条件、群组大小 |
| 数据看板配置 | `data_dashboard` | 看板配置 | 看板名称、包含图表、刷新间隔 |
| 用户分群 | `user_cohort` | 分群结果 | 分群名称、分群条件、群组大小 |
| 异常检测器 | `anomaly_detector` | 检测规则 | 规则名称、目标指标、检测算法 |
| 数据导出服务 | `data_export_service` | 导出任务 | 任务名称、数据范围、目标格式 |
| 分群对比分析 | `segment_compare` | 对比任务 | 对比名称、分群A、分群B |
| 透视表配置 | `pivot_table` | 透视配置 | 配置名称、数据源指标、行维度 |
| 数据订阅 | `data_subscription` | 订阅任务 | 订阅名称、关联指标、推送条件 |
| 查询缓存层 | `cache_layer` | 缓存策略 | 策略名称、关联查询模板、缓存时长 |
| 数据追溯系统 | `trace_system` | 追溯链条 | 追溯ID、起点指标、中间步骤 |
| 口径重算引擎 | `recalc_engine` | 重算任务 | 关联指标、新口径版本、历史时间范围 |
| 字段血缘 | `field_lineage` | 字段血缘 | 目标字段、来源字段列表、转换逻辑 |

## 受控透视（按读者授权限制上卷/下钻）

透视表配置除保存行列轴外，还能按读者授权生成受控透视网格。核心约束：

- 事实表按 `区域 / 区 / 门店` 三级地理层级组织，读者授权为
  `最大可见层级 + 可见范围`，层级数字越大越细（1=区域，2=区，3=门店）。
- **统一裁剪**：行轴、列轴、交叉格、行/列小计和总计都基于同一份已授权事实行
  计算。请求超出层级的轴会被裁剪成读者可见的层级，未配置/未开放的轴不产生任何
  格值，避免用交叉格反推门店值。
- **授权 revision**：每次收紧授权都会追加一个新 revision，历史 revision 原样保留。
  分析任务分为"准备行列"和"计算"两个阶段，准备阶段钉住当时的授权快照，计算阶段
  绝不改用最新授权。结果里带 `auth_revision`、`snapshot_id` 和 `superseded`，
  因此同一分析员的两个窗口能分别自证对应哪次授权。
- **下钻核对**：下钻按结果声明的层级重算，层级必须等于授权裁剪后的层级；越权或
  层级不符一律拒绝，且不返回任何门店级明细。

后端入口（`backend/app/services/`）：

| 文件 | 职责 |
| --- | --- |
| `access_control.py` | 授权 revision 注册表、不可变 `AuthSnapshot`、收紧校验 |
| `analytics_facts.py` | 门店×品类事实表与层级聚合 |
| `governed_pivot.py` | 受控透视网格、未开放轴封锁、下钻核对 |
| `governed_analysis.py` | 准备/计算两阶段分析任务、双窗口对比 |
| `routers/governed.py` | `/api/governed/*` 接口 |

常用接口：

```bash
# 当前读者与层级
curl http://127.0.0.1:8015/api/governed/readers
# 按读者授权生成受控网格（门店轴会被裁剪到区）
curl "http://127.0.0.1:8015/api/governed/matrix?reader=reader_district&row_dimension=门店&col_dimension=品类"
# 重新进入模型，核对获准维度与小计层级
curl "http://127.0.0.1:8015/api/governed/pivot/5/dimensions?reader=reader_district"
# 双窗口 + 中途收紧门店权限的完整演示
curl -X POST http://127.0.0.1:8015/api/governed/scenario/concurrent-tighten \
  -H 'Content-Type: application/json' \
  -d '{"reader":"analyst_a","config_id":5,"tighten_level":2}'
```

前端在左侧导航的「受控透视」（`/governed-pivot`）页面展示读者层级、受控网格、
revision 历史，以及双窗口收紧演示和下钻核对结果。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
