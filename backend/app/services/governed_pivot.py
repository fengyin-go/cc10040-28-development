"""受控透视网格：从事实表维度数据生成按授权层级裁剪的透视表与小计。

规则集中在一处，保证行轴、列轴、交叉格和合计口径一致：

1. 行列轴各自裁剪到读者可见层级（``clip_dimension``），超出层级的轴视为未开放；
2. 只有"两轴都开放"的交叉格才允许出现，未开放轴的任何组合都不生成格值；
3. 行小计 / 列小计 / 总计都在同一份可见事实行上求和，因此与交叉格自洽；
4. 下钻核对按"结果声明的层级"重算，字号（门店级）结果用更细的授权拿不到；
5. 结果用不可变授权快照打标，收紧授权后旧结果可自证对应哪次授权。
"""
from __future__ import annotations

from typing import Any

from app.services import analytics_facts as facts
from app.services.access_control import AuthSnapshot, authorization_block


def axis_state(
    requested: str, max_level: int
) -> dict[str, Any]:
    requested = str(requested or "").strip()
    if not requested:
        return {
            "requested": requested,
            "effective": "",
            "level": None,
            "kind": "未配置",
            "open": False,
            "clipped": False,
            "reason": "轴未配置",
        }
    level = facts.dimension_level(requested)
    if level is None:
        # 非地理维度（如品类）不做层级裁剪，始终开放。
        return {
            "requested": requested,
            "effective": requested,
            "requested_level": None,
            "level": None,
            "kind": "维度",
            "open": True,
            "clipped": False,
            "reason": "",
        }
    effective, clipped = facts.clip_dimension(requested, max_level)
    if not effective:
        return {
            "requested": requested,
            "effective": "",
            "requested_level": level,
            "level": None,
            "kind": "地理",
            "open": False,
            "clipped": True,
            "reason": f"读者仅有{level_name(max_level)}级授权，无法开放{requested}轴",
        }
    if clipped:
        return {
            "requested": requested,
            "effective": effective,
            "requested_level": level,
            "level": facts.dimension_level(effective),
            "kind": "地理",
            "open": True,
            "clipped": True,
            "reason": (
                f"{requested}轴超出{level_name(max_level)}级授权，"
                f"已裁剪为{effective}级"
            ),
        }
    return {
        "requested": requested,
        "effective": effective,
        "requested_level": level,
        "level": level,
        "kind": "地理",
        "open": True,
        "clipped": False,
        "reason": "",
    }


def level_name(level: int) -> str:
    return facts.LEVEL_NAMES.get(int(level), "未授权")


def _effective_axis_level(axis: dict[str, Any], max_level: int) -> int:
    """轴实际展示数据所用的层级；未配置轴视为此快照的最细可见层级。"""
    if axis["level"] is None:
        return int(max_level)
    return int(axis["level"])


def _resolve_max_level(snap: AuthSnapshot | None) -> int:
    if snap is None or snap.grant is None:
        return 0
    return int(snap.max_level)


def _authorize_scope(snap: AuthSnapshot | None) -> list[str]:
    if snap is None or snap.grant is None:
        return []
    return list(snap.scope)


def compute_pivot_grid(
    *,
    row_dimension: str,
    col_dimension: str,
    aggregation: str = "SUM",
    snapshot: AuthSnapshot | None,
) -> dict[str, Any]:
    """按授权快照生成受控透视网格。

    snapshot 为 None 或未授权读者时返回拒绝结构；调用方不应在拒绝后继续展示数据。
    """
    max_level = _resolve_max_level(snapshot)
    scope = _authorize_scope(snapshot)
    auth = authorization_block(snapshot)

    if max_level < 1 or not scope:
        return {
            "ok": False,
            "denied": True,
            "reason": "读者没有有效授权，无法生成透视网格",
            "metric": facts.METRIC_NAME,
            "aggregation": aggregation,
            "row_axis": axis_state(row_dimension, max_level),
            "column_axis": axis_state(col_dimension, max_level),
            "grid": [],
            "row_totals": {},
            "column_totals": {},
            "grand_total": None,
            "authorization": auth,
        }

    row_axis = axis_state(row_dimension, max_level)
    col_axis = axis_state(col_dimension, max_level)

    # 全量的事实只能先按范围裁剪；层级裁剪通过"轴的实际维度"体现。
    scoped_facts = facts.scope_filtered(facts.fact_rows(), scope)

    if not row_axis["open"] or not col_axis["open"]:
        # 任一轴未开放：不产生任何格值或小计，防止用交叉格反推门店值。
        blocked_axis = row_axis if not row_axis["open"] else col_axis
        return {
            "ok": True,
            "metric": facts.METRIC_NAME,
            "aggregation": aggregation,
            "row_axis": row_axis,
            "column_axis": col_axis,
            "grid": [],
            "row_totals": {},
            "column_totals": {},
            "grand_total": None,
            "row_count": 0,
            "column_count": 0,
            "cell_count": 0,
            "visible_store_count": 0,
            "blocked": True,
            "reason": blocked_axis["reason"],
            "authorization": auth,
        }

    row_key = row_axis["effective"]
    col_key = col_axis["effective"]

    # 先按行的可见层级聚合，再按列的可见层级聚合，两者都只接触已授权事实。
    rows = facts.distinct_values(scoped_facts, row_key)
    cols = facts.distinct_values(scoped_facts, col_key)

    grid: list[dict[str, Any]] = []
    row_totals: dict[str, int | float | None] = {}
    col_totals: dict[str, int | float | None] = {}
    grand_total = facts.aggregate(scoped_facts, aggregation)

    def _store_count(row_value: str) -> int:
        return len(facts.distinct_stores(
            [r for r in scoped_facts if str(r.get(row_key, "")) == row_value]
        ))

    # 单店抑制：只含一家门店的分组与门店值一一对应。即使读者看不到门店轴，
    # 也能从这样的行小计/交叉格反推该店，所以按统计披露控制的做法：
    # 1) 单店格不展示值；2) 含单店格的整行小计不展示；
    # 3) 只要存在任何单店分组，列小计与总计一并省略，避免"总计 - 可见项"反推。
    # 门店级读者本身有权看门店，不做抑制。
    apply_suppression = max_level < facts.GEO_LEVELS["门店"]

    suppressed_cells = 0
    allowed_rows: list[str] = []
    grid: list[dict[str, Any]] = []
    row_totals: dict[str, int | float | None] = {}
    row_store_counts: dict[str, int] = {}

    for row_value in rows:
        row_facts = [r for r in scoped_facts if str(r.get(row_key, "")) == row_value]
        store_count = len(facts.distinct_stores(row_facts))
        row_store_counts[row_value] = store_count
        row_has_suppressed = False
        if not apply_suppression or store_count >= 2:
            allowed_rows.append(row_value)

        for col_value in cols:
            cell_facts = [
                r for r in row_facts if str(r.get(col_key, "")) == col_value
            ]
            if not cell_facts:
                continue
            cell_stores = len(facts.distinct_stores(cell_facts))
            cell_suppressed = apply_suppression and cell_stores < 2
            if cell_suppressed:
                suppressed_cells += 1
                row_has_suppressed = True
            grid.append(
                {
                    "row": row_value,
                    "column": col_value,
                    "value": (
                        None
                        if cell_suppressed
                        else facts.display_number(facts.aggregate(cell_facts, aggregation))
                    ),
                    "store_count": cell_stores,
                    "suppressed": cell_suppressed,
                }
            )

        row_value_safe = store_count >= 2 or not apply_suppression
        if row_value_safe and not row_has_suppressed:
            row_totals[row_value] = facts.display_number(
                facts.aggregate(row_facts, aggregation)
            )

    total_safe = not apply_suppression

    suppressed_rows = [
        value for value in rows if apply_suppression and row_store_counts[value] < 2
    ]
    any_suppression = bool(suppressed_rows) or suppressed_cells > 0
    col_totals: dict[str, int | float | None] = {}
    for col_value in cols:
        col_facts = [r for r in scoped_facts if str(r.get(col_key, "")) == col_value]
        if not any_suppression:
            # 没有单店分组时列小计不含单店贡献，可安全展示。
            col_totals[col_value] = facts.display_number(
                facts.aggregate(col_facts, aggregation)
            )

    return {
        "ok": True,
        "metric": facts.METRIC_NAME,
        "aggregation": str(aggregation or "SUM").upper(),
        "row_axis": row_axis,
        "column_axis": col_axis,
        "grid": grid,
        "row_totals": row_totals,
        "column_totals": col_totals,
        "grand_total": (
            facts.display_number(grand_total) if not any_suppression else None
        ),
        "row_count": len(allowed_rows),
        "column_count": len(cols),
        "cell_count": len(grid),
        "visible_store_count": len(facts.distinct_stores(scoped_facts)),
        "singleton_suppressed": any_suppression,
        "suppressed_rows": sorted(suppressed_rows),
        "suppressed_cell_count": suppressed_cells,
        "row_store_counts": row_store_counts,
        "blocked": False,
        "authorization": auth,
    }


def verify_drill_down(
    *,
    row_dimension: str,
    col_dimension: str,
    aggregation: str,
    snapshot: AuthSnapshot | None,
    result_level: int,
    row_value: str | None = None,
    col_value: str | None = None,
) -> dict[str, Any]:
    """下钻核对：按结果声明的层级重新裁剪，并使用同一份授权快照重算。

    关键点：
    - 结果层级必须等于两轴裁剪后的实际层级；不一致说明结果与授权不符。
    - 重算仍用传入的 snapshot，而不是"当前最新授权"，这样并发收紧时能明确
      告诉调用方这份结果对应哪一次授权。
    - 若读者有权限，下钻返回明细格与小计；若没有，明确拒绝且不返回任何门店值。
    """
    max_level = _resolve_max_level(snapshot)
    auth = authorization_block(snapshot)
    row_axis = axis_state(row_dimension, max_level)
    col_axis = axis_state(col_dimension, max_level)
    consistent = True
    reasons: list[str] = []

    row_level = _effective_axis_level(row_axis, max_level) if row_axis["open"] else 0
    col_level = _effective_axis_level(col_axis, max_level) if col_axis["open"] else 0
    expected_level = max(row_level, col_level)
    if row_axis["open"] and col_axis["open"] and expected_level != int(result_level):
        consistent = False
        reasons.append(
            f"结果声明为{level_name(result_level)}级，但当前轴在"
            f"{level_name(expected_level)}级，层级不一致"
        )
    if not row_axis["open"] or not col_axis["open"]:
        consistent = False
        reasons.append("存在未开放轴，未开放轴的下钻请求一律拒绝")

    if max_level < 1 or not _authorize_scope(snapshot):
        return {
            "ok": False,
            "denied": True,
            "allowed": False,
            "reason": "读者没有有效授权，不能下钻",
            "authorization": auth,
            "result_level": result_level,
            "result_level_name": level_name(result_level),
            "expected_level": expected_level,
            "expected_level_name": level_name(expected_level),
            "consistent": False,
            "reasons": reasons or ["无有效授权"],
        }

    # 下钻目标层级不能比读者的最大可见层级更细。
    if int(result_level) > max_level:
        return {
            "ok": False,
            "denied": True,
            "allowed": False,
            "reason": (
                f"结果声明的{level_name(result_level)}级超出读者授权的"
                f"{level_name(max_level)}级层级"
            ),
            "authorization": auth,
            "result_level": result_level,
            "result_level_name": level_name(result_level),
            "expected_level": expected_level,
            "expected_level_name": level_name(expected_level),
            "consistent": False,
            "reasons": reasons + ["下钻目标层级超出授权层级"],
        }

    # 声明层级必须正好等于授权裁剪后的层级：声明更粗或更细都视为归属不符，
    # 防止用"声明成区级"的请求去拿更细粒度，也防止用旧层级结果套新授权。
    if int(result_level) != int(expected_level):
        return {
            "ok": False,
            "denied": True,
            "allowed": False,
            "reason": "下钻层级与授权裁剪结果不一致，已拒绝",
            "authorization": auth,
            "result_level": result_level,
            "result_level_name": level_name(result_level),
            "expected_level": expected_level,
            "expected_level_name": level_name(expected_level),
            "consistent": False,
            "reasons": reasons,
        }

    if not consistent:
        # 层级不一致时不返回任何数据，避免借不一致结果的路径拿到更细粒度。
        return {
            "ok": False,
            "denied": True,
            "allowed": False,
            "reason": "下钻层级与授权裁剪结果不一致，已拒绝",
            "authorization": auth,
            "result_level": result_level,
            "result_level_name": level_name(result_level),
            "expected_level": expected_level,
            "expected_level_name": level_name(expected_level),
            "consistent": False,
            "reasons": reasons,
        }

    scoped_facts = facts.scope_filtered(facts.fact_rows(), _authorize_scope(snapshot))
    if row_value is not None:
        scoped_facts = [
            r for r in scoped_facts if str(r.get(row_axis["effective"], "")) == row_value
        ]
    if col_value is not None and col_axis["effective"]:
        scoped_facts = [
            r for r in scoped_facts if str(r.get(col_axis["effective"], "")) == col_value
        ]

    # 明细行按目标层级去掉门店列，确保返回结果就是声明层级。
    # 单店明细同样抑制：只含一家门店的明细行不得作为下钻结果返回。
    level_field = facts.LEVEL_DIMENSIONS.get(int(result_level), "")
    detail: list[dict[str, Any]] = []
    suppressed_values: list[str] = []
    if level_field:
        for value in facts.distinct_values(scoped_facts, level_field):
            group = [r for r in scoped_facts if str(r.get(level_field, "")) == value]
            store_count = len(facts.distinct_stores(group))
            if max_level < facts.GEO_LEVELS["门店"] and store_count < 2:
                suppressed_values.append(value)
                detail.append(
                    {
                        "level": int(result_level),
                        "level_name": level_name(result_level),
                        "value": value,
                        "subtotal": None,
                        "store_count": store_count,
                        "suppressed": True,
                    }
                )
                continue
            detail.append(
                {
                    "level": int(result_level),
                    "level_name": level_name(result_level),
                    "value": value,
                    "subtotal": facts.display_number(
                        facts.aggregate(group, aggregation)
                    ),
                    "store_count": store_count,
                    "suppressed": False,
                }
            )
        # 门店级下钻只有在授权达到门店级时才可能走到这里。
    else:
        detail = [
            {
                "level": int(result_level),
                "level_name": level_name(result_level),
                "value": "全部",
                "subtotal": facts.display_number(
                    facts.aggregate(scoped_facts, aggregation)
                ),
                "store_count": len(facts.distinct_stores(scoped_facts)),
                "suppressed": False,
            }
        ]

    return {
        "ok": True,
        "denied": False,
        "allowed": True,
        "reason": "",
        "authorization": auth,
        "result_level": result_level,
        "result_level_name": level_name(result_level),
        "expected_level": expected_level,
        "expected_level_name": level_name(expected_level),
        "consistent": True,
        "reasons": [],
        "detail_level": level_field,
        "detail": detail,
        "detail_total": (
            None
            if suppressed_values
            else facts.display_number(facts.aggregate(scoped_facts, aggregation))
        ),
        "suppressed_values": sorted(suppressed_values),
        "singleton_suppressed": bool(suppressed_values),
        "store_count": len(facts.distinct_stores(scoped_facts)),
    }
