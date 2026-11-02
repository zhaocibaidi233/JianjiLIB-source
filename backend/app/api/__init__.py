# -*- coding: utf-8 -*-
"""FastAPI 路由聚合。"""
from fastapi import APIRouter

from . import (accession, acquisitions, agents, ai, auth, book_libraries,
               cataloging, circulation, dashboard, dictionaries,
               holdings, import_export, inspect, inventory, jobs, labels,
               lookup, maintenance, marc_browser, marc_fields, meta,
               property as property_api, quick, readers, recommendations,
               record_logs, records, reservations, sources, stats, transfer,
               users, portal, seats, seat_admin)

api_router = APIRouter(prefix="/api")
api_router.include_router(meta.router)
# 工作台聚合（登录即可访问，见 core/perm.py）：上班第一眼的运营摘要
api_router.include_router(dashboard.router)
# 读者门户（M4）：独立读者会话，馆员中间件对 /api/portal 整体旁路
api_router.include_router(portal.router)
api_router.include_router(seats.router)
# 座位区域配置（馆员端）：静态路径 /settings/seats/... 必须先于 ai.py 的
# /settings/{key} 注册，避免被单段通配吞掉。
api_router.include_router(seat_admin.router)
# 登录与用户管理。**放在最前面**：auth 的路由都是静态路径
# （/auth/status 等），没有路径参数，不存在被后面路由抢匹配的风险；
# users 的路由带 /users/{user_id}，模块清单特意放在 /auth/modules 下避开它
# （见 api/auth.py 里 auth_modules 的说明）。
api_router.include_router(auth.router)
api_router.include_router(users.router)
# agent 凭据与审计：归「用户管理」模块，紧跟账号/角色
api_router.include_router(agents.router)
# 维护：库结构升级（管理员）。由后端执行而不是启动器 —— 只有后端知道
# 自己实际连的是哪个库（见 api/maintenance.py 的说明）。
api_router.include_router(maintenance.router)
api_router.include_router(book_libraries.router)
api_router.include_router(records.router)
# 操作日志：`/record-logs` 与 `/records/{id}/logs` 都是"某条记录被谁改过"，
# 归 records 模块（能编目的人本来就该看得见）；清空另加管理员判定。
api_router.include_router(record_logs.router)
# 图书转库：`/transfers` 是 records 模块下的子能力，单独成文件
api_router.include_router(transfer.router)
api_router.include_router(import_export.router)
# MARC 文件浏览（全局设置里的工具）：只读本地文件，不碰库里的数据
api_router.include_router(marc_browser.router)
api_router.include_router(import_export.export_router)
api_router.include_router(sources.router)
api_router.include_router(cataloging.router)
api_router.include_router(jobs.router)
api_router.include_router(holdings.router)
# 读者与流通：读者档案是借还的前提，先挂它再挂借还台
api_router.include_router(readers.router)
api_router.include_router(circulation.router)
# 盘点：归「借还台」模块——它盘的是流通中的馆藏实物
api_router.include_router(inventory.router)
# 图书预约：归「借还台」模块——预约是流通队列的一部分
api_router.include_router(reservations.router)
# 图书推荐：读者侧的展示页，同样归流通模块
api_router.include_router(recommendations.router)
# 荐购/受赠登记与审核：不是采购单，也不产生馆藏（教材〔2021〕2号）
api_router.include_router(acquisitions.router)
api_router.include_router(dictionaries.router)
# CNMARC 字段字典（只读）：归「字典中心」模块，供编辑器翻译字段中文名
api_router.include_router(marc_fields.router)
api_router.include_router(ai.router)
api_router.include_router(quick.router)
api_router.include_router(stats.router)
# 财产登记表：报表类接口，归 stats 权限模块
api_router.include_router(property_api.router)
# 总括/个别登录账：报表类接口，归 stats 权限模块
api_router.include_router(accession.router)
api_router.include_router(inspect.router)
api_router.include_router(labels.router)
api_router.include_router(lookup.router)
