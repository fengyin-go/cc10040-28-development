"""受控透视分析任务：把"准备行列"和"计算"拆成两个阶段，各自钉住授权 revision。

典型并发场景（也是必须能回答的问题）：
- 窗口一在旧授权下准备行列并抓到快照 r1；
- 管理员收紧门店级权限，生成 r2；
- 窗口二在 r2 下准备行列；
- 两个窗口分别计算时，各自只能看到自己快照对应的格值与小计，
  返回结构里带 ``auth_revision``、``snapshot_id`` 和 ``superseded``，
  因此可以确认"当前拿到的结果对应哪次授权"。

同一个分析员的两份快照互不影响，且服务端从不使用"当前最新授权"重算已准备
的任务，避免旧窗口被悄悄提升到新权限，或新窗口被旧结果污染。
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from app.services import governed_pivot
from app.services.access_control import AuthSnapshot, authorization_block, registry
from app.store import store

# 分析任务的阶段；只有 prepared 能进入 computed，computed 之后不可回退。
STAGE_PREPARED = "prepared"
STAGE_COMPUTED = "computed"
STAGE_REJECTED = "rejected"

_STAGE_LOCK = threading.RLock()


def _new_cell_id() -> str:
    return f"cell-{uuid.uuid4().hex[:12]}"


def prepare_cell(
    *,
    reader: str,
    row_dimension: str,
    col_dimension: str,
    aggregation: str = "SUM",
    revision: int | None = None,
    label: str = "",
) -> dict[str, Any]:
    """准备阶段：抓取授权快照并冻结本次分析使用的行列轴。

    revision 允许显式指定旧 revision，用于复现"窗口一仍停在准备阶段的旧视图"。
    """
    snapshot = registry.snapshot(reader, revision)
    if snapshot is None:
        return {"ok": False, "error": f"授权 revision {revision} 不存在"}

    cell_id = _new_cell_id()
    max_level = snapshot.max_level
    row_axis = governed_pivot.axis_state(row_dimension, max_level)
    col_axis = governed_pivot.axis_state(col_dimension, max_level)
    payload = {
        "cell_id": cell_id,
        "reader": reader,
        "label": label or cell_id,
        "stage": STAGE_PREPARED,
        "row_dimension": row_dimension,
        "col_dimension": col_dimension,
        "aggregation": str(aggregation or "SUM").upper(),
        "row_axis": row_axis,
        "column_axis": col_axis,
        "auth_revision": snapshot.revision,
        "snapshot_id": snapshot.snapshot_id,
        "grant_digest": snapshot.grant_digest,
        "authorization": authorization_block(snapshot),
        "result": None,
    }
    store.save_analysis_cell(cell_id, payload)
    return {
        "ok": True,
        "cell_id": cell_id,
        "stage": STAGE_PREPARED,
        "row_axis": row_axis,
        "column_axis": col_axis,
        "authorization": payload["authorization"],
    }


def _stage_transition(cell: dict[str, Any], target: str) -> tuple[bool, str]:
    """阶段流转只允许 prepared -> computed / rejected，且不可重复计算。

    调用方需持有 ``_STAGE_LOCK``，因此这里只做纯判断，不自行加锁。
    """
    current = cell.get("stage")
    if current == target:
        return False, f"分析任务已处于{target}阶段，不能重复计算"
    if current != STAGE_PREPARED:
        return False, f"分析任务当前阶段为{current}，不能流转到{target}"
    if target not in (STAGE_COMPUTED, STAGE_REJECTED):
        return False, f"未知目标阶段：{target}"
    return True, ""


def compute_cell(cell_id: str, *, revision: int | None = None) -> dict[str, Any]:
    """计算阶段：使用准备时冻结的快照生成受控透视网格。

    revision 可用于"按明确 revision 复核"：若指定且与准备时的 revision 不一致，
    直接拒绝，避免拿新授权重算旧窗口。
    """
    with _STAGE_LOCK:
        cell = store.get_analysis_cell(cell_id)
        if cell is None:
            return {"ok": False, "error": f"分析任务 {cell_id} 不存在"}

        if revision is not None and int(revision) != int(cell["auth_revision"]):
            return {
                "ok": False,
                "denied": True,
                "error": (
                    f"分析任务准备于第 {cell['auth_revision']} 次授权，"
                    f"不能按第 {revision} 次授权重算"
                ),
                "cell_id": cell_id,
                "auth_revision": cell["auth_revision"],
            }

        if cell["stage"] == STAGE_COMPUTED:
            return {
                "ok": True,
                "cell_id": cell_id,
                "stage": STAGE_COMPUTED,
                "deduplicated": True,
                "result": cell["result"],
                "authorization": cell["result"]["authorization"],
            }

        allowed, reason = _stage_transition(cell, STAGE_COMPUTED)
        if not allowed:
            return {"ok": False, "error": reason, "cell_id": cell_id}

        snapshot = registry.snapshot(cell["reader"], int(cell["auth_revision"]))
        if snapshot is None:
            cell["stage"] = STAGE_REJECTED
            cell["reject_reason"] = "准备时的授权 revision 已不可读取"
            store.save_analysis_cell(cell_id, cell)
            return {"ok": False, "error": cell["reject_reason"], "cell_id": cell_id}

        result = governed_pivot.compute_pivot_grid(
            row_dimension=cell["row_dimension"],
            col_dimension=cell["col_dimension"],
            aggregation=cell["aggregation"],
            snapshot=snapshot,
        )
        result["cell_id"] = cell_id
        result["stage"] = STAGE_COMPUTED
        result["prepared_at"] = cell.get("prepared_at")
        cell["stage"] = STAGE_COMPUTED
        cell["result"] = result
        store.save_analysis_cell(cell_id, cell)
        return {
            "ok": True,
            "cell_id": cell_id,
            "stage": STAGE_COMPUTED,
            "deduplicated": False,
            "result": result,
            "authorization": result["authorization"],
        }


def stage_cell(cell_id: str) -> dict[str, Any] | None:
    """返回任务当前阶段与授权归属摘要，不含任何格值。"""
    cell = store.get_analysis_cell(cell_id)
    if cell is None:
        return None
    snapshot = registry.snapshot(cell["reader"], int(cell["auth_revision"]))
    return {
        "cell_id": cell_id,
        "label": cell.get("label"),
        "stage": cell.get("stage"),
        "reader": cell.get("reader"),
        "row_dimension": cell.get("row_dimension"),
        "col_dimension": cell.get("col_dimension"),
        "row_axis": cell.get("row_axis"),
        "column_axis": cell.get("column_axis"),
        "auth_revision": cell.get("auth_revision"),
        "snapshot_id": cell.get("snapshot_id"),
        "authorization": authorization_block(snapshot),
        "has_result": cell.get("result") is not None,
    }


def cell_result(cell_id: str) -> dict[str, Any] | None:
    cell = store.get_analysis_cell(cell_id)
    if cell is None:
        return None
    snapshot = registry.snapshot(cell["reader"], int(cell["auth_revision"]))
    return {
        "cell_id": cell_id,
        "label": cell.get("label"),
        "stage": cell.get("stage"),
        "row_axis": cell.get("row_axis"),
        "column_axis": cell.get("column_axis"),
        "result": cell.get("result"),
        "authorization": authorization_block(snapshot),
    }


def verify_cell_drill(
    cell_id: str,
    *,
    row_value: str | None = None,
    col_value: str | None = None,
) -> dict[str, Any]:
    """对已计算任务做下钻核对，层级取自结果的声明，授权取自同一快照。"""
    cell = store.get_analysis_cell(cell_id)
    if cell is None:
        return {"ok": False, "error": f"分析任务 {cell_id} 不存在"}
    if cell["stage"] != STAGE_COMPUTED or not cell.get("result"):
        return {"ok": False, "error": "分析任务尚未计算，不能下钻"}

    result = cell["result"]
    row_axis = result.get("row_axis") or {}
    col_axis = result.get("column_axis") or {}
    max_level = int(result.get("authorization", {}).get("max_level") or 0)
    row_level = (
        int(row_axis.get("level") or max_level) if row_axis.get("open") else 0
    )
    col_level = (
        int(col_axis.get("level") or max_level) if col_axis.get("open") else 0
    )
    declared_level = max(row_level, col_level)

    snapshot = registry.snapshot(cell["reader"], int(cell["auth_revision"]))
    check = governed_pivot.verify_drill_down(
        row_dimension=cell["row_dimension"],
        col_dimension=cell["col_dimension"],
        aggregation=cell["aggregation"],
        snapshot=snapshot,
        result_level=declared_level,
        row_value=row_value,
        col_value=col_value,
    )
    check["cell_id"] = cell_id
    check["declared_level"] = declared_level
    check["declared_level_name"] = governed_pivot.level_name(declared_level)
    return check


def compare_windows(cell_ids: list[str]) -> dict[str, Any]:
    """对比同一分析员的两个窗口：各自对应哪次授权、值是否来自同一层。"""
    windows = []
    for cell_id in cell_ids:
        cell = store.get_analysis_cell(cell_id)
        if cell is None:
            windows.append({"cell_id": cell_id, "error": "不存在"})
            continue
        snapshot = registry.snapshot(cell["reader"], int(cell["auth_revision"]))
        result = cell.get("result") or {}
        windows.append(
            {
                "cell_id": cell_id,
                "label": cell.get("label"),
                "stage": cell.get("stage"),
                "reader": cell.get("reader"),
                "auth_revision": cell.get("auth_revision"),
                "snapshot_id": cell.get("snapshot_id"),
                "row_axis": cell.get("row_axis"),
                "column_axis": cell.get("column_axis"),
                "grant_digest": cell.get("grant_digest"),
                "grand_total": result.get("grand_total"),
                "visible_store_count": result.get("visible_store_count"),
                "authorization": authorization_block(snapshot),
            }
        )

    revisions = {
        int(item["auth_revision"])
        for item in windows
        if item.get("auth_revision") is not None
    }
    max_levels = {
        int(item["authorization"].get("max_level") or 0)
        for item in windows
        if item.get("authorization")
    }
    return {
        "windows": windows,
        "distinct_revisions": sorted(revisions),
        "distinct_levels": sorted(max_levels),
        "same_authorization": len(revisions) == 1,
        "note": (
            "两个窗口来自同一次授权"
            if len(revisions) == 1
            else "两个窗口来自不同授权，结果分别对应各自的 revision"
        ),
    }


def tighten_and_recheck(
    *,
    reader: str,
    max_level: int,
    scope: list[str] | None = None,
    actor: str = "管理员",
    note: str = "",
    before_cell_id: str | None = None,
    after_cell_id: str | None = None,
) -> dict[str, Any]:
    """收紧授权，并回看收紧前已计算的结果是否被标记为已取代。"""
    record, message = registry.tighten(
        reader, max_level, scope=scope, actor=actor, note=note
    )
    if record is None:
        return {"ok": False, "message": message}

    before = cell_result(before_cell_id) if before_cell_id else None
    after = cell_result(after_cell_id) if after_cell_id else None
    return {
        "ok": True,
        "message": message,
        "revision": record["revision"],
        "reader": reader,
        "before": before,
        "after": after,
        "trace": {
            "before_auth_revision": (
                before["authorization"]["auth_revision"] if before else None
            ),
            "after_auth_revision": (
                after["authorization"]["auth_revision"] if after else None
            ),
            "before_superseded": (
                before["authorization"]["superseded"] if before else None
            ),
            "before_still_reports_its_own_revision": (
                before["authorization"]["result_authorization"] if before else None
            ),
        },
    }


def reset_analysis_state() -> None:
    store.clear_analysis_cells()
