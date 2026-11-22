"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

固定前缀的跨模块与受控透视路由必须先于 ``/api/{module}`` 动态资源路由注册，
否则 ``/api/governed/readers`` 这类两段路径会被 ``/api/{module}/{entry_id}``
抢先匹配，导致 entry_id 解析失败。
"""
from __future__ import annotations

from app.routers import resource, cross_module, governed

ROUTERS = [cross_module, governed, resource]