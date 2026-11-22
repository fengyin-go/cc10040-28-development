"""受控透视与层级授权的行为测试。

覆盖题面要求的四个观察点：
1. 区级读者只能看到区级小计（含多店合计），看不到门店轴；
2. 同一个分析员两窗口跨授权收紧，结果能自证对应哪次 revision；
3. 行轴、列轴、交叉格、三个方向合计统一按同一层级裁剪，未开放轴不泄露门店值；
4. 下钻按结果声明的层级核对归属，层级不符或越权一律拒绝。
"""
from __future__ import annotations

import pytest

from app.services import analytics_facts as facts
from app.services.access_control import (
    authorization_block,
    registry,
    reset_authorization_state,
)
from app.services.governed_analysis import (
    compare_windows,
    compute_cell,
    prepare_cell,
    reset_analysis_state,
    verify_cell_drill,
)
from app.services.governed_pivot import compute_pivot_grid, verify_drill_down
from app.services.pivot_table import PivotTableService


@pytest.fixture(autouse=True)
def fresh_authorization():
    reset_authorization_state()
    reset_analysis_state()
    yield
    reset_authorization_state()
    reset_analysis_state()


def _snap(reader: str):
    snapshot = registry.snapshot(reader)
    assert snapshot is not None
    return snapshot


class TestDistrictReaderGrid:
    """区级用户核对包含多店的区小计。"""

    def test_district_reader_sees_district_subtotals(self):
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        assert result["ok"] is True
        assert result["authorization"]["max_level"] == 2
        assert result["authorization"]["level_name"] == "区"
        # 只覆盖授权范围内的两个区，且都不等于全部门店合计。
        assert set(result["row_totals"]) == {"浦东区", "黄浦区"}
        assert result["row_totals"]["浦东区"] == 6370
        assert result["row_totals"]["黄浦区"] == 2620
        assert result["grand_total"] == 8990
        # 每个区小计确实包含多店。
        store_counts = {
            cell["row"]: cell["store_count"]
            for cell in result["grid"]
            if cell["column"] == "生鲜"
        }
        assert store_counts["浦东区"] == 3
        assert store_counts["黄浦区"] == 2

    def test_row_totals_equal_sum_of_visible_cells(self):
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        for row, total in result["row_totals"].items():
            cell_sum = sum(
                cell["value"] for cell in result["grid"] if cell["row"] == row
            )
            assert total == cell_sum
        assert result["grand_total"] == sum(result["row_totals"].values())
        for col, total in result["column_totals"].items():
            cell_sum = sum(
                cell["value"] for cell in result["grid"] if cell["column"] == col
            )
            assert total == cell_sum

    def test_no_store_identifiers_in_district_grid(self):
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        all_stores = set()
        for row in facts.fact_rows():
            all_stores.add(row["门店"])
        payload = str(result)
        for store in all_stores:
            assert store not in payload


class TestAxisClipping:
    """行轴、列轴和合计统一服从同一层级裁剪。"""

    def test_store_axis_is_clipped_to_district(self):
        result = compute_pivot_grid(
            row_dimension="门店", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        assert result["row_axis"]["requested"] == "门店"
        assert result["row_axis"]["effective"] == "区"
        assert result["row_axis"]["clipped"] is True
        # 裁剪后仍只有授权范围内的区。
        assert set(result["row_totals"]) == {"浦东区", "黄浦区"}
        assert all("店" not in cell["row"] for cell in result["grid"])

    def test_region_reader_cannot_open_store_axis(self):
        result = compute_pivot_grid(
            row_dimension="门店", col_dimension="门店", aggregation="SUM",
            snapshot=_snap("reader_region"),
        )
        assert result["row_axis"]["effective"] == "区域"
        assert result["column_axis"]["effective"] == "区域"
        assert result["authorization"]["level_name"] == "区域"

    def test_geo_axis_below_reader_level_is_closed(self):
        # 区级读者请求门店轴时裁剪成区；这里的"未开放"针对更细层级被收回的读者。
        registry.tighten("reader_district", 1, note="收回区级权限")
        result = compute_pivot_grid(
            row_dimension="门店", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        # 收紧到区域级后，门店轴被裁剪为区域轴，仍不出现门店值。
        assert result["row_axis"]["effective"] == "区域"
        assert result["authorization"]["max_level"] == 1

    def test_category_axis_not_clipped(self):
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        assert result["column_axis"]["effective"] == "品类"
        assert result["column_axis"]["clipped"] is False


class TestClosedAxisDoesNotLeak:
    """未开放的轴以及交叉格不能成为反推门店值的途径。"""

    def test_unconfigured_axis_blocks_grid(self):
        result = compute_pivot_grid(
            row_dimension="", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        assert result["blocked"] is True
        assert result["grid"] == []
        assert result["row_totals"] == {}
        assert result["grand_total"] is None
        assert result["visible_store_count"] == 0

    def test_closed_column_axis_blocks_all_cells(self):
        # 把读者收回到区域级后，"门店"列轴被裁剪为区域轴；再组合一个未配置轴验证封锁。
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="", aggregation="SUM",
            snapshot=_snap("reader_region"),
        )
        assert result["blocked"] is True
        assert result["cell_count"] == 0
        assert result["column_totals"] == {}

    def test_unauthorized_reader_gets_empty_grid(self):
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=registry.snapshot("unknown_reader"),
        )
        assert result["ok"] is False
        assert result["denied"] is True
        assert result["grid"] == []
        assert result["grand_total"] is None


class TestConcurrentTighten:
    """同一分析员两窗口 + 中途收紧门店级权限。"""

    def test_one_window_superseded_window_reports_own_revision(self):
        window_a = prepare_cell(
            reader="analyst_a", row_dimension="区域", col_dimension="门店",
            aggregation="SUM", label="窗口一",
        )
        assert window_a["ok"]
        computed_a = compute_cell(window_a["cell_id"])
        assert computed_a["ok"]
        result_a = computed_a["result"]
        assert result_a["authorization"]["max_level"] == 3
        assert len(result_a["row_totals"]) >= 1
        assert any("店" in cell["column"] for cell in result_a["grid"])

        # 管理员收紧门店级权限：analyst_a 从门店级降到区级。
        from app.services.governed_analysis import tighten_and_recheck
        tighten = tighten_and_recheck(
            reader="analyst_a", max_level=2,
            before_cell_id=window_a["cell_id"],
            note="复核期间收紧门店级权限",
        )
        assert tighten["ok"]
        assert tighten["revision"] == 2

        window_b = prepare_cell(
            reader="analyst_a", row_dimension="区域", col_dimension="门店",
            aggregation="SUM", label="窗口二",
        )
        assert window_b["ok"]
        computed_b = compute_cell(window_b["cell_id"])
        assert computed_b["ok"]
        result_b = computed_b["result"]

        # 窗口二在区级授权下准备：门店列轴被裁剪为区。
        assert result_b["authorization"]["auth_revision"] == 2
        assert result_b["column_axis"]["effective"] == "区"
        assert all("店" not in cell["column"] for cell in result_b["grid"])

        # 窗口一的结果仍然能自证是第 1 次授权，并被标记为已取代。
        refreshed_a = verify_cell_drill(window_a["cell_id"])
        assert refreshed_a["authorization"]["auth_revision"] == 1
        assert refreshed_a["authorization"]["superseded"] is True
        assert refreshed_a["authorization"]["result_authorization"] == "第 1 次授权"

        comparison = compare_windows([window_a["cell_id"], window_b["cell_id"]])
        assert comparison["same_authorization"] is False
        assert comparison["distinct_revisions"] == [1, 2]
        assert comparison["distinct_levels"] == [2, 3]

    def test_old_window_never_recomputed_with_new_authorization(self):
        window_a = prepare_cell(
            reader="analyst_a", row_dimension="区域", col_dimension="门店",
            aggregation="SUM",
        )
        assert window_a["ok"]
        registry.tighten("analyst_a", 2)
        # 用新 revision 重算旧窗口必须被拒绝。
        with_new = compute_cell(window_a["cell_id"], revision=2)
        assert with_new["ok"] is False
        assert with_new["auth_revision"] == 1

    def test_computed_cell_is_idempotent(self):
        window = prepare_cell(
            reader="analyst_a", row_dimension="区", col_dimension="品类",
        )
        first = compute_cell(window["cell_id"])
        second = compute_cell(window["cell_id"])
        assert first["ok"] and second["ok"]
        assert second["deduplicated"] is True
        assert second["result"]["grand_total"] == first["result"]["grand_total"]

    def test_tighten_cannot_widen(self):
        record, message = registry.tighten("reader_district", 3)
        assert record is None
        assert "不允许放宽" in message

        record, message = registry.tighten(
            "reader_district", 2, scope=["浦东区", "黄浦区", "静安区"]
        )
        assert record is None
        assert "不允许扩大" in message


class TestDrillDownAttribution:
    """下钻时按结果声明的层级核对归属。"""

    def test_district_drill_returns_district_level_subtotals(self):
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
        )
        check = verify_drill_down(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
            result_level=2,
        )
        assert check["allowed"] is True
        assert check["consistent"] is True
        assert check["detail_level"] == "区"
        assert check["detail_total"] == result["grand_total"]
        assert {row["value"] for row in check["detail"]} == {"浦东区", "黄浦区"}

    def test_drill_rejects_mismatched_level(self):
        # 门店轴对区级读者会被裁剪成区，声明成门店级即与授权裁剪结果不符。
        check = verify_drill_down(
            row_dimension="门店", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
            result_level=3,
        )
        assert check["allowed"] is False
        assert check["consistent"] is False
        assert check["expected_level"] == 2
        assert "层级" in check["reason"]

    def test_drill_after_tighten_uses_old_snapshot_not_new_grant(self):
        window = prepare_cell(
            reader="analyst_a", row_dimension="区域", col_dimension="区",
            aggregation="SUM",
        )
        assert window["ok"]
        computed = compute_cell(window["cell_id"])
        assert computed["ok"]
        registry.tighten("analyst_a", 2)
        # 结果的声明层级来自快照 r1（区级），下钻核对用同一快照，仍可回答归属。
        check = verify_cell_drill(window["cell_id"])
        assert check["allowed"] is True
        assert check["authorization"]["auth_revision"] == 1
        assert check["authorization"]["superseded"] is True

    def test_drill_rejects_when_result_level_exceeds_reader(self):
        check = verify_drill_down(
            row_dimension="门店", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_district"),
            result_level=3,
        )
        assert check["allowed"] is False
        assert check["result_level"] == 3
        assert check["expected_level"] == 2


class TestReentryModel:
    """重新进入模型后检查获准的维度、透视明细及总计。"""

    def test_allowed_dimensions_exposes_levels(self):
        service = PivotTableService()
        allowed = service.allowed_dimensions(5, reader="reader_district")
        assert allowed["ok"]
        assert allowed["max_level"] == 2
        assert allowed["max_level_name"] == "区"
        assert {item["level"] for item in allowed["subtotal_levels"]} == {1, 2}
        assert allowed["row_axis"]["effective"] == "区域"
        assert allowed["column_axis"]["effective"] == "区"
        assert allowed["authorization"]["auth_revision"] == 1
        # 门店维度可"裁剪使用"但并未在门店层级获准，重新进入模型时要能分辨。
        store_dim = next(d for d in allowed["allowed_dimensions"] if d["dimension"] == "门店")
        assert store_dim["granted"] is False
        assert store_dim["status"] == "裁剪至更粗层级"
        district_dim = next(d for d in allowed["allowed_dimensions"] if d["dimension"] == "区")
        assert district_dim["granted"] is True

    def test_config_matrix_matches_detail_and_total(self):
        service = PivotTableService()
        result = service.matrix_for_config(5, reader="reader_district")
        assert result["ok"]
        assert result["config_name"] == "区域-门店下钻透视"
        assert result["column_axis"]["effective"] == "区"
        assert result["grand_total"] == sum(result["row_totals"].values())
        assert result["visible_store_count"] == 5

    def test_shared_config_does_not_leak_store_values(self):
        service = PivotTableService()
        result = service.matrix_for_config(5, reader="reader_district")
        for store in {row["门店"] for row in facts.fact_rows()}:
            assert store not in str(result)


class TestAuthorizationBlock:
    """授权归属信息本身要可审计。"""

    def test_authorization_block_reports_revision_and_digest(self):
        block = authorization_block(_snap("reader_district"))
        assert block["auth_revision"] == 1
        assert block["max_level"] == 2
        assert block["result_authorization"] == "第 1 次授权"
        assert block["superseded"] is False
        assert len(block["grant_digest"]) == 64

    def test_head_grant_follows_tighten(self):
        old_snapshot = _snap("reader_district")
        registry.tighten("reader_district", 1)
        block = authorization_block(old_snapshot)
        assert block["current_revision"] == 2
        assert block["revision_advanced"] is True
        assert block["superseded"] is True
        # 旧快照本身仍是区级授权，新的 head 是区域级。
        assert block["grant"]["max_level"] == 2
        assert registry.snapshot("reader_district").max_level == 1


class TestGovernedApi:
    """路由层契约：请求模型到响应结构都要保持授权归属字段。"""

    def _scenario(self):
        from app.routers.governed import ScenarioPayload, concurrent_tighten_scenario

        return concurrent_tighten_scenario(
            ScenarioPayload(
                reader="analyst_a", config_id=5, tighten_level=2,
                note="复核期间收紧门店级权限",
            )
        )

    def test_scenario_endpoint_reports_both_revisions(self):
        result = self._scenario()
        assert result["ok"] is True
        assert result["trace"]["window_a_auth_revision"] == 1
        assert result["trace"]["window_b_auth_revision"] == 2
        assert result["windows"]["a"]["result"]["authorization"]["superseded"] is True
        assert result["windows"]["b"]["result"]["column_axis"]["effective"] == "区"

    def test_scenario_reentry_dimensions_exclude_store_grant(self):
        result = self._scenario()
        dims = result["dimensions_on_reentry"]
        assert dims["max_level_name"] == "区"
        by_name = {item["dimension"]: item for item in dims["allowed_dimensions"]}
        assert by_name["门店"]["granted"] is False
        assert by_name["区"]["granted"] is True
        # 总计只落在授权层级上，不含门店级值。
        assert result["windows"]["b"]["result"]["visible_store_count"] == 5
        assert sum(result["windows"]["b"]["result"]["row_totals"].values()) == (
            result["windows"]["b"]["result"]["grand_total"]
        )

    def test_matrix_endpoint_clips_and_labels(self):
        from app.routers.governed import ad_hoc_matrix

        matrix = ad_hoc_matrix(
            reader="reader_district", row_dimension="门店", col_dimension="品类",
            aggregation="SUM", revision=None,
        )
        assert matrix["row_axis"]["effective"] == "区"
        assert matrix["authorization"]["result_authorization"] == "第 1 次授权"
        assert matrix["row_totals"] == {"浦东区": 6370, "黄浦区": 2620}

    def test_drill_endpoint_uses_declared_level(self):
        from app.routers.governed import (
            ComputePayload,
            PreparePayload,
            compute,
            drill,
            prepare,
            DrillPayload,
        )

        prepared = prepare(
            PreparePayload(
                reader="reader_district", row_dimension="区", col_dimension="品类",
                label="区级窗口",
            )
        )
        computed = compute(prepared["cell_id"], ComputePayload())
        assert computed["ok"]
        check = drill(prepared["cell_id"], DrillPayload())
        assert check["allowed"] is True
        assert check["detail_level"] == "区"
        assert check["detail_total"] == computed["result"]["grand_total"]

    def test_prepare_with_missing_revision_is_rejected(self):
        from app.routers.governed import PreparePayload, prepare

        result = prepare(
            PreparePayload(
                reader="reader_district", row_dimension="区", revision=999,
            )
        )
        assert result["ok"] is False


class TestSingletonSuppression:
    """单店分组不能被小计、交叉格或下钻明细反推。"""

    def _snapshot(self):
        from app.services.access_control import AuthSnapshot

        return AuthSnapshot(
            reader="custom", revision=7, created_at="t", actor="t", note="t",
            grant={"display_name": "混合范围", "max_level": 2,
                   "scope": ["浦东区", "静安区"]},
        )

    def test_singleton_district_is_suppressed(self):
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=self._snapshot(),
        )
        assert result["singleton_suppressed"] is True
        assert result["suppressed_rows"] == ["静安区"]
        assert "静安区" not in result["row_totals"]
        assert result["grand_total"] is None
        assert result["column_totals"] == {}
        suppressed_cells = [c for c in result["grid"] if c["suppressed"]]
        assert suppressed_cells
        for cell in suppressed_cells:
            assert cell["value"] is None
        # 多店区仍然可以核对。
        assert result["row_totals"]["浦东区"] == 6370

    def test_haidian_reader_single_district_scope_is_fine(self):
        # 整个可见范围只有海淀区，但区内有 3 家门店，可直接展示区小计。
        result = compute_pivot_grid(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_haidian"),
        )
        assert result["singleton_suppressed"] is False
        assert result["row_totals"] == {"海淀区": 4520}
        assert result["grand_total"] == 4520

    def test_store_reader_not_suppressed(self):
        result = compute_pivot_grid(
            row_dimension="门店", col_dimension="品类", aggregation="SUM",
            snapshot=_snap("reader_store"),
        )
        assert result["singleton_suppressed"] is False
        assert result["grand_total"] == 6370

    def test_drill_suppresses_singleton_detail(self):
        check = verify_drill_down(
            row_dimension="区", col_dimension="品类", aggregation="SUM",
            snapshot=self._snapshot(), result_level=2,
        )
        assert check["allowed"] is True
        by_value = {row["value"]: row for row in check["detail"]}
        assert by_value["静安区"]["suppressed"] is True
        assert by_value["静安区"]["subtotal"] is None
        assert by_value["浦东区"]["subtotal"] == 6370
        assert check["detail_total"] is None
