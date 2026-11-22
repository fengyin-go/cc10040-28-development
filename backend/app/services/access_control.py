"""读者授权快照：按层级授予可见粒度，并用不可变快照固定每次计算结果对应的授权。

这里的授权是"读者 + 最大可见层级 + 可见范围"三件事：
- 最大可见层级（max_level）决定读者能看到哪一层的聚合，1=区域、2=区、3=门店；
- 可见范围（scope）是允许访问的区列表，``["*"]`` 表示全部区域；
- 每次授权变更都会生成新的 revision，历史 revision 原样保留。

分析任务可能跨 revision 执行：窗口一在准备行列阶段拿到快照，管理员随后收紧
门店级权限生成新 revision，窗口二再准备。计算结果必须能回答"我这份数据对应
哪一次授权"，因此服务端只使用调用时抓取的快照，并在结果里带上 revision、
授权摘要和是否已被后续授权取代的标记。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

LEVEL_NAMES: dict[int, str] = {1: "区域", 2: "区", 3: "门店"}
MAX_ALLOWED_LEVEL = max(LEVEL_NAMES)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def grant_digest(grant: dict[str, Any] | None) -> str:
    """只对授权语义（层级与范围）取摘要，展示名变化不影响结果归属。"""
    if grant is None:
        return "none"
    payload = json.dumps(
        {
            "max_level": int(grant.get("max_level") or 0),
            "scope": sorted(str(item) for item in (grant.get("scope") or [])),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scope_subset(candidate: list[str], existing: list[str]) -> bool:
    if "*" in existing:
        return True
    if "*" in candidate:
        return False
    return set(candidate).issubset(set(existing))


@dataclass(frozen=True)
class AuthSnapshot:
    """某个读者在某个 revision 上的授权视图，创建后不再变化。"""

    reader: str
    revision: int
    created_at: str
    actor: str
    note: str
    grant: dict[str, Any] | None

    @property
    def max_level(self) -> int:
        return int((self.grant or {}).get("max_level") or 0)

    @property
    def scope(self) -> list[str]:
        return list((self.grant or {}).get("scope") or [])

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES.get(self.max_level, "未授权")

    @property
    def grant_digest(self) -> str:
        return grant_digest(self.grant)

    @property
    def snapshot_id(self) -> str:
        return f"auth-r{self.revision}-{self.grant_digest[:10]}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "reader": self.reader,
            "revision": self.revision,
            "created_at": self.created_at,
            "actor": self.actor,
            "note": self.note,
            "grant": copy.deepcopy(self.grant),
            "grant_digest": self.grant_digest,
            "snapshot_id": self.snapshot_id,
        }


# 初始授权：覆盖区域 / 区 / 门店三种读者，另留一个门店分析员用于并发收紧演示。
INITIAL_GRANTS: dict[str, dict[str, Any]] = {
    "reader_region": {
        "display_name": "区域分析员",
        "max_level": 1,
        "scope": ["*"],
    },
    "reader_district": {
        "display_name": "区级分析员",
        "max_level": 2,
        "scope": ["浦东区", "黄浦区"],
    },
    "reader_haidian": {
        "display_name": "海淀区分析员",
        "max_level": 2,
        "scope": ["海淀区"],
    },
    "reader_store": {
        "display_name": "门店分析员",
        "max_level": 3,
        "scope": ["浦东区"],
    },
    "analyst_a": {
        "display_name": "分析员甲",
        "max_level": 3,
        "scope": ["浦东区", "黄浦区"],
    },
}


class AuthorizationRegistry:
    """授权 revision 注册表；所有读写都在同一把锁内完成。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._revisions: list[dict[str, Any]] = []
        self.reset()

    # ── 生命周期 ──

    def reset(self) -> None:
        with self._lock:
            self._revisions = [
                self._make_revision(1, "系统", "初始授权快照", copy.deepcopy(INITIAL_GRANTS))
            ]

    def _make_revision(
        self, revision: int, actor: str, note: str, grants: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "revision": revision,
            "created_at": _now(),
            "actor": actor,
            "note": note,
            "grants": copy.deepcopy(grants),
        }

    # ── 查询 ──

    def current_revision(self) -> int:
        with self._lock:
            return int(self._revisions[-1]["revision"])

    def head(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._revisions[-1])

    def revisions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "revision": item["revision"],
                    "created_at": item["created_at"],
                    "actor": item["actor"],
                    "note": item["note"],
                    "reader_count": len(item["grants"]),
                }
                for item in self._revisions
            ]

    def get_revision(self, revision: int) -> dict[str, Any] | None:
        with self._lock:
            for item in self._revisions:
                if int(item["revision"]) == int(revision):
                    return copy.deepcopy(item)
            return None

    def readers(self) -> list[dict[str, Any]]:
        head = self.head()
        result = []
        for reader, grant in sorted(head["grants"].items()):
            result.append({
                "reader": reader,
                "display_name": grant.get("display_name", reader),
                "max_level": int(grant.get("max_level") or 0),
                "level_name": LEVEL_NAMES.get(int(grant.get("max_level") or 0), "未授权"),
                "scope": list(grant.get("scope") or []),
                "grant_digest": grant_digest(grant),
            })
        return result

    def snapshot(self, reader: str, revision: int | None = None) -> AuthSnapshot | None:
        """抓取不可变授权视图；revision 不存在时返回 None，读者未授权时 grant 为 None。"""
        with self._lock:
            if not self._revisions:
                return None
            if revision is None:
                item = self._revisions[-1]
            else:
                item = next(
                    (row for row in self._revisions if int(row["revision"]) == int(revision)),
                    None,
                )
            if item is None:
                return None
            grant = item["grants"].get(reader)
            return AuthSnapshot(
                reader=reader,
                revision=int(item["revision"]),
                created_at=item["created_at"],
                actor=item["actor"],
                note=item["note"],
                grant=copy.deepcopy(grant),
            )

    def grant_at(self, reader: str, revision: int | None = None) -> dict[str, Any] | None:
        snap = self.snapshot(reader, revision)
        return copy.deepcopy(snap.grant) if snap is not None else None

    # ── 收紧授权 ──

    def tighten(
        self,
        reader: str,
        max_level: int,
        *,
        scope: list[str] | None = None,
        actor: str = "管理员",
        note: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """生成新的授权 revision。只允许收紧：层级不能变深、范围不能扩大。"""
        try:
            max_level = int(max_level)
        except (TypeError, ValueError):
            return None, "目标层级必须是整数"
        if max_level < 1 or max_level > MAX_ALLOWED_LEVEL:
            return None, f"目标层级必须在 1 至 {MAX_ALLOWED_LEVEL} 之间"

        with self._lock:
            head = copy.deepcopy(self._revisions[-1])
            grants = head["grants"]
            if reader not in grants:
                return None, f"读者 {reader} 未登记授权，不能通过收紧操作新增"
            old = grants[reader]
            old_level = int(old.get("max_level") or 0)
            if max_level > old_level:
                return None, (
                    f"收紧操作不允许放宽可见层级：{reader} 当前为"
                    f"{LEVEL_NAMES.get(old_level, old_level)}级"
                )
            old_scope = list(old.get("scope") or [])
            new_scope = list(scope) if scope is not None else list(old_scope)
            new_scope = [str(item) for item in new_scope if str(item)]
            if not new_scope:
                return None, "收紧后的可见范围不能为空"
            if not _scope_subset(new_scope, old_scope):
                return None, "收紧操作不允许扩大可见范围"

            grants[reader] = {
                **old,
                "max_level": max_level,
                "scope": new_scope,
            }
            revision = int(head["revision"]) + 1
            change_note = note or (
                f"收紧 {reader} 至{LEVEL_NAMES.get(max_level, max_level)}级"
            )
            record = self._make_revision(revision, actor, change_note, grants)
            self._revisions.append(record)
            return copy.deepcopy(record), (
                f"已生成第 {revision} 次授权：{reader} 可见"
                f"{LEVEL_NAMES.get(max_level, max_level)}级，范围 {new_scope}"
            )


registry = AuthorizationRegistry()


def reset_authorization_state() -> None:
    """测试与本地重置入口。"""
    registry.reset()


def authorization_block(snap: AuthSnapshot | None) -> dict[str, Any]:
    """给任何计算结果贴上的授权归属信息。"""
    head = registry.head()
    if snap is None:
        return {
            "reader": None,
            "auth_revision": None,
            "result_authorization": "无有效授权快照",
            "denied": True,
            "current_revision": head["revision"],
        }

    head_grant = head["grants"].get(snap.reader)
    head_digest = grant_digest(head_grant)
    grant_changed = head_digest != snap.grant_digest
    revision_advanced = int(head["revision"]) > int(snap.revision)
    return {
        "reader": snap.reader,
        "auth_revision": snap.revision,
        "auth_revision_created_at": snap.created_at,
        "auth_revision_actor": snap.actor,
        "auth_revision_note": snap.note,
        "snapshot_id": snap.snapshot_id,
        "grant": copy.deepcopy(snap.grant),
        "max_level": snap.max_level,
        "level_name": snap.level_name,
        "grant_digest": snap.grant_digest,
        "result_authorization": f"第 {snap.revision} 次授权",
        "current_revision": int(head["revision"]),
        "authorization_changed": grant_changed,
        "revision_advanced": revision_advanced,
        "superseded": grant_changed,
        "head_grant": copy.deepcopy(head_grant),
        "message": (
            f"结果对应第 {snap.revision} 次授权；当前阅读权限已被收紧，"
            f"最新为第 {head['revision']} 次授权"
            if grant_changed
            else f"结果对应第 {snap.revision} 次授权，与当前授权一致"
        ),
    }
