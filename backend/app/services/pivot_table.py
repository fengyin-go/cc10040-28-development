"""透视表配置领域服务：管理多维透视表的行列维度、聚合方式和共享配置。

跨模块数据流: drill_down -> pivot_table -> report_template
透视表消费下钻路径的维度结构，产出可嵌入报告模板的汇总视图。

除原有示例聚合外，透视配置还可以按读者授权生成受控透视网格：
行轴、列轴、交叉格与三个方向的小计统一服从同一层级裁剪。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.access_control import authorization_block, registry
from app.services.base import BaseModuleService
from app.services.governed_analysis import STAGE_PREPARED, prepare_cell
from app.services.governed_pivot import compute_pivot_grid


class PivotTableService(BaseModuleService):
    MODULE = "pivot_table"
    ENTITY = "透视配置"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["配置名称", "数据源指标", "行维度", "列维度", "聚合方式"]
    REQUIRED_FIELDS = ["配置名称", "数据源指标", "行维度"]
    STATUS_ORDER = ["草稿", "已保存", "已共享"]
    ACTION_RULES = {"保存配置": "已保存", "共享配置": "已共享"}
    NEGATIVE_ACTIONS = []

    def compute_pivot(self, entry_id: int) -> dict[str, Any] | None:
        """执行透视表计算，返回二维汇总数据。

        跨模块数据流: dimension_model -> drill_down -> pivot_table
        """
        config = store.find(self.MODULE, entry_id)
        if config is None:
            return {"error": f"透视配置 {entry_id} 不存在"}

        metric = config.get("数据源指标", "")
        row_dim = config.get("行维度", "")
        col_dim = config.get("列维度", "")
        agg_method = config.get("聚合方式", "COUNT")

        # 查找关联维度模型
        dim_models = store.find_by_field("dimension_model", "模型名称", metric)
        dimensions_available = []
        if dim_models:
            dimensions_available = [
                d.strip() for d in dim_models[0].get("维度列表", "").split(",") if d.strip()
            ]

        # 模拟透视表计算
        row_values = [f"{row_dim}_{i+1}" for i in range(3)]
        col_values = [f"{col_dim}_{j+1}" for j in range(3)]

        grid = []
        for rv in row_values:
            for cv in col_values:
                grid.append({
                    "row": rv,
                    "column": cv,
                    "value": 50 * (row_values.index(rv) + 1) * (col_values.index(cv) + 1),
                })

        # 计算行汇总
        row_totals = {}
        for rv in row_values:
            row_totals[rv] = sum(g["value"] for g in grid if g["row"] == rv)

        return {
            "config_name": config.get("配置名称"),
            "metric": metric,
            "row_dimension": row_dim,
            "column_dimension": col_dim,
            "aggregation": agg_method,
            "grid": grid,
            "row_totals": row_totals,
            "row_count": len(row_values),
            "column_count": len(col_values),
            "available_dimensions": dimensions_available,
        }

    def share_to_report(self, entry_id: int, report_template_id: int) -> dict[str, Any]:
        """将透视表共享到报告模板。

        跨模块数据流: pivot_table -> report_template
        """
        config = store.find(self.MODULE, entry_id)
        if config is None:
            return {"error": f"透视配置 {entry_id} 不存在"}

        report = store.find("report_template", report_template_id)
        if report is None:
            return {"error": f"报告模板 {report_template_id} 不存在"}

        # 计算透视结果
        pivot_result = self.compute_pivot(entry_id)
        if "error" in pivot_result:
            return pivot_result

        # 将透视结果注入报告模板
        report["透视表结果"] = pivot_result

        return {
            "pivot_name": config.get("配置名称"),
            "report_name": report.get("模板名称"),
            "status": "已关联",
            "pivot_summary": f"行={pivot_result.get('row_count')} x 列={pivot_result.get('column_count')}",
        }

    # ── 受控透视网格：按读者授权裁剪行列轴与小计 ──

    def matrix_for_config(
        self,
        entry_id: int,
        *,
        reader: str,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """按读者授权直接生成配置对应的受控透视网格。

        与 ``compute_pivot`` 的示例聚合分开，保证旧的报告链路不受授权影响。
        """
        config = store.find(self.MODULE, entry_id)
        if config is None:
            return {"ok": False, "error": f"透视配置 {entry_id} 不存在"}

        snapshot = registry.snapshot(reader, revision)
        result = compute_pivot_grid(
            row_dimension=str(config.get("行维度", "")),
            col_dimension=str(config.get("列维度", "")),
            aggregation=str(config.get("聚合方式", "SUM")),
            snapshot=snapshot,
        )
        result["config_id"] = entry_id
        result["config_name"] = config.get("配置名称")
        if snapshot is None:
            result["denied"] = True
            result["reason"] = f"授权 revision {revision} 不存在"
            result["authorization"] = authorization_block(None)
        return result

    def prepare_governed_matrix(
        self,
        entry_id: int,
        *,
        reader: str,
        revision: int | None = None,
        label: str = "",
    ) -> dict[str, Any]:
        """登记一次准备阶段：冻结本窗口使用的授权快照与行列轴。

        跨模块数据流: pivot_table -> governed_analysis -> access_control
        """
        config = store.find(self.MODULE, entry_id)
        if config is None:
            return {"ok": False, "error": f"透视配置 {entry_id} 不存在"}
        prepared = prepare_cell(
            reader=reader,
            row_dimension=str(config.get("行维度", "")),
            col_dimension=str(config.get("列维度", "")),
            aggregation=str(config.get("聚合方式", "SUM")),
            revision=revision,
            label=label or f"{config.get('配置名称')}",
        )
        if prepared.get("ok"):
            prepared["config_id"] = entry_id
            prepared["config_name"] = config.get("配置名称")
            prepared["stage"] = STAGE_PREPARED
        return prepared

    def allowed_dimensions(
        self,
        entry_id: int,
        *,
        reader: str,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """重新进入模型时，列出该读者真正获准的行列维度与小计层级。"""
        from app.services import analytics_facts as facts
        from app.services.governed_pivot import axis_state

        config = store.find(self.MODULE, entry_id)
        if config is None:
            return {"ok": False, "error": f"透视配置 {entry_id} 不存在"}
        snapshot = registry.snapshot(reader, revision)
        max_level = snapshot.max_level if snapshot is not None else 0

        row_axis = axis_state(str(config.get("行维度", "")), max_level)
        col_axis = axis_state(str(config.get("列维度", "")), max_level)

        allowed: list[dict[str, Any]] = []
        for dim in [*facts.LEVEL_DIMENSIONS.values(), facts.CATEGORY_DIMENSION]:
            state = axis_state(dim, max_level)
            level = facts.dimension_level(dim)
            # granted：读者在维度自身层级上获准；usable：可开放（可能被裁剪到更粗层级）。
            granted = state["open"] and not state["clipped"]
            allowed.append(
                {
                    "dimension": dim,
                    "level": level,
                    "level_name": (
                        facts.LEVEL_NAMES[level] if level else "维度"
                    ),
                    "granted": granted,
                    "allowed": state["open"],
                    "status": (
                        "获准"
                        if granted
                        else ("裁剪至更粗层级" if state["open"] else "未获准")
                    ),
                    "effective": state["effective"],
                    "reason": state["reason"],
                }
            )

        return {
            "ok": True,
            "config_id": entry_id,
            "config_name": config.get("配置名称"),
            "reader": reader,
            "max_level": max_level,
            "max_level_name": (
                facts.LEVEL_NAMES.get(max_level, "未授权") if max_level else "未授权"
            ),
            "row_axis": row_axis,
            "column_axis": col_axis,
            "allowed_dimensions": allowed,
            "subtotal_levels": [
                {"level": level, "name": name}
                for level, name in facts.LEVEL_NAMES.items()
                if level <= max_level
            ],
            "authorization": authorization_block(snapshot),
        }