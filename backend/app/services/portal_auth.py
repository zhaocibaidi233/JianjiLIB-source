# -*- coding: utf-8 -*-
"""读者门户账号与会话。

与馆员登录**完全分离**：读者账号不写入 `users`，不影响 `auth_enabled()`，
也不参与馆员模块权限。令牌同样只存 SHA-256，明文只在登录响应里出现一次。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..core.security import (hash_password, new_token, password_problem,
                             token_hash, verify_password)
from ..models import READER_INACTIVE, ReaderAccount, ReaderORM, ReaderSession
from .auth import SESSION_DAYS, token_from_request
from .circulation import READER_STATUS_LABELS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _public_reader(reader: ReaderORM) -> dict:
    return {
        "id": reader.id,
        "library_id": reader.library_id,
        "card_no": reader.card_no,
        "name": reader.name,
        "reader_type": reader.reader_type,
        "grade": reader.grade,
        "class_name": reader.class_name,
        "phone": reader.phone,
        "status": reader.status,
        "status_label": READER_STATUS_LABELS.get(reader.status, reader.status),
    }


def account_for(db: Session, reader_id: int) -> ReaderAccount | None:
    return (db.query(ReaderAccount)
            .filter(ReaderAccount.reader_id == reader_id).first())


def account_info(db: Session, reader_id: int) -> dict | None:
    account = account_for(db, reader_id)
    if account is None:
        return None
    return {"username": account.username, "enabled": bool(account.enabled),
            "last_login_at": str(account.last_login_at) if account.last_login_at else None}


def provision_account(db: Session, reader: ReaderORM, password: str) -> ReaderAccount:
    """开通或重置读者账号；初始账号 = 借书证号（小写）。**不 commit**。

    ## 登录名为什么只在**库内**唯一

    借书证号本身只在库内唯一（`uq_readers_library_card_no`）：两个图书库各自
    从 0001 编起是正常状态，`_find_reader_by_card` 那句「该借书证号在多个图书库
    中存在，请先选择图书库」就是为这个场景写的。

    所以登录名的唯一性口径必须跟着证号走。原来按**全局**唯一判，建第二个图书库
    之后同号读者开通门户必然失败，而报错文案「已被其他账号占用」会把馆员引去
    查一个不存在的账号——占用者其实在另一个库里，本库账号列表根本看不到它。
    """
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    username = reader.card_no.strip().lower()
    if not username:
        raise ValueError("借书证号为空，无法开通账号")
    if password.strip().lower() == username:
        # 证号是公开信息（贴在借书证上），拿它当密码等于没有密码。这是现场
        # 最常见的初始口令选择，也是最容易被同学猜到的那个。
        raise ValueError("初始密码不能与借书证号相同，请另设一个")
    other = (db.query(ReaderAccount)
             .join(ReaderORM, ReaderORM.id == ReaderAccount.reader_id)
             .filter(ReaderAccount.username == username,
                     ReaderORM.library_id == reader.library_id,
                     ReaderAccount.reader_id != reader.id).first())
    if other is not None:
        raise ValueError("本图书库内该借书证号对应的登录名已被其他账号占用")
    account = account_for(db, reader.id)
    if account is None:
        account = ReaderAccount(reader_id=reader.id, username=username,
                                password_hash=hash_password(password))
        db.add(account)
    else:
        account.username = username
        account.password_hash = hash_password(password)
        account.enabled = True
        # 馆员重置密码后旧会话必须失效，否则原密码泄露期间签发的令牌仍然可用
        purge_sessions(db, reader.id)
    db.flush()
    return account


def disable_account(db: Session, reader_id: int) -> bool:
    account = account_for(db, reader_id)
    if account is None:
        return False
    account.enabled = False
    db.query(ReaderSession).filter(ReaderSession.reader_id == reader_id).delete(
        synchronize_session=False)
    db.flush()
    return True


def purge_sessions(db: Session, reader_id: int) -> int:
    count = (db.query(ReaderSession)
             .filter(ReaderSession.reader_id == reader_id)
             .delete(synchronize_session=False))
    db.flush()
    return int(count)


def create_session(db: Session, reader: ReaderORM,
                   client: str | None = None) -> str:
    token = new_token()
    now = _now()
    db.add(ReaderSession(token_hash=token_hash(token), reader_id=reader.id,
                         created_at=now,
                         expires_at=now + timedelta(days=SESSION_DAYS),
                         last_seen_at=now,
                         client=(client or "")[:256] or None))
    account = account_for(db, reader.id)
    if account is not None:
        account.last_login_at = now
    db.commit()
    return token


def login(db: Session, card_no: str, password: str,
          library_id: int | None = None,
          client: str | None = None) -> tuple[str, ReaderORM]:
    """门户登录。

    ## 判据顺序：先证明「知道口令」，再回答「你是谁 / 为什么进不去」

    旧实现的顺序是「查账号 → 停用/注销/多库 → 验口令」，于是任何**错口令**都能
    拿到比「借书证号或密码不正确」更具体的信息：

    - 账号停用 → 「读者账号已停用」；
    - 读者注销 → 「该读者已注销」；
    - 同号跨库 → 「请指定图书库」。

    借书证号多为「年份 + 流水号」，这是可以猜的：一次错口令就能判定某个证号是否
    存在、是否被停用，等于把全校证号名单逐步筛出来。现在口令错一律同一句话；只有
    口令正确（已经证明自己是本人）才告诉他停用/注销/该选哪个图书库。

    代价：多库账号要逐个比对哈希（最多 2 个）。这个分支本来就罕见，多一次 scrypt
    换掉一个枚举面是划算的。
    """
    username = (card_no or "").strip().lower()
    if not username or not password:
        raise ValueError("借书证号与密码不能为空")
    query = (db.query(ReaderAccount, ReaderORM)
             .join(ReaderORM, ReaderORM.id == ReaderAccount.reader_id)
             .filter(ReaderAccount.username == username))
    if library_id:
        query = query.filter(ReaderORM.library_id == library_id)
    rows = query.limit(2).all()
    if not rows:
        # 证号不存在时也要跑满一次 scrypt。不跑的话「证号不存在」比「口令错」
        # 快一个数量级（实测 5.5ms vs 166.7ms，约 30 倍），单次请求就能把有效
        # 借书证号筛出来——而证号多为「年份+流水号」，筛出来就等于拿到学生名单。
        # 馆员登录那边已经这样处理（见 core/security.py::verify_password）。
        verify_password(password, None)
        raise PermissionError("借书证号或密码不正确")

    matched = [row for row in rows if verify_password(password, row[0].password_hash)]
    if not matched:
        raise PermissionError("借书证号或密码不正确")
    if len(rows) > 1:
        if len(matched) == 1:
            # 跨库同号但只有一个库的账号认这个口令 → 没有歧义，直接进那个库。
            # 只有两个库都认同一个口令时才要求用户指明（这时才真的分不出来）。
            rows = matched
        else:
            raise ValueError("该借书证号在多个图书库中存在，请指定图书库")

    account, reader = matched[0]
    if not account.enabled:
        raise PermissionError("读者账号已停用，请联系图书馆")
    if reader.status == READER_INACTIVE:
        raise PermissionError("该读者已注销，不能登录读者门户")
    token = create_session(db, reader, client=client)
    return token, reader


def resolve_session(db: Session, token: str | None) -> ReaderORM | None:
    if not token:
        return None
    session = db.get(ReaderSession, token_hash(token))
    if session is None:
        return None
    if _aware(session.expires_at) < _now():
        db.delete(session)
        db.commit()
        return None
    reader = db.get(ReaderORM, session.reader_id)
    if reader is None or reader.status == READER_INACTIVE:
        return None
    account = account_for(db, reader.id)
    if account is None or not account.enabled:
        return None
    return reader


def reader_from_request(db: Session, request) -> ReaderORM | None:
    return resolve_session(db, token_from_request(request))


def logout(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.get(ReaderSession, token_hash(token))
    if row is not None:
        db.delete(row)
        db.commit()


def change_password(db: Session, reader: ReaderORM, old_password: str,
                    new_password: str, client: str | None = None) -> str:
    account = account_for(db, reader.id)
    if account is None:
        raise ValueError("账号不存在")
    if not verify_password(old_password, account.password_hash):
        raise ValueError("原密码不正确")
    problem = password_problem(new_password)
    if problem:
        raise ValueError(problem)
    if new_password == old_password:
        raise ValueError("新密码不能与原密码相同")
    account.password_hash = hash_password(new_password)
    purge_sessions(db, reader.id)
    token = create_session(db, reader, client=client)
    return token


def to_public(reader: ReaderORM) -> dict:
    return _public_reader(reader)
