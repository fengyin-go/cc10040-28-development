"""受控透视使用的事实表：门店销售明细与地理层级。

事实表只保存最细粒度的门店×品类记录。聚合、裁剪和权限核对全部在这里的
层级定义之上完成，避免各个模块各自拼一套"区域/区/门店"的解释。

层级约定（数字越大越细）：
    1 = 区域
    2 = 区
    3 = 门店
"""
from __future__ import annotations

import copy
from typing import Any, Iterable

GEO_LEVELS: dict[str, int] = {"区域": 1, "区": 2, "门店": 3}
LEVEL_DIMENSIONS: dict[int, str] = {1: "区域", 2: "区", 3: "门店"}
LEVEL_NAMES: dict[int, str] = {1: "区域", 2: "区", 3: "门店"}

# 品类不是地理层级，作为列轴时不参与层级裁剪，但交叉格仍要检查门店数。
CATEGORY_DIMENSION = "品类"
METRIC_NAME = "销售额"

# 事实表：区域 / 区 / 门店 / 品类 / 销售额。
# 静安区只有一家门店，用来演示区级以下的单店粒度过滤。
FACT_ROWS: list[dict[str, Any]] = [
    {"区域": "华东", "区": "浦东区", "门店": "浦东一店", "品类": "生鲜", "销售额": 1200},
    {"区域": "华东", "区": "浦东区", "门店": "浦东一店", "品类": "日用", "销售额": 800},
    {"区域": "华东", "区": "浦东区", "门店": "浦东一店", "品类": "服饰", "销售额": 450},
    {"区域": "华东", "区": "浦东区", "门店": "浦东二店", "品类": "生鲜", "销售额": 1100},
    {"区域": "华东", "区": "浦东区", "门店": "浦东二店", "品类": "日用", "销售额": 620},
    {"区域": "华东", "区": "浦东区", "门店": "浦东二店", "品类": "服饰", "销售额": 380},
    {"区域": "华东", "区": "浦东区", "门店": "浦东三店", "品类": "生鲜", "销售额": 980},
    {"区域": "华东", "区": "浦东区", "门店": "浦东三店", "品类": "日用", "销售额": 540},
    {"区域": "华东", "区": "浦东区", "门店": "浦东三店", "品类": "服饰", "销售额": 300},
    {"区域": "华东", "区": "黄浦区", "门店": "黄浦一店", "品类": "生鲜", "销售额": 760},
    {"区域": "华东", "区": "黄浦区", "门店": "黄浦一店", "品类": "日用", "销售额": 410},
    {"区域": "华东", "区": "黄浦区", "门店": "黄浦一店", "品类": "服饰", "销售额": 220},
    {"区域": "华东", "区": "黄浦区", "门店": "黄浦二店", "品类": "生鲜", "销售额": 690},
    {"区域": "华东", "区": "黄浦区", "门店": "黄浦二店", "品类": "日用", "销售额": 360},
    {"区域": "华东", "区": "黄浦区", "门店": "黄浦二店", "品类": "服饰", "销售额": 180},
    {"区域": "华东", "区": "静安区", "门店": "静安一店", "品类": "生鲜", "销售额": 520},
    {"区域": "华东", "区": "静安区", "门店": "静安一店", "品类": "日用", "销售额": 280},
    {"区域": "华北", "区": "海淀区", "门店": "海淀一店", "品类": "生鲜", "销售额": 900},
    {"区域": "华北", "区": "海淀区", "门店": "海淀一店", "品类": "日用", "销售额": 470},
    {"区域": "华北", "区": "海淀区", "门店": "海淀一店", "品类": "服饰", "销售额": 260},
    {"区域": "华北", "区": "海淀区", "门店": "海淀二店", "品类": "生鲜", "销售额": 840},
    {"区域": "华北", "区": "海淀区", "门店": "海淀二店", "品类": "日用", "销售额": 430},
    {"区域": "华北", "区": "海淀区", "门店": "海淀二店", "品类": "服饰", "销售额": 240},
    {"区域": "华北", "区": "海淀区", "门店": "海淀三店", "品类": "生鲜", "销售额": 780},
    {"区域": "华北", "区": "海淀区", "门店": "海淀三店", "品类": "日用", "销售额": 390},
    {"区域": "华北", "区": "海淀区", "门店": "海淀三店", "品类": "服饰", "销售额": 210},
    {"区域": "华北", "区": "朝阳区", "门店": "朝阳一店", "品类": "生鲜", "销售额": 660},
    {"区域": "华北", "区": "朝阳区", "门店": "朝阳一店", "品类": "日用", "销售额": 350},
    {"区域": "华北", "区": "朝阳区", "门店": "朝阳一店", "品类": "服饰", "销售额": 190},
    {"区域": "华北", "区": "朝阳区", "门店": "朝阳二店", "品类": "生鲜", "销售额": 610},
    {"区域": "华北", "区": "朝阳区", "门店": "朝阳二店", "品类": "日用", "销售额": 330},
    {"区域": "华北", "区": "朝阳区", "门店": "朝阳二店", "品类": "服饰", "销售额": 170},
]


def fact_rows() -> list[dict[str, Any]]:
    """返回事实表副本，调用方可安全地做本地增删。"""
    return copy.deepcopy(FACT_ROWS)


def dimension_level(dimension: str) -> int | None:
    """地理维度的层级；非地理维度（如品类）返回 None。"""
    return GEO_LEVELS.get(str(dimension or "").strip())


def clip_dimension(dimension: str, max_level: int) -> tuple[str, bool]:
    """把请求维度裁剪到读者可见层级。

    返回 (实际维度, 是否发生裁剪)。非地理维度不裁剪。
    """
    level = dimension_level(dimension)
    if level is None:
        return dimension, False
    if max_level < 1:
        return "", True
    if level <= max_level:
        return dimension, False
    return LEVEL_DIMENSIONS[max_level], True


def in_scope(row: dict[str, Any], scope: Iterable[str]) -> bool:
    """判断事实行是否落在读者可见范围里。

    范围里写区域名表示整个区域可见，写区名表示该区可见，写门店表示单店可见。
    """
    scope_list = [str(item) for item in scope]
    if "*" in scope_list:
        return True
    return any(
        row.get(field) in scope_list
        for field in ("区域", "区", "门店")
        if row.get(field)
    )


def scope_filtered(
    facts: Iterable[dict[str, Any]], scope: Iterable[str]
) -> list[dict[str, Any]]:
    return [copy.deepcopy(row) for row in facts if in_scope(row, scope)]


def distinct_stores(facts: Iterable[dict[str, Any]]) -> set[str]:
    return {str(row.get("门店", "")) for row in facts if row.get("门店")}


def distinct_values(facts: Iterable[dict[str, Any]], field: str) -> list[str]:
    return sorted({str(row.get(field, "")) for row in facts if row.get(field)})


def aggregate(
    facts: Iterable[dict[str, Any]], method: str = "SUM"
) -> int | float | None:
    """按聚合方式计算指标；未知方式退回 SUM，保证结果可解释。"""
    values = [
        float(row.get(METRIC_NAME, 0) or 0)
        for row in facts
        if row.get(METRIC_NAME) is not None
    ]
    if not values:
        return None
    method_key = str(method or "SUM").upper()
    if method_key == "COUNT":
        return len(values)
    if method_key == "AVG":
        return round(sum(values) / len(values), 2)
    if method_key == "MAX":
        return max(values)
    if method_key == "MIN":
        return min(values)
    return round(sum(values), 2)


def display_number(value: int | float | None) -> int | float | None:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
