# -*- coding: utf-8 -*-
"""开发演示数据：图书库 / 书目 / 馆藏 / 读者 / 借阅 / 推荐。

只用于 jianji_demo 这类演示库，**不要在生产库执行**。脚本幂等：库里已有
`source == "demo"` 的书目时直接退出。需要重来请先手动清空开发库。

## 口令策略

演示账号口令不会写死在源码中。默认随机生成 12 位口令，跑完打印一次：

  - 默认**随机生成**一个 12 位口令，跑完打印一次，请立刻记下；
  - 需要固定口令时显式 `--password`，并且必须自己承担「这个口令会出现在
    屏幕与命令历史里」的后果；
  - 写入前要求确认（非交互环境必须加 `--yes`），避免误在生产库上跑。

用法：
    cd backend
    python scripts/seed_demo.py                 # 随机口令 + 交互确认
    python scripts/seed_demo.py --yes           # 随机口令，不确认（脚本/CI）
    python scripts/seed_demo.py --password xxx --yes
"""
from __future__ import annotations

import argparse
import secrets
import string
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    LOAN_ACTIVE, LOAN_RETURNED, READER_ACTIVE, READER_SUSPENDED,
    RESERVATION_PENDING, RESERVATION_READY, SEAT_BOOKED, BookLibrary,
    HoldingORM, LoanORM, ReaderORM, RecommendationORM, RecordORM,
    ReservationORM, Seat, SeatArea, SeatBooking, User,
)
from app.services.portal_auth import provision_account  # noqa: E402
from app.services import record_from_payload, record_to_orm_values  # noqa: E402
from app.services.auth import (  # noqa: E402
    admin_role, ensure_default_roles, make_user, seed_first_admin,
)
from app.services.batch import create_holdings  # noqa: E402

LIBRARY_NAME = "简籍示范中学"
ADMIN_USER = "admin"
ADMIN_DISPLAY = "演示管理员"


def _random_password(length: int = 12) -> str:
    """随机演示口令。只用字母数字：避免现场抄写时被 `l/1/O/0` 或符号绊住。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


BOOKS = [
    # title, author, isbn, publisher, place, year, class_no, price, pages, copies
    ("设计中的设计", "原研哉", "9787563394180", "广西师范大学出版社", "桂林", 2010, "TB47", "58.00", "312", 2),
    ("三体", "刘慈欣", "9787536692930", "重庆出版社", "重庆", 2008, "I247.55", "23.00", "302", 4),
    ("活着", "余华", "9787506365437", "作家出版社", "北京", 2012, "I247.57", "28.00", "191", 3),
    ("人类简史", "尤瓦尔·赫拉利", "9787508647357", "中信出版社", "北京", 2014, "K02", "68.00", "440", 3),
    ("置身事内", "兰小欢", "9787208171336", "上海人民出版社", "上海", 2021, "F124", "65.00", "340", 2),
    ("百年孤独", "加西亚·马尔克斯", "9787544253994", "南海出版公司", "海口", 2011, "I775.45", "39.50", "360", 2),
    ("月亮与六便士", "毛姆", "9787532757862", "上海译文出版社", "上海", 2012, "I561.45", "32.00", "294", 2),
    ("小王子", "圣埃克苏佩里", "9787020042494", "人民文学出版社", "北京", 2003, "I565.88", "22.00", "186", 3),
    ("朝花夕拾", "鲁迅", "9787020008735", "人民文学出版社", "北京", 1979, "I210.4", "18.00", "168", 2),
    ("昆虫记", "法布尔", "9787544768358", "译林出版社", "南京", 2017, "Q96-49", "45.00", "412", 2),
    ("时间简史", "史蒂芬·霍金", "9787535732309", "湖南科学技术出版社", "长沙", 2010, "P159-49", "45.00", "238", 2),
    ("数学之美", "吴军", "9787115373557", "人民邮电出版社", "北京", 2014, "TP301.6", "49.00", "312", 2),
    ("万历十五年", "黄仁宇", "9787108009821", "生活·读书·新知三联书店", "北京", 1997, "K248.3", "18.00", "286", 2),
    ("乡土中国", "费孝通", "9787108014252", "生活·读书·新知三联书店", "北京", 2013, "C912.82", "28.00", "156", 3),
    ("艺术的故事", "贡布里希", "9787807463726", "广西美术出版社", "南宁", 2008, "J110.9", "280.00", "688", 2),
    ("窗边的小豆豆", "黑柳彻子", "9787544250580", "南海出版公司", "海口", 2011, "I313.84", "25.00", "236", 3),
    ("平凡的世界", "路遥", "9787530216781", "北京十月文艺出版社", "北京", 2017, "I247.57", "108.00", "1232", 4),
    ("呼兰河传", "萧红", "9787020104345", "人民文学出版社", "北京", 2018, "I246.5", "26.00", "248", 2),
    ("苏东坡传", "林语堂", "9787544250153", "南海出版公司", "海口", 2012, "K825.6", "36.00", "354", 2),
    ("如何阅读一本书", "莫提默·艾德勒", "9787100040945", "商务印书馆", "北京", 2004, "G792", "38.00", "412", 2),
]

READERS = [
    ("R20260001", "陈知遥", "teacher", None, "语文组", "13800001001"),
    ("R20260002", "周雨桐", "student", "七年级", "七年级(3)班", "13800001002"),
    ("R20260003", "林子航", "student", "七年级", "七年级(3)班", "13800001003"),
    ("R20260004", "许清和", "student", "七年级", "七年级(5)班", "13800001004"),
    ("R20260005", "沈星野", "student", "八年级", "八年级(1)班", "13800001005"),
    ("R20260006", "顾南乔", "student", "八年级", "八年级(2)班", "13800001006"),
    ("R20260007", "苏晚晴", "teacher", None, "数学组", "13800001007"),
    ("R20260008", "陆时安", "student", "高一", "高一(1)班", "13800001008"),
    ("R20260009", "宋予安", "student", "高一", "高一(2)班", "13800001009"),
    ("R20260010", "唐听澜", "student", "高二", "高二(4)班", "13800001010"),
    ("R20260011", "江照月", "teacher", None, "英语组", "13800001011"),
    ("R20260012", "叶书言", "student", "高二", "高二(1)班", "13800001012"),
    ("R20260013", "程墨白", "staff", None, "教务处", "13800001013"),
    ("R20260014", "温以宁", "student", "七年级", "七年级(1)班", "13800001014"),
    ("R20260015", "夏拾光", "student", "八年级", "八年级(6)班", "13800001015"),
    ("R20260016", "何清越", "student", "高一", "高一(5)班", "13800001016"),
]

RECOMMEND_NOTES = {
    "设计中的设计": "把“设计”从造型拉回到生活本身，适合每一个开始观察日常的人。",
    "三体": "宏大的宇宙想象与冷静的人性追问，中文科幻绕不开的一本。",
    "人类简史": "用一条清晰的线索，把人类如何走到今天讲得好看又耐想。",
    "乡土中国": "理解中国社会的底层结构，薄薄一册，常读常新。",
    "艺术的故事": "最好的艺术入门书之一，像听一位耐心的长者讲完整个人类艺术史。",
}

LOCATION_BY_CLASS = {
    "I": "3F 文学区",
    "K": "2F 历史区",
    "F": "2F 社科区",
    "C": "2F 社科区",
    "G": "2F 社科区",
    "T": "2F 科技区",
    "Q": "2F 科技区",
    "P": "2F 科技区",
    "J": "3F 艺术区",
}


def _fields(book: tuple) -> dict:
    title, author, isbn, publisher, place, year, class_no, price, pages, _copies = book
    stamp = date.today().strftime("%Y%m%d")
    general = f"{stamp}d{year}    em y0chiy0110    ea"
    assert len(general) == 36, len(general)
    return {
        "leader": "00000nam0 2200000   450 ",
        "fields": [
            {"tag": "001", "data": isbn[-8:]},
            {"tag": "010", "i1": " ", "i2": " ", "sf": [["a", isbn], ["d", f"CNY{price}"]]},
            {"tag": "100", "i1": " ", "i2": " ", "sf": [["a", general]]},
            {"tag": "101", "i1": "0", "i2": " ", "sf": [["a", "chi"]]},
            {"tag": "102", "i1": " ", "i2": " ", "sf": [["a", "CN"]]},
            {"tag": "105", "i1": " ", "i2": " ", "sf": [["a", "y   z   000yy"]]},
            {"tag": "200", "i1": "1", "i2": " ", "sf": [["a", title], ["f", author]]},
            {"tag": "210", "i1": " ", "i2": " ", "sf": [["a", place], ["c", publisher], ["d", str(year)]]},
            {"tag": "215", "i1": " ", "i2": " ", "sf": [["a", pages], ["d", "21cm"]]},
            {"tag": "606", "i1": "0", "i2": " ", "sf": [["a", "课外阅读"]]},
            {"tag": "690", "i1": " ", "i2": " ", "sf": [["a", class_no]]},
            {"tag": "801", "i1": " ", "i2": " ", "sf": [["a", "CN"], ["b", LIBRARY_NAME], ["c", "20261022"]]},
        ],
    }


def _at(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute)).astimezone()


def seed(password: str) -> None:
    """灌演示数据。`password` 是本次给所有演示账号（管理员 + 读者门户）设的口令。"""
    db = SessionLocal()
    try:
        existing = db.query(RecordORM).filter(RecordORM.source == "demo").count()
        if existing:
            print(f"演示数据已存在（{existing} 条 demo 书目），跳过。")
            return

        ensure_default_roles(db)
        role = admin_role(db)
        if role is None:
            raise SystemExit("找不到 admin 角色")
        if db.query(User).filter(User.username == ADMIN_USER).first() is None:
            if db.query(User).count() == 0:
                seed_first_admin(db, username=ADMIN_USER, password=password,
                                 display_name=ADMIN_DISPLAY)
            else:
                db.add(make_user(db, username=ADMIN_USER, display_name=ADMIN_DISPLAY,
                                 password=password, role=role))
                db.commit()
            # 口令只在交互终端显示：启动器会把输出收进日志文件，
            # 非交互环境请用 --password 显式传入，或到交互终端重跑。
            if sys.stdout.isatty():
                print(f"管理员：{ADMIN_USER} / {password}")
            else:
                print(f"管理员 {ADMIN_USER} 已创建；口令未回显（当前输出会被"
                      " launcher 收进日志文件）。如需查看请重新运行："
                      f"python scripts/seed_demo.py --password <口令> --yes")

        library = db.query(BookLibrary).filter(BookLibrary.name == LIBRARY_NAME).first()
        if library is None:
            library = BookLibrary(name=LIBRARY_NAME, note="开发演示馆")
            db.add(library)
            db.flush()

        today = date.today()
        barcode_seq = 26000000
        records: list[RecordORM] = []
        holdings: list[HoldingORM] = []

        for book in BOOKS:
            title = book[0]
            payload = _fields(book)
            rec = record_from_payload(payload)
            values = record_to_orm_values(rec, source="demo", status="final")
            values["created_by"] = ADMIN_DISPLAY
            orm = RecordORM(library_id=library.id, **values)
            db.add(orm)
            db.flush()

            class_no = book[6]
            base_loc = LOCATION_BY_CLASS.get(class_no[:1].upper(), "2F 综合区")
            barcodes = []
            for _ in range(book[9]):
                barcode_seq += 1
                barcodes.append(f"{barcode_seq:08d}")
            # 地点只放库室，架位号单独写 `shelf_no`——把架位塞进地点是 Phase 6 之前
            # 的旧写法，迁移 `e3b7c1d5f9a2` 会把它当冗余清掉，等于白写。
            shelf_no = f"{chr(65 + (len(records) % 6))}-{len(records) % 12 + 1:02d}"
            created = create_holdings(db, orm, len(barcodes), barcodes=barcodes,
                                      location=base_loc, shelf_no=shelf_no)
            records.append(orm)
            holdings.extend(created)
            print(f"  + {title} / {class_no} / {len(created)} 册")

        db.flush()

        readers: list[ReaderORM] = []
        for i, (card, name, rtype, grade, klass, phone) in enumerate(READERS):
            reader = ReaderORM(
                library_id=library.id, card_no=card, name=name, reader_type=rtype,
                grade=grade, class_name=klass, phone=phone,
                status=READER_SUSPENDED if i == 13 else READER_ACTIVE,
                note="开发演示读者" if i == 13 else None,
            )
            db.add(reader)
            readers.append(reader)
        db.flush()

        # 12 笔在借：3 笔逾期，4 笔今日借出，其余在借期内
        active_plan = [
            (0, -12, 0), (1, -6, 1), (2, -3, 2),
            (3, 1, 0), (4, 3, 3), (5, 5, 4), (6, 7, 5), (7, 10, 6),
            (8, 20, 8), (9, 24, 9), (10, 28, 10), (11, 30, 11),
        ]
        for holding_idx, due_offset, reader_idx in active_plan:
            h = holdings[holding_idx]
            rec = next(r for r in records if r.id == h.record_id)
            reader = readers[reader_idx]
            borrowed_today = reader_idx in (8, 9, 10, 11)
            loaned = _at(today, 9, 20) if borrowed_today else _at(today - timedelta(days=20), 10, 0)
            db.add(LoanORM(
                library_id=library.id, holding_id=h.id, record_id=h.record_id,
                barcode=h.barcode, title=rec.title_norm,
                reader_id=reader.id, reader_card_no=reader.card_no,
                reader_name=reader.name, reader_class=reader.class_name,
                loaned_at=loaned, due_on=today + timedelta(days=due_offset),
                status=LOAN_ACTIVE, operator_loan=ADMIN_DISPLAY,
                fine_amount=Decimal("0.00"), compensation_amount=Decimal("0.00"),
            ))

        # 借阅历史：每天 2 笔归还铺满近 14 天，另 2 笔落在今天；
        # 借期取 1-7 天，让借出与归还两条曲线都有连续分布
        history_titles = [0, 1, 2, 2, 3, 3, 4, 5, 6, 7, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 16, 17, 18, 19, 1, 2, 3, 15, 16, 0]
        for i, holding_idx in enumerate(history_titles):
            h = holdings[holding_idx % len(holdings)]
            rec = next(r for r in records if r.id == h.record_id)
            reader = readers[i % len(readers)]
            days_ago = i // 2 if i < 28 else 0
            returned = _at(today - timedelta(days=days_ago), 15, 30)
            loaned = _at(today - timedelta(days=days_ago + 1 + i % 7), 10, 0)
            db.add(LoanORM(
                library_id=library.id, holding_id=h.id, record_id=h.record_id,
                barcode=h.barcode, title=rec.title_norm,
                reader_id=reader.id, reader_card_no=reader.card_no,
                reader_name=reader.name, reader_class=reader.class_name,
                loaned_at=loaned, due_on=loaned.date() + timedelta(days=30),
                returned_at=returned, status=LOAN_RETURNED,
                operator_loan=ADMIN_DISPLAY, operator_return=ADMIN_DISPLAY,
                renew_count=1 if i % 5 == 0 else 0,
                fine_amount=Decimal("0.00"), compensation_amount=Decimal("0.00"),
            ))

        by_title = {r.title_norm: r for r in records}
        for order, (title, note) in enumerate(RECOMMEND_NOTES.items()):
            rec = by_title.get(title)
            if rec is None:
                continue
            db.add(RecommendationORM(
                library_id=library.id, record_id=rec.id, note=note,
                sort_order=order, created_by=ADMIN_DISPLAY,
            ))

        # 两条演示预约：一条排队中，一条已到书（保留至 +2 天）。
        # 预约对象是书目，读者到馆取任意一册在架副本即可。
        db.add(ReservationORM(
            library_id=library.id, record_id=by_title["三体"].id,
            reader_id=readers[2].id, status=RESERVATION_PENDING,
            reserved_at=_at(today - timedelta(days=3), 14, 10),
            created_by=ADMIN_DISPLAY))
        db.add(ReservationORM(
            library_id=library.id, record_id=by_title["设计中的设计"].id,
            reader_id=readers[3].id, status=RESERVATION_READY,
            reserved_at=_at(today - timedelta(days=5), 9, 40),
            ready_at=_at(today - timedelta(days=1), 15, 30),
            expires_at=_at(today + timedelta(days=2), 15, 30),
            note="已电话通知读者", created_by=ADMIN_DISPLAY))

        # 读者门户账号（演示）：登录名 = 借书证号，口令与管理员同一个
        for reader in readers[:2]:
            provision_account(db, reader, password)

        # 座位区域与座位；预约时段从下一个完整的 2 小时段开始
        area_specs = [
            ("2F 静默自习区", "2F", "安静阅读与自习", 6, 8, "quiet"),
            ("3F 开放阅览区", "3F", "开放阅览与小组讨论", 6, 8, "normal"),
            ("4F 电子阅览区", "4F", "检索与数字资源使用", 4, 6, "computer"),
        ]
        seat_sets: list[tuple[SeatArea, list[Seat]]] = []
        for name, floor, desc, rows, cols, kind in area_specs:
            area = SeatArea(library_id=library.id, name=name, floor=floor,
                            description=desc, rows=rows, cols=cols,
                            open_time=time(8, 0), close_time=time(22, 0))
            db.add(area)
            db.flush()
            seats = []
            for r in range(rows):
                for c in range(cols):
                    seat = Seat(area_id=area.id, code=f"{chr(65 + r)}-{c + 1:02d}",
                                row=r, col=c, kind=kind)
                    db.add(seat)
                    seats.append(seat)
            db.flush()
            seat_sets.append((area, seats))

        slot_day = today
        slot_start = None
        now = datetime.now().astimezone()
        for hour in (8, 10, 12, 14, 16, 18, 20):
            if _at(today, hour, 0) > now + timedelta(minutes=45):
                slot_start = time(hour, 0)
                break
        if slot_start is None:
            slot_day = today + timedelta(days=1)
            slot_start = time(8, 0)
        slot_end = time(slot_start.hour + 2, 0)
        area_one, seats_one = seat_sets[0]
        for index, reader in enumerate(readers[:4]):
            seat = seats_one[2 + index]
            db.add(SeatBooking(
                library_id=library.id, seat_id=seat.id, reader_id=reader.id,
                booking_date=slot_day, start_time=slot_start, end_time=slot_end,
                status=SEAT_BOOKED))

        db.commit()
        print(f"完成：书目 {len(records)} 条，馆藏 {len(holdings)} 册，"
              f"读者 {len(readers)} 位，在借 12 笔，历史借阅 {len(history_titles)} 笔。")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="灌开发演示数据（不要在生产库执行）")
    ap.add_argument("--password", help="统一口令；不给则随机生成 12 位")
    ap.add_argument("--yes", action="store_true",
                    help="跳过确认（非交互环境必须加）")
    args = ap.parse_args(argv)

    password = (args.password or "").strip() or _random_password()

    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit(
                "非交互环境下必须显式加 --yes 才能写入演示数据。\n"
                "这个脚本会创建管理员账号，误跑在生产库上等于给系统留一个后门。")
        print("即将向当前连接的数据库写入演示数据，并创建管理员账号：")
        print(f"   管理员 {ADMIN_USER}，口令 {password}")
        print("⚠ 只应在开发库执行。生产库请跳过本脚本，改在界面里创建管理员。")
        try:
            if input("确认继续请输入 YES：").strip() != "YES":
                print("已取消。")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 1

    seed(password)

    print("")
    print("=" * 56)
    print("  演示账号")
    if sys.stdout.isatty():
        print(f"    管理员   {ADMIN_USER} / {password}")
        print(f"    读者门户 借书证号见「读者」页，口令同为 {password}")
    else:
        print(f"    管理员   {ADMIN_USER}（为免写入日志，口令未回显）")
        print("    读者门户口令与管理员相同；如需指定口令，请使用 --password。")
    print("=" * 56)
    print("  ⚠ 正式使用请勿启用这些账号，或在界面中立即改密。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
