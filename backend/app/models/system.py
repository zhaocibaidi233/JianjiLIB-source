from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

class Setting(Base):
    """系统设置（key-value）：ai 配置（base_url/model/api_key）等，UI 可编辑。"""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)


class Job(Base):
    """后台批量任务（导入/导出/套录批次）：进度可查询。"""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))                       # import-iso / ...
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/error
    progress: Mapped[int] = mapped_column(Integer, default=0)           # 0~100
    message: Mapped[str | None] = mapped_column(String(256))
    params: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


PERM_NONE = "none"


PERM_READ = "read"


PERM_WRITE = "write"


PERM_LEVELS = (PERM_NONE, PERM_READ, PERM_WRITE)


PERM_WILDCARD = "*"


ROLE_ADMIN = "admin"


ROLE_EDITOR = "editor"


class Role(Base):
    """角色：一组「模块 → 权限档」的映射。"""
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # name 是稳定标识（权限判定只认它），title 是给人看的中文名
    name: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(64))
    # {"records": "write", "stats": "read", ...}；管理员为 {"*": "write"}
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """登录账号。

    `password_hash` 是自描述字符串（`scrypt$n$r$p$salt$hash`，见 core/security.py），
    不引入 passlib/bcrypt 依赖——运行依赖里本来就没有认证库。
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    # 显式命名 FK：autogenerate 对无名约束生成不出可逆的 drop 指令
    # （同 holdings.library_id 的教训，见迁移 ce9e44899a63）。
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", name="fk_users_role_id_roles"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 署名字段：登录后 `records.created_by` 落这里，替代手填的 X-Operator
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


REG_PENDING = "pending"


REG_APPROVED = "approved"


REG_REJECTED = "rejected"


REG_STATUSES = (REG_PENDING, REG_APPROVED, REG_REJECTED)


class RegistrationRequest(Base):
    """自助注册申请单。**批准的那一刻才往 `users` 插行**，申请本身不是账号。

    为什么不复用 `users` 加一列 `status`（holdings 那套 status+原因+处置人+处置时间
    看起来正好是同构的）：`services/auth.py::auth_enabled()` 的判据是
    「users 表有没有行」，一条待审申请会**提前**让整个系统进入「必须登录」状态，
    而这时能用的人一个都没有；它还会一起污染「最后一个管理员」守卫（`_admin_count`）
    和用户列表。申请单不是账号，就不该住在账号表里。

    `password_hash` 可空，且**进入终态（批准/驳回）时清空**：申请单要长期留着当台账，
    没道理让一个永远不会再用到的口令哈希继续躺在库里。申请人自填口令是唯一可行的
    流程——系统没有邮箱/短信通道，管理员无法把临时密码交给对方。

    `reviewed_by` 存冗余姓名、**不加外键**（与 `holdings.withdrawn_by`、
    `library_transfers` 同一口径：台账要活过它记录的那些行，审批人被删掉之后
    「谁批的」仍然查得到）。
    """
    __tablename__ = "registration_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 申请人自定的登录名（已 normalize：小写、无空格）。不建唯一约束——
    # 同一个人被驳回后可以重新申请，历史那几行要留得住。
    username: Mapped[str] = mapped_column(String(64), index=True)
    note: Mapped[str | None] = mapped_column(String(256))     # 申请人填的备注（可选）
    password_hash: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default=REG_PENDING, index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(256))
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """登录会话（令牌）。

    **只存令牌的 SHA-256**，不存明文：库被拷走也不能直接拿来登录
    （明文只在前端 localStorage 与请求头里）。
    """
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_sessions_user_id_users"),
        index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client: Mapped[str | None] = mapped_column(String(256))   # User-Agent，仅供管理员排查


class AgentToken(Base):
    """agent 凭据——给 Claude Desktop / 脚本这类**非人类客户端**的长期令牌。

    为什么不复用 `user_sessions`：
      - **生命周期不同**：登录会话 30 天过期、登出即失效；agent 凭据要长期有效
        （写在 MCP 客户端配置里，天天弹登录不现实），只能靠显式吊销。
      - **署名要分得开**：`created_by` 记的是账号名，agent 干的活署名成
        「agent: 导入机器人」，财产账与转库台账上要能一眼看出是人还是机器改的。
      - **可审计**：凭据有名字（「导入机器人」「对账脚本」），审计表里才认得出
        是谁在调；一堆匿名会话令牌查不出东西。

    **权限不另起炉灶**：凭据绑一个**既有角色**，判定完全走 `core/perm.py` 的
    同一张模块权限表 —— agent 能做的事不会比同角色的人多一件。

    同样**只存令牌的 SHA-256**；`prefix` 是明文前 8 位，仅用于在列表里认人
    （拿它登录不了）。
    """
    __tablename__ = "agent_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))            # 明文前 8 位，列表展示用
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", name="fk_agent_tokens_role_id_roles"), index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))  # 建它的账号名（人）
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentAuditLog(Base):
    """agent 调用审计——**每一条** agent 请求记一行。

    为什么必须记：人的操作有界面、有确认框、当事人说得清；agent 的操作是
    无人值守的，出了事只能靠日志回答「它什么时候动了什么」。所以不记摘要、
    不采样，**逐条记**（方法 + 路径 + 状态码 + 耗时已经够定位问题）。

    **只记 agent 请求**，不记人的：人的操作不该因为开了这个功能就被记账，
    而且量级差两个数量级（界面一次点击就是几十个请求）。

    表会涨：提供「清空」，并保留最近 `_KEEP` 条（见 services/agents.py），
    免得单机工具被自己的日志撑爆。
    """
    __tablename__ = "agent_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[int] = mapped_column(Integer, index=True)   # 不加外键：凭据可删，日志要留
    token_name: Mapped[str] = mapped_column(String(64))          # 冗余存名字：凭据删了也认得出是谁
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(256), index=True)
    status: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecordLog(Base):
    """记录级操作日志——**谁在何时改了哪条书目记录**。

    与 `AgentAuditLog` 的分工：那张记的是"请求"，这张记的是"数据变了"。
    只读请求不记（翻一百页图书库不会留下一条日志），失败请求也不记
    （业务回滚了就等于没发生）—— 所以这张表**更小、更接近审计刚需**：
    用户问的是"这条记录的书目字段怎么变了""这批 500 条是谁导进来的"。

    **冗余 title / barcode 不是偷懒**：记录可能被删，删完只剩 id 的日志
    等于没记。日志要能**独立读懂**（同 agent 审计冗余 token_name 的理由）。

    `record_id` / `library_id` 都**不加外键**：记录与图书库都可能被删，
    日志必须留下 —— 加了外键就变成"删记录时日志跟着消失"，那还叫审计吗。

    表会涨：提供「清空」，并保留最近 `LOG_KEEP` 条（见 services/record_logs.py）。
    """
    __tablename__ = "record_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 记录可能的动作：create / update / delete / import（整批汇总）
    action: Mapped[str] = mapped_column(String(16), index=True)
    # 署名口径与 `records.created_by` 完全一致（core/operator.py）：
    # 登录姓名 → agent:名称 → 请求头 → 空。同一套口径，账才对得上。
    operator: Mapped[str | None] = mapped_column(String(64), index=True)
    library_id: Mapped[int | None] = mapped_column(Integer, index=True)
    record_id: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    barcode: Mapped[str] = mapped_column(String(64), default="")
    # 涉及的册数：单条记录恒为 1；整批汇总行放实际条数。
    copies: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
