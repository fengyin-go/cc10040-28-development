"""受控透视路由：授权快照、受控网格、双窗口计算与下钻核对。

路由层只做参数转发，层级裁剪、快照钉住与阶段流转都在领域服务里完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import analytics_facts as facts
from app.services.access_control import (
    LEVEL_NAMES,
    authorization_block,
    registry,
)
from app.services.governed_analysis import (
    cell_result,
    compare_windows,
    compute_cell,
    prepare_cell,
    reset_analysis_state,
    stage_cell,
    tighten_and_recheck,
    verify_cell_drill,
)
from app.services.governed_pivot import compute_pivot_grid
from app.services.pivot_table import PivotTableService

router = APIRouter(prefix="/api/governed", tags=["受控透视"])


class TightenPayload(BaseModel):
    reader: str
    max_level: int
    scope: list[str] | None = None
    actor: str = "管理员"
    note: str = ""
    before_cell_id: str | None = None
    after_cell_id: str | None = None


class PreparePayload(BaseModel):
    reader: str
    row_dimension: str
    col_dimension: str = ""
    aggregation: str = "SUM"
    revision: int | None = None
    label: str = ""


class ComputePayload(BaseModel):
    revision: int | None = None


class DrillPayload(BaseModel):
    row_value: str | None = None
    col_value: str | None = None


class ConfigPreparePayload(BaseModel):
    reader: str
    revision: int | None = None
    label: str = ""


class ScenarioPayload(BaseModel):
    reader: str = "analyst_a"
    config_id: int = 5
    tighten_level: int = 2
    actor: str = "管理员"
    note: str = "复核期间收紧门店级权限"
    scope: list[str] | None = Field(default=None)


@router.get("/readers")
def list_readers() -> dict[str, Any]:
    """当前授权下每位读者可见的层级与范围。"""
    return {
        "revision": registry.current_revision(),
        "level_names": LEVEL_NAMES,
        "readers": registry.readers(),
    }


@router.get("/revisions")
def list_revisions() -> dict[str, Any]:
    """授权 revision 历史；收紧操作只追加，不回写历史。"""
    return {
        "current_revision": registry.current_revision(),
        "revisions": registry.revisions(),
    }


@router.get("/revisions/{revision}")
def get_revision(revision: int) -> dict[str, Any]:
    record = registry.get_revision(revision)
    if record is None:
        raise HTTPException(status_code=404, detail=f"授权 revision {revision} 不存在")
    return record


@router.get("/fact-levels")
def fact_levels() -> dict[str, Any]:
    """事实表的层级定义，供前端展示"权限对应的层级"。"""
    return {
        "metric": facts.METRIC_NAME,
        "dimensions": [
            {"name": name, "level": level}
            for level, name in facts.LEVEL_DIMENSIONS.items()
        ] + [{"name": facts.CATEGORY_DIMENSION, "level": None}],
        "level_names": facts.LEVEL_NAMES,
    }


@router.post("/authorizations/tighten")
def tighten_authorization(payload: TightenPayload) -> dict[str, Any]:
    """收紧某位读者的层级或范围，并回看收紧前后的结果归属。"""
    return tighten_and_recheck(
        reader=payload.reader,
        max_level=payload.max_level,
        scope=payload.scope,
        actor=payload.actor,
        note=payload.note,
        before_cell_id=payload.before_cell_id,
        after_cell_id=payload.after_cell_id,
    )


@router.get("/matrix")
def ad_hoc_matrix(
    reader: str = Query(...),
    row_dimension: str = Query(default="区"),
    col_dimension: str = Query(default="品类"),
    aggregation: str = Query(default="SUM"),
    revision: int | None = Query(default=None),
) -> dict[str, Any]:
    """按读者授权即时生成受控透视网格（不落分析任务）。"""
    snapshot = registry.snapshot(reader, revision)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"授权 revision {revision} 不存在")
    return compute_pivot_grid(
        row_dimension=row_dimension,
        col_dimension=col_dimension,
        aggregation=aggregation,
        snapshot=snapshot,
    )


@router.get("/pivot/{config_id}/matrix")
def config_matrix(
    config_id: int,
    reader: str = Query(...),
    revision: int | None = Query(default=None),
) -> dict[str, Any]:
    """用透视配置的行列轴生成受控网格。"""
    return PivotTableService().matrix_for_config(
        config_id, reader=reader, revision=revision
    )


@router.get("/pivot/{config_id}/dimensions")
def config_dimensions(
    config_id: int,
    reader: str = Query(...),
    revision: int | None = Query(default=None),
) -> dict[str, Any]:
    """重新进入模型时核对：获准的维度与小计层级。"""
    return PivotTableService().allowed_dimensions(
        config_id, reader=reader, revision=revision
    )


@router.post("/pivot/{config_id}/prepare")
def config_prepare(config_id: int, payload: ConfigPreparePayload) -> dict[str, Any]:
    return PivotTableService().prepare_governed_matrix(
        config_id,
        reader=payload.reader,
        revision=payload.revision,
        label=payload.label,
    )


@router.post("/cells/prepare")
def prepare(payload: PreparePayload) -> dict[str, Any]:
    """准备阶段：冻结行列轴与授权快照，返回 cell_id 供后续计算。"""
    return prepare_cell(
        reader=payload.reader,
        row_dimension=payload.row_dimension,
        col_dimension=payload.col_dimension,
        aggregation=payload.aggregation,
        revision=payload.revision,
        label=payload.label,
    )


@router.post("/cells/{cell_id}/compute")
def compute(cell_id: str, payload: ComputePayload | None = None) -> dict[str, Any]:
    """计算阶段：只用准备时冻结的授权快照，绝不悄悄改用最新授权。"""
    revision = payload.revision if payload is not None else None
    return compute_cell(cell_id, revision=revision)


@router.get("/cells/compare")
def compare(cell_ids: str = Query(..., description="逗号分隔的 cell_id 列表")) -> dict[str, Any]:
    ids = [item.strip() for item in cell_ids.split(",") if item.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="至少提供一个 cell_id")
    return compare_windows(ids)


@router.get("/cells/{cell_id}")
def get_cell(cell_id: str) -> dict[str, Any]:
    stage = stage_cell(cell_id)
    if stage is None:
        raise HTTPException(status_code=404, detail=f"分析任务 {cell_id} 不存在")
    return stage


@router.get("/cells/{cell_id}/result")
def get_cell_result(cell_id: str) -> dict[str, Any]:
    result = cell_result(cell_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"分析任务 {cell_id} 不存在")
    return result


@router.post("/cells/{cell_id}/drill")
def drill(cell_id: str, payload: DrillPayload | None = None) -> dict[str, Any]:
    """下钻核对：按结果声明的层级与同一授权快照重算归属。"""
    values = payload or DrillPayload()
    return verify_cell_drill(
        cell_id, row_value=values.row_value, col_value=values.col_value
    )


@router.post("/scenario/concurrent-tighten")
def concurrent_tighten_scenario(payload: ScenarioPayload) -> dict[str, Any]:
    """演示并复现"两窗口 + 中途收紧门店权限"的完整链路。

    步骤固定、全部带 revision 标记，便于审计：
    1. 窗口一在旧授权准备并计算；
    2. 管理员收紧门店级权限，生成新 revision；
    3. 窗口二在新授权准备并计算；
    4. 两个窗口对比，并回看窗口一是否被标为已取代。
    """
    config = PivotTableService().get_entry(payload.config_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"透视配置 {payload.config_id} 不存在")

    row_dimension = str(config.get("行维度", ""))
    col_dimension = str(config.get("列维度", ""))
    aggregation = str(config.get("聚合方式", "SUM"))

    window_a = prepare_cell(
        reader=payload.reader,
        row_dimension=row_dimension,
        col_dimension=col_dimension,
        aggregation=aggregation,
        label="窗口一（准备于收紧前）",
    )
    if not window_a.get("ok"):
        return {"ok": False, "step": "prepare_window_a", "detail": window_a}
    computed_a = compute_cell(window_a["cell_id"])
    if not computed_a.get("ok"):
        return {"ok": False, "step": "compute_window_a", "detail": computed_a}

    tighten = tighten_and_recheck(
        reader=payload.reader,
        max_level=payload.tighten_level,
        scope=payload.scope,
        actor=payload.actor,
        note=payload.note,
        before_cell_id=window_a["cell_id"],
    )
    if not tighten.get("ok"):
        return {"ok": False, "step": "tighten", "detail": tighten}

    window_b = prepare_cell(
        reader=payload.reader,
        row_dimension=row_dimension,
        col_dimension=col_dimension,
        aggregation=aggregation,
        label="窗口二（准备于收紧后）",
    )
    if not window_b.get("ok"):
        return {"ok": False, "step": "prepare_window_b", "detail": window_b}
    computed_b = compute_cell(window_b["cell_id"])
    if not computed_b.get("ok"):
        return {"ok": False, "step": "compute_window_b", "detail": computed_b}

    comparison = compare_windows([window_a["cell_id"], window_b["cell_id"]])
    drill_a = verify_cell_drill(window_a["cell_id"])
    drill_b = verify_cell_drill(window_b["cell_id"])
    dimensions = PivotTableService().allowed_dimensions(
        payload.config_id, reader=payload.reader
    )

    # 场景在收紧之后才返回，窗口一的授权块要按当前 head 重新评估，
    # 这样调用方看到的就是"第 1 次授权、已被取代"，而不是计算当时的旧标记。
    def _fresh(cell_id: str) -> dict[str, Any]:
        fresh = cell_result(cell_id) or {}
        result = dict(fresh.get("result") or {})
        if fresh.get("authorization"):
            result["authorization"] = fresh["authorization"]
        return result

    result_a = _fresh(window_a["cell_id"])
    result_b = _fresh(window_b["cell_id"])

    return {
        "ok": True,
        "config": {
            "id": payload.config_id,
            "name": config.get("配置名称"),
            "row_dimension": row_dimension,
            "column_dimension": col_dimension,
            "aggregation": aggregation,
        },
        "windows": {
            "a": {
                "cell_id": window_a["cell_id"],
                "prepared_axes": {
                    "row": window_a["row_axis"],
                    "column": window_a["column_axis"],
                },
                "authorization_at_prepare": window_a["authorization"],
                "result": result_a,
                "drill": drill_a,
            },
            "b": {
                "cell_id": window_b["cell_id"],
                "prepared_axes": {
                    "row": window_b["row_axis"],
                    "column": window_b["column_axis"],
                },
                "authorization_at_prepare": window_b["authorization"],
                "result": result_b,
                "drill": drill_b,
            },
        },
        "tighten": tighten,
        "comparison": comparison,
        "dimensions_on_reentry": dimensions,
        "trace": {
            "window_a_auth_revision": result_a["authorization"]["auth_revision"],
            "window_b_auth_revision": result_b["authorization"]["auth_revision"],
            "window_a_superseded": result_a["authorization"]["superseded"],
            "window_a_result_authorization": result_a["authorization"][
                "result_authorization"
            ],
            "window_b_result_authorization": result_b["authorization"][
                "result_authorization"
            ],
        },
    }


@router.post("/reset")
def reset_demo_state() -> dict[str, Any]:
    """把授权与分析任务恢复到初始快照（本地演示/测试用）。"""
    registry.reset()
    reset_analysis_state()
    return {
        "ok": True,
        "revision": registry.current_revision(),
        "readers": registry.readers(),
        "authorization": authorization_block(registry.snapshot("reader_region")),
    }
