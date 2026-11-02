# -*- coding: utf-8 -*-
"""进程内登录限速（滑动窗口计数）。

## 为什么不用 Redis / slowapi / 网关限速

部署形态是**单机 / 校园局域网、uvicorn 单进程**（见启动器），进程内计数就是
全局计数。为这个形态引一个分布式限速依赖，换来的是用不上的能力，多的是要维护
的版本。更关键的一条：**限速不能依赖数据库**——数据库连不上时登录接口会被
反复重试，那恰恰是最需要限速的时刻。

## 它挡什么、不挡什么（别当成安全边界用）

挡：在线口令爆破、脚本化暴力重试、以及每次校验要占 16 MiB 内存的并发打满。
不挡：分布式慢速撞库（需要网关层）、多 worker 部署下的跨进程累计。

已知边界，写在这里避免误判：
  - 进程重启后计数清零；
  - 多 worker 部署时各算各的（本项目单进程）；
  - 只按「账号」与「来源」两个维度计数，换 IP + 换账号的广撒网要靠人工看日志。

## 为什么不按 IP 限速（在局域网形态下）

前端经代理访问时，后端看到的来源可能恒为 127.0.0.1。按来源计数会让
**全校共用一个桶**——20 次失败就把所有人锁死，比不设
还糟。所以这里只保留「账号 / 借书证号」一个维度。

直连后端（单端口静态托管）时来源地址是真的，理论上可以再加一层；但两种部署
形态共存时无法判断当前是哪种，加错了就是全校被锁。宁可少一层，也不要一个会
误伤的限速。真要防分布式撞库，那是网关层的活。
"""
from __future__ import annotations

import asyncio
import threading
import time
import weakref
from collections import deque

#: 同一 key 的失败时间戳队列上限（防止单 key 被刷爆内存）
_MAX_EVENTS_PER_KEY = 64
#: 全局 key 数量上限。超过后淘汰最久没活动的 key——攻击者用随机用户名刷
#: 新 key 时，被淘汰的是那些早已沉寂的，正在攻击的 key 不受影响。
_MAX_KEYS = 4096

_LOCK = threading.Lock()
#: key → [失败时间戳队列, 最后活动时间, 窗口秒数, 阈值]
#: 窗口与阈值随条目一起存：淘汰时需要在**不知道调用方是谁**的情况下判断
#: 「这个条目现在是否处于封锁中」，而封锁与否取决于它自己的窗口与阈值。
_BUCKETS: dict[str, list] = {}


def _prune(dq: deque, now: float, window: float) -> None:
    cutoff = now - window
    while dq and dq[0] <= cutoff:
        dq.popleft()


def _is_blocked(entry: list, now: float) -> bool:
    """该条目此刻是否仍在封锁中（会把过期的失败先清掉）。"""
    dq, _ts, window, limit = entry
    _prune(dq, now, window)
    return len(dq) >= limit


def _evict_if_needed(now: float) -> None:
    """key 数量超上限时淘汰最久未活动的条目（调用方已持锁）。

    ## 为什么不能简单地「按最后活动时间淘汰」

    被封的 key 恰恰**从此不再活动**——按时间排序它永远排在最前面。结果是：
    攻击者锁死 `admin` 之后灌 4000 个随机用户名，就能把 `admin` 那条挤掉，
    封锁立刻解除（实测有效）。这是「淘汰策略反过来削弱了自己保护的东西」。

    所以分两轮：先淘汰**没有在封锁中**的条目（它们才是真正闲置的），不够再
    动封锁中的条目，且优先动「最早会自然解封」的那个——伤害最小。
    """
    if len(_BUCKETS) <= _MAX_KEYS:
        return
    overflow = len(_BUCKETS) - _MAX_KEYS
    idle, locked = [], []
    for key, entry in _BUCKETS.items():
        if _is_blocked(entry, now):
            locked.append((entry[0][0] + entry[2], key))     # 解封时刻
        else:
            idle.append((entry[1], key))                      # 最后活动
    idle.sort()
    victims = [k for _, k in idle[:overflow]]
    if len(victims) < overflow:
        locked.sort()
        victims += [k for _, k in locked[:overflow - len(victims)]]
    for key in victims:
        _BUCKETS.pop(key, None)


class Limiter:
    """一个「在 window 秒内最多失败 limit 次」的计数器。

    用法（登录这类场景）：

        if (wait := LOGIN.retry_after(key)) > 0:  → 429
        ... 校验失败 → LOGIN.fail(key)
        ... 校验成功 → LOGIN.reset(key)

    只统计**失败**：成功登录不该消耗额度，否则正常用户反复登录也会被锁。
    """

    def __init__(self, name: str, limit: int, window: float):
        self.name = name
        self.limit = limit
        self.window = float(window)

    def _key(self, key: str) -> str:
        return f"{self.name}\x1f{key}"

    def retry_after(self, key: str) -> int:
        """还要等多少秒才允许再试；0 表示当前可以试。"""
        k = self._key(key)
        now = time.monotonic()
        with _LOCK:
            entry = _BUCKETS.get(k)
            if entry is None:
                return 0
            dq = entry[0]
            _prune(dq, now, self.window)
            entry[1] = now
            if len(dq) < self.limit:
                return 0
            # 最早一次失败滑出窗口时即解锁
            remain = dq[0] + self.window - now
            return max(1, int(remain + 0.999))

    def fail(self, key: str) -> None:
        """记一次失败。"""
        k = self._key(key)
        now = time.monotonic()
        with _LOCK:
            entry = _BUCKETS.get(k)
            if entry is None:
                _evict_if_needed(now)
                entry = [deque(), now, self.window, self.limit]
                _BUCKETS[k] = entry
            dq = entry[0]
            _prune(dq, now, self.window)
            dq.append(now)
            while len(dq) > _MAX_EVENTS_PER_KEY:
                dq.popleft()
            entry[1] = now

    def reset(self, key: str) -> None:
        """清空该 key 的失败记录（登录成功后调用）。"""
        with _LOCK:
            _BUCKETS.pop(self._key(key), None)


#: 馆员登录：同一账号 15 分钟内失败 5 次 → 锁 15 分钟。
#: 6 位最小口令 + 5 次额度，靠在线爆破拿下一个账号的期望时间被推到天文数字，
#: 而正常馆员「输错几次」几乎不可能触发。
#:
#: ## 已知取舍：硬锁可以被用来「定向踢人」
#:
#: 同网段的人连错 5 次就能把某个账号锁 15 分钟，知道口令的馆员也会被挡在门外。
#: 曾试过「锁定后每 10 秒仍放一次校验」来缓解，但那样把日爆破预算从约 480 次
#: 放大到约 8600 次（19 倍）——用真实的爆破风险换一点可用性，不划算，已撤回。
#:
#: 现状：接受这个可用性问题，并给出解法——**重启后端立即清零**（计数在内存里）。
#: 学校场景里这比放松限速更安全：锁 15 分钟是可恢复的麻烦，口令被撞开不是。
LOGIN_BY_USER = Limiter("login:user", limit=5, window=15 * 60)

#: 读者门户：借书证号可预测（多为递增数字），比馆员账号更需要限速。
PORTAL_BY_CARD = Limiter("portal:card", limit=5, window=15 * 60)


def reset_all() -> None:
    """清空全部计数。**仅供测试使用**，不要在业务代码里调用。"""
    with _LOCK:
        _BUCKETS.clear()


# ---------------------------------------------------------------------------
#  口令校验并发闸门（**async**，在事件循环上排队）
# ---------------------------------------------------------------------------
#
# 单次 scrypt 实测约 190ms、峰值内存 16 MiB。登录是**公开端点**，不限并发时
# 几十个并发请求就能把线程池（默认 40）和内存一起打满——这是拒绝服务，不需要
# 猜中任何口令。
#
# ## 为什么必须是 async 而不是 threading.Semaphore
#
# 第一版用的是 `threading.BoundedSemaphore`，在**同步**路由里 `acquire`。同步
# 路由跑在 Starlette 的线程池里，于是等待闸门的请求**同样占着线程**：40 个并发
# 就能把 40 个线程全堵在 acquire 上，`/api/health` 这种毫不相干的接口排在后面，
# 实测被拖到 6.3 秒。也就是说，登录端点的限流反过来把全站拖垮了。
#
# 改成 async 之后，排队发生在**事件循环**上，不占线程：200 个并发登录只会有
# 4 个进入线程池，其余在协程里等，健康检查照常响应。
#
# 4 个并发 × 16 MiB ≈ 64 MiB 峰值，吞吐约 20 次/秒，远高于任何真实场景的需要
# （一所学校的馆员总数是个位数，读者登录也是零散的）。
#
# 闸门**按事件循环**取：`asyncio.Semaphore` 第一次使用时会绑定当前循环，跨循环
# 复用会抛 RuntimeError。测试里每个 client 一个循环，生产里只有一个——用弱引用
# 字典按循环缓存，循环销毁后条目自动消失。

VERIFY_SLOTS = 4

_VERIFY_SEMS: "weakref.WeakKeyDictionary[object, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary())


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _VERIFY_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(VERIFY_SLOTS)
        _VERIFY_SEMS[loop] = sem
    return sem


async def acquire_verify_slot(timeout: float = 1.0) -> bool:
    """占用一个校验位；等不到返回 False（调用方应回 429，**不要**继续排队）。

    `timeout` 是给真实突发留的余量（4 个并发 × 190ms，5 个同时登录只需等
    不到 200ms）。等不到说明已经在被压，直接拒绝比让用户干等更诚实。
    """
    try:
        await asyncio.wait_for(_semaphore().acquire(), timeout=timeout)
        return True
    except (asyncio.TimeoutError, TimeoutError):
        return False


def release_verify_slot() -> None:
    _semaphore().release()


# —— 同步（线程池）侧的校验闸门 ——

# async 闸门（acquire_verify_slot）只能在事件循环上取；setup / register / 改密码 /
# 建用户 / 改用户这些**同步路由**跑在线程池里，拿不到 running loop。而这些端点同样
# 要跑一次 16 MiB 的 scrypt——不设闸的话，开放自助注册后匿名并发打 register、或
# 低权限馆员连环改密，async 侧的防线等于被绕过。
# 这里用 threading.Semaphore 与 async 侧同参数（4 槽）。两边各自限 4，最坏
# 8 并发 × 16 MiB ≈ 128 MiB，仍远低于攻击者在这几个端点上能凑出的内存压力；
# 相比把全部同步路由改成 async + run_in_threadpool（login 的做法），侵入小得多。
_VERIFY_GATE_SYNC = threading.BoundedSemaphore(VERIFY_SLOTS)


def acquire_verify_slot_sync(timeout: float = 1.0) -> bool:
    """同步路由占用一个校验位；等不到返回 False（调用方应回 429，**不要**排队）。"""
    return _VERIFY_GATE_SYNC.acquire(timeout=timeout)


def release_verify_slot_sync() -> None:
    _VERIFY_GATE_SYNC.release()
