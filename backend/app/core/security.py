# -*- coding: utf-8 -*-
"""口令哈希与会话令牌（标准库实现，不引入第三方认证库）。

为什么不加 passlib / bcrypt / python-jose：
  - 运行依赖里本来就没有任何认证依赖（项目此前刻意不做登录）；
  - 单机部署、账号数以「个」计，哈希成本不是瓶颈，不值得多三个依赖；
  - `hashlib.scrypt` 是标准库且是**内存硬**的（128·n·r 字节内存/次），抗 GPU 爆破
    强于 pbkdf2，参数还能随硬件升级写进哈希串里。

哈希串是**自描述**的，便于以后换参数或换算法时平滑迁移：

    scrypt$<n>$<r>$<p>$<b64(salt)>$<b64(dk)>

`verify_password` 只认 `scrypt$` 前缀；解析失败一律返回 False —— **绝不抛异常**。
脏哈希不该让登录接口 500（那会把「密码不对」暴露成「服务器错误」，也给了
构造畸形哈希串做拒绝服务的机会），它就应该安静地表现为「密码不对」。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGO = "scrypt"

# 参数：n=2^14, r=8, p=1 → 每次 16 MiB 内存、约 10~30ms。
# 这是登录接口能接受的量级，同时把离线爆破的成本抬到「每猜一个密码十几毫秒 + 16MiB」。
_N = 2 ** 14
_R = 8
_P = 1
_SALT_BYTES = 16
_DK_LEN = 32
# 显式给 maxmem：OpenSSL 默认上限 32 MiB，正好是 128·n·r=16 MiB 的两倍，
# 看着够用，但一旦有人调大 n 就会撞上「memory limit exceeded」这种难懂的报错。
_MAXMEM = 128 * 1024 * 1024

# 会话令牌：32 字节随机 → urlsafe 43 字符。库里只存它的 SHA-256（见 token_hash）。
TOKEN_BYTES = 32

MIN_PASSWORD_LEN = 6
MAX_PASSWORD_LEN = 128
MAX_USERNAME_LEN = 64
MAX_DISPLAY_NAME_LEN = 64

# 哑哈希的口令本身没有意义（任何人都不能用它登录成功，见 verify_password）；
# 它只是让「账号不存在」这条路径跑满一次真实的 scrypt。
_DUMMY_PASSWORD = "dummy-password-for-timing-equalization"
_DUMMY_HASH: str | None = None


def _b64e(raw: bytes) -> str:
    """urlsafe base64 去掉 `=` 填充（哈希串里分隔符是 `$`，留着 `=` 只是噪音）。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """生成自描述哈希串。密码先 UTF-8 编码——中文密码必须能存能验。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P,
                        dklen=_DK_LEN, maxmem=_MAXMEM)
    return f"{_ALGO}${_N}${_R}${_P}${_b64e(salt)}${_b64e(dk)}"


def _dummy_hash() -> str:
    """固定哑哈希（惰性生成一次）。用途见 `verify_password`。"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(_DUMMY_PASSWORD)
    return _DUMMY_HASH


def verify_password(password: str, stored: str | None) -> bool:
    """校验口令。任何解析/计算异常都归一为 False（见模块 docstring）。

    ## 为什么 `stored` 为空时还要算一次 scrypt

    「账号不存在」若直接返回，会比「密码错误」快一个数量级（实测 0.003 ms
    vs 187 ms，约 6 万倍），于是响应时间本身就成了一个账号枚举接口——先筛出
    有效用户名，再针对性爆破。这里改用一份**固定哑哈希**把成本补齐到与真实
    校验完全一致。

    用真跑 scrypt 而不是 `time.sleep(常量)`：sleep 的时长与真实成本无关，
    一旦机器换了、或者 scrypt 参数调整了，时序差立刻重新出现；用同一套参数
    算一遍，成本天然对齐，也不需要维护一个魔法数字。

    哑哈希惰性生成（首次用到才算），避免拖慢模块导入；算出的结果**恒为
    False**，绝不因为「密码恰好等于哑口令」而误放行。
    """
    is_dummy = not stored or not isinstance(stored, str)
    if is_dummy:
        stored = _dummy_hash()
    try:
        algo, n_s, r_s, p_s, salt_s, dk_s = stored.split("$")
        if algo != _ALGO:
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt, expected = _b64d(salt_s), _b64d(dk_s)
        if n < 2 or (n & (n - 1)) or r < 1 or p < 1 or not salt or not expected:
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                             dklen=len(expected), maxmem=_MAXMEM)
    except Exception:
        return False
    if is_dummy:
        # 成本已补齐，但「不存在的账号」永远不能通过
        return False
    return hmac.compare_digest(got, expected)


def new_token() -> str:
    """新会话令牌（明文只在这一刻存在，随后只留哈希）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    """令牌 → 库里的主键。SHA-256 足够：令牌本身是 256 位随机数，不需要抗爆破的慢哈希。

    （口令要 scrypt 是因为人选的口令熵低；令牌是 CSPRNG 生成的，暴力搜索不可行。）
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_problem(password: str) -> str | None:
    """口令合法性检查；通过返回 None，否则返回**给人看的中文说明**。

    不设复杂度要求（大小写/数字/符号）：这类规则实测只会催生 `Abc123!` 这种
    在字典里的口令，长度才是真正有效的约束。
    """
    if not isinstance(password, str) or not password:
        return "密码不能为空"
    if len(password) < MIN_PASSWORD_LEN:
        return f"密码至少 {MIN_PASSWORD_LEN} 位"
    if len(password) > MAX_PASSWORD_LEN:
        return f"密码不能超过 {MAX_PASSWORD_LEN} 位"
    return None


def normalize_username(raw) -> str | None:
    """用户名归一：去空白、转小写、限长；非法返回 None。

    转小写是为了「Admin 与 admin 是同一个账号」——不转的话 PG 的 unique 约束
    区分大小写，会冒出两个只差大小写的账号，登录时输错一个就登不进去。
    """
    if not isinstance(raw, str):
        return None
    name = raw.strip().lower()
    if not name or len(name) > MAX_USERNAME_LEN:
        return None
    if any(c.isspace() for c in name):
        return None
    return name
