# -*- coding: utf-8 -*-
# ============================================================================
# SQLite数据库操作模块

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


# 数据库文件路径（项目根目录/database/finance_audit.db）
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "database" / "finance_audit.db"
UPLOAD_DIR = PROJECT_ROOT / "uploads"


def get_connection():
    """获取数据库连接，自动创建表（如果不存在）"""
    conn = sqlite3.connect(str(DB_PATH))
    # 开启外键约束
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value):
    """把金额字符串转成 float，失败返回 None"""
    if value is None or value == "":
        return None
    try:
        text = str(value).replace("¥", "").replace("￥", "").replace(",", "").strip()
        return float(text)
    except (TypeError, ValueError):
        return None


def init_db():
    """初始化数据库，创建所有表"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    cursor = conn.cursor()

    # 报销记录表（原有，保持不变）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reimbursement (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL UNIQUE,  -- 发票号码（唯一，用于查重）
            invoice_type TEXT,                    -- 发票类型
            invoice_date TEXT,                    -- 开票日期
            seller_name TEXT,                     -- 销售方名称
            seller_tax_id TEXT,                   -- 销售方税号
            buyer_name TEXT,                      -- 购买方名称
            total_amount REAL,                    -- 价税合计
            employee_name TEXT,                   -- 报销人
            expense_type TEXT,                    -- 费用类型
            status TEXT DEFAULT '待审核',          -- 状态：待审核/已通过/已拒绝
            submit_time TEXT,                     -- 提交时间
            audit_result TEXT                     -- 审核结果（JSON）
        )
    """)

    # 员工表（票夹归属）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_no TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            department TEXT,
            level TEXT NOT NULL DEFAULT '普通员工',
            phone TEXT,
            email TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # 发票表（原文件 + 解析内容 + 票夹状态）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoice (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            file_ext TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER,
            file_sha256 TEXT,
            invoice_type TEXT,
            invoice_species TEXT,
            invoice_number TEXT,
            invoice_date TEXT,
            buyer_name TEXT,
            buyer_tax_id TEXT,
            seller_name TEXT,
            seller_tax_id TEXT,
            amount_without_tax REAL,
            tax_amount REAL,
            tax_rate TEXT,
            total_amount REAL,
            total_amount_cn TEXT,
            drawer TEXT,
            remark TEXT,
            item_name TEXT,
            parsed_json TEXT,
            parse_status TEXT NOT NULL DEFAULT 'success',
            parse_error TEXT,
            audit_result TEXT,                     -- Agent 审核结果（JSON：结论/审核文本/明细）
            folder_status TEXT NOT NULL DEFAULT 'in_folder',
            expense_type TEXT,
            uploaded_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (employee_id) REFERENCES employee(id)
        )
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uk_invoice_number
        ON invoice(invoice_number)
        WHERE invoice_number IS NOT NULL AND invoice_number != ''
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_employee
        ON invoice(employee_id, folder_status)
    """)

    # 兼容旧库：老版本建的表没有 audit_result 列，这里自动补上
    cursor.execute("PRAGMA table_info(invoice)")
    invoice_columns = {row[1] for row in cursor.fetchall()}
    if "audit_result" not in invoice_columns:
        cursor.execute("ALTER TABLE invoice ADD COLUMN audit_result TEXT")

    # 发票明细
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoice_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            line_no INTEGER NOT NULL,
            item_name TEXT,
            spec TEXT,
            unit TEXT,
            quantity TEXT,
            unit_price TEXT,
            amount REAL,
            tax_rate TEXT,
            tax_amount REAL,
            FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_item_invoice ON invoice_item(invoice_id)
    """)
    # 报销单表（统一主表 + 分类扩展）
    # - invoice_id 唯一  → 实现「1 张发票 : 1 张报销单」，不需要额外的关联表
    # - trip_no          → 行程号：同一趟出差的多张单填同一个号，即可按行程分组查看
    # - ext_fields       → 分类扩展字段（JSON）：不同费用类型的专属字段都放这里，
    #                      以后新增费用类型只改配置，不用改表结构
    # - detail_json      → 明细模块（JSON）：发票上已有的商品明细自动注入进来
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expense_form (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_no TEXT NOT NULL UNIQUE,
            employee_id INTEGER NOT NULL,
            invoice_id INTEGER NOT NULL UNIQUE,
            trip_no TEXT,
            expense_type TEXT,
            occur_date TEXT,
            reason TEXT,
            total_amount REAL,
            participants TEXT,
            ext_fields TEXT,
            detail_json TEXT,
            status TEXT NOT NULL DEFAULT '待财务终审',
            audit_result TEXT,
            submit_time TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (employee_id) REFERENCES employee(id),
            FOREIGN KEY (invoice_id) REFERENCES invoice(id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_form_employee ON expense_form(employee_id, trip_no)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_form_invoice ON expense_form(invoice_id)
    """)


    # 预置员工（轻量多员工，不做登录，页面顶部可切换身份）
    preset_employees = [
        ("E001", "张三", "销售部", "普通员工"),
        ("E002", "李四", "销售部", "部门经理"),
        ("E003", "王五", "技术部", "普通员工"),
        ("E004", "赵六", "技术部", "普通员工"),
        ("E005", "财务小王", "财务部", "财务"),
    ]
    now = _now()
    for emp_no, name, dept, level in preset_employees:
        cursor.execute("SELECT id FROM employee WHERE emp_no = ?", (emp_no,))
        if cursor.fetchone() is None:
            cursor.execute(
                """
                INSERT INTO employee (emp_no, name, department, level, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (emp_no, name, dept, level, now, now),
            )

    conn.commit()
    conn.close()
    print(f"数据库初始化完成：{DB_PATH}")


def get_default_employee_id():
    """获取默认员工 ID（E001 张三，票夹暂无登录时使用）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM employee WHERE emp_no = ?", ("E001",))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        init_db()
        return get_default_employee_id()
    return row["id"]


def list_employees():
    """列出所有员工（用于页面顶部的身份切换下拉框）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, emp_no, name, department, level FROM employee WHERE status = 'active' ORDER BY id"
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def is_invoice_exists(invoice_number):
    """
    检查发票号码是否已存在于数据库中（用于重复报销检测）

    参数：
        invoice_number: 发票号码
    返回：
        True=已存在，False=不存在
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM reimbursement WHERE invoice_number = ?",
        (invoice_number,)
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def insert_reimbursement(invoice_data, employee_name="", expense_type=""):
    """
    插入一条报销记录

    参数：
        invoice_data: 发票识别结果字典
        employee_name: 报销人姓名
        expense_type: 费用类型

    返回：
        新记录的ID
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO reimbursement (
                invoice_number, invoice_type, invoice_date,
                seller_name, seller_tax_id, buyer_name,
                total_amount, employee_name, expense_type,
                status, submit_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            invoice_data.get("发票号码", ""),
            invoice_data.get("发票类型", ""),
            invoice_data.get("开票日期", ""),
            invoice_data.get("销售方名称", ""),
            invoice_data.get("销售方税号", ""),
            invoice_data.get("购买方名称", ""),
            float(invoice_data.get("价税合计小写", 0) or 0),
            employee_name,
            expense_type,
            "待审核",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # 发票号码重复
        conn.close()
        return None
    finally:
        conn.close()


def save_invoice_to_folder(
    file_bytes,
    original_filename,
    invoice_data=None,
    parse_status="success",
    parse_error=None,
    employee_id=None,
    audit_result=None,
):
    """
    将原文件与解析结果存入票夹。

    返回：
        {"ok": True, "id": ...} 或 {"ok": False, "error": "..."}
    """
    if employee_id is None:
        employee_id = get_default_employee_id()

    invoice_data = invoice_data or {}
    invoice_number = (invoice_data.get("发票号码") or "").strip()
    now = _now()
    file_ext = Path(original_filename).suffix.lstrip(".").lower() or "bin"
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    details = invoice_data.get("明细列表") or []
    first_item_name = ""
    if details and isinstance(details[0], dict):
        first_item_name = details[0].get("项目名称", "") or ""
    if not first_item_name:
        first_item_name = invoice_data.get("项目名称", "") or ""

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 先按发票号码查重。唯一索引覆盖全部记录（含已从票夹移除的），
        # 所以这里要区分两种情况：还在票夹里 → 拒绝；已被移除 → 复用旧记录重新入夹。
        existing_id = None
        if invoice_number:
            cursor.execute(
                "SELECT id, folder_status FROM invoice WHERE invoice_number = ? ORDER BY id LIMIT 1",
                (invoice_number,),
            )
            row = cursor.fetchone()
            if row is not None:
                if row["folder_status"] == "discarded":
                    existing_id = row["id"]  # 之前被移除过：复用这条记录，重新放回票夹
                else:
                    # 已在票夹中：查出是谁存的，给友好提示
                    cursor.execute(
                        "SELECT e.name, e.department FROM invoice i LEFT JOIN employee e ON e.id = i.employee_id WHERE i.id = ?",
                        (row["id"],),
                    )
                    owner = cursor.fetchone()
                    if owner and owner["name"]:
                        owner_info = f"{owner['name']}（{owner['department'] or '未知部门'}）"
                    else:
                        owner_info = "其他员工"
                    return {"ok": False, "error": f"该发票已被{owner_info}存入票夹，同一张发票不能重复报销"}

        # 需要写入的字段集中放在一个字典里，新增 / 复用记录共用同一份赋值
        values = {
            "employee_id": employee_id,
            "original_filename": original_filename,
            "file_ext": file_ext,
            "file_size": len(file_bytes),
            "file_sha256": file_sha256,
            "invoice_type": invoice_data.get("发票类型", ""),
            "invoice_species": invoice_data.get("发票种类", ""),
            "invoice_number": invoice_number,
            "invoice_date": invoice_data.get("开票日期", ""),
            "buyer_name": invoice_data.get("购买方名称", ""),
            "buyer_tax_id": invoice_data.get("购买方税号", ""),
            "seller_name": invoice_data.get("销售方名称", ""),
            "seller_tax_id": invoice_data.get("销售方税号", ""),
            "amount_without_tax": _to_float(invoice_data.get("不含税金额")),
            "tax_amount": _to_float(invoice_data.get("税额")),
            "tax_rate": invoice_data.get("税率", ""),
            "total_amount": _to_float(invoice_data.get("价税合计小写")),
            "total_amount_cn": invoice_data.get("价税合计大写", ""),
            "drawer": invoice_data.get("开票人", ""),
            "remark": invoice_data.get("备注", ""),
            "item_name": first_item_name,
            "parsed_json": json.dumps(invoice_data, ensure_ascii=False) if invoice_data else None,
            "parse_status": parse_status,
            "parse_error": parse_error,
            "audit_result": json.dumps(audit_result, ensure_ascii=False) if audit_result else None,
            "folder_status": "in_folder",
        }

        if existing_id is None:
            # 新记录：file_path 先占位，落盘后再回填真实路径
            columns = ["file_path", *values.keys(), "uploaded_at", "updated_at"]
            placeholders = ", ".join("?" for _ in columns)
            cursor.execute(
                f"INSERT INTO invoice ({', '.join(columns)}) VALUES ({placeholders})",
                ["", *values.values(), now, now],
            )
            invoice_id = cursor.lastrowid
        else:
            # 复用被移除过的记录：覆盖字段、重新入夹、刷新上传时间，并清掉旧明细
            invoice_id = existing_id
            assignments = ", ".join(f"{col} = ?" for col in values)
            cursor.execute(
                f"UPDATE invoice SET {assignments}, uploaded_at = ?, updated_at = ? WHERE id = ?",
                [*values.values(), now, now, invoice_id],
            )
            cursor.execute("DELETE FROM invoice_item WHERE invoice_id = ?", (invoice_id,))

        # 按员工/年月落盘，文件名带发票 id，避免重名覆盖
        rel_dir = Path("uploads") / str(employee_id) / now[:4] / now[5:7]
        abs_dir = PROJECT_ROOT / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(original_filename).name
        rel_path = rel_dir / f"{invoice_id}_{safe_name}"
        abs_path = PROJECT_ROOT / rel_path
        abs_path.write_bytes(file_bytes)

        cursor.execute(
            "UPDATE invoice SET file_path = ? WHERE id = ?",
            (str(rel_path).replace("\\", "/"), invoice_id),
        )

        for idx, item in enumerate(details, start=1):
            if not isinstance(item, dict):
                continue
            cursor.execute(
                """
                INSERT INTO invoice_item (
                    invoice_id, line_no, item_name, spec, unit,
                    quantity, unit_price, amount, tax_rate, tax_amount
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    idx,
                    item.get("项目名称", ""),
                    item.get("规格型号", ""),
                    item.get("单位", ""),
                    item.get("数量", ""),
                    item.get("单价", ""),
                    _to_float(item.get("金额")),
                    item.get("税率", ""),
                    _to_float(item.get("税额")),
                ),
            )

        conn.commit()
        return {"ok": True, "id": invoice_id}
    except sqlite3.IntegrityError:
        conn.rollback()
        return {"ok": False, "error": f"发票号码 {invoice_number} 已存在"}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def list_folder_invoices(employee_id=None, folder_status="in_folder"):
    """列出票夹中的发票（默认当前默认员工）"""
    if employee_id is None:
        employee_id = get_default_employee_id()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, original_filename, file_ext, invoice_type, invoice_number,
               invoice_date, buyer_name, seller_name, total_amount, item_name,
               parse_status, parse_error, folder_status, uploaded_at, file_path
        FROM invoice
        WHERE employee_id = ? AND folder_status = ?
        ORDER BY uploaded_at DESC, id DESC
        """,
        (employee_id, folder_status),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_invoice_detail(invoice_id):
    """获取单张发票详情（含明细与完整解析 JSON）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return None

    invoice = dict(row)
    cursor.execute(
        """
        SELECT line_no, item_name, spec, unit, quantity, unit_price,
               amount, tax_rate, tax_amount
        FROM invoice_item
        WHERE invoice_id = ?
        ORDER BY line_no
        """,
        (invoice_id,),
    )
    items = [dict(r) for r in cursor.fetchall()]
    conn.close()

    parsed = {}
    if invoice.get("parsed_json"):
        try:
            parsed = json.loads(invoice["parsed_json"])
        except json.JSONDecodeError:
            parsed = {}

    audit = None
    if invoice.get("audit_result"):
        try:
            audit = json.loads(invoice["audit_result"])
        except json.JSONDecodeError:
            audit = None

    invoice["items"] = items
    invoice["parsed"] = parsed
    invoice["audit"] = audit
    return invoice


def discard_folder_invoice(invoice_id):
    """从票夹移除（软删除，不物理删文件）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE invoice
        SET folder_status = 'discarded', updated_at = ?
        WHERE id = ? AND folder_status = 'in_folder'
        """,
        (_now(), invoice_id),
    )
    changed = cursor.rowcount
    conn.commit()
    conn.close()
    return changed > 0


def resolve_invoice_file_path(file_path):
    """把库里的相对路径解析成绝对路径"""
    if not file_path:
        return None
    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path if path.exists() else None




# ============================================================================
# 报销单（统一主表 + 分类扩展；1 张发票 : 1 张报销单）
# ============================================================================
def _make_form_no(cursor):
    """生成报销单单号：BX + 年月日 + 3 位当日流水号（例如 BX20260911001）

    注意：这里不能用「当天单据数 + 1」来生成 —— 中间的单据被删掉后，
    再新建就会算出已经用过的号，撞上 form_no 的唯一约束，
    然后被当成“发票已填写过报销单”误报出去。
    所以改成取「当天已用过的最大流水号 + 1」。
    """
    prefix = "BX" + datetime.now().strftime("%Y%m%d")  # 当天单号前缀
    cursor.execute(  # 看当天已经用过的最大单号（字符串倒序排 == 流水号倒序排）
        "SELECT form_no FROM expense_form WHERE form_no LIKE ? ORDER BY form_no DESC LIMIT 1",
        (prefix + "%",),
    )
    row = cursor.fetchone()  # 当天最后一张单（没有就是 None）
    seq = 1  # 流水号默认从 001 开始
    if row is not None:  # 当天已经有单据
        tail = (row["form_no"] or "")[len(prefix):]  # 去掉前缀，只留 3 位流水号
        if tail.isdigit():  # 是纯数字才能算出下一个号
            seq = int(tail) + 1  # 在最大号基础上加 1
    return prefix + str(seq).zfill(3)  # 补足 3 位后返回


def _decode_json_field(value, default):
    """把数据库里的 JSON 文本还原成 Python 对象，解析失败就返回默认值"""
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _form_row_to_dict(row):
    """把一行 expense_form（含 JOIN 出来的发票字段）转成字典，并展开 JSON 列"""
    form = dict(row)
    form["ext_fields"] = _decode_json_field(form.get("ext_fields"), {})
    form["detail_items"] = _decode_json_field(form.get("detail_json"), [])
    form["audit"] = _decode_json_field(form.get("audit_result"), {})
    return form


def create_expense_form(
    employee_id=None,
    invoice_id=None,
    trip_no="",
    expense_type="",
    occur_date="",
    reason="",
    total_amount=None,
    participants="",
    ext_fields=None,
    detail_items=None,
    status="待财务终审",
    audit_result=None,
):
    """
    新建一张报销单。

    返回：{"ok": True, "id": ..., "form_no": ...} 或 {"ok": False, "error": "..."}
    说明：invoice_id 上有唯一约束，所以同一张发票不可能生成两张报销单。
    """
    if employee_id is None:
        employee_id = get_default_employee_id()
    if not invoice_id:
        return {"ok": False, "error": "请先选择一张发票"}

    conn = get_connection()
    cursor = conn.cursor()
    try:  # 统一异常处理：任何失败都回滚并返回结构化结果
        cursor.execute("SELECT id FROM expense_form WHERE invoice_id = ?", (invoice_id,))  # 先查这张发票是否已经有报销单
        if cursor.fetchone() is not None:  # 已经有了
            return {"ok": False, "error": "这张发票已经填写过报销单了"}  # 同一张发票只允许一张单

        now = _now()  # 当前时间（提交时间 / 创建时间 / 更新时间共用）
        form_no = ""  # 先占个位，循环里再赋值
        for _ in range(5):  # 万一撞号（并发等情况）就重试，最多 5 次
            form_no = _make_form_no(cursor)  # 取当天最大流水号 + 1 作为新单号
            try:  # 先试着插入这一行
                cursor.execute(
                    """
                    INSERT INTO expense_form (
                        form_no, employee_id, invoice_id, trip_no, expense_type, occur_date,
                        reason, total_amount, participants, ext_fields, detail_json,
                        status, audit_result, submit_time, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        form_no,  # 报销单号
                        employee_id,  # 员工 id
                        invoice_id,  # 关联的发票 id
                        trip_no,  # 行程号
                        expense_type,  # 费用类型
                        occur_date,  # 费用发生日期
                        reason,  # 报销事由
                        _to_float(total_amount),  # 报销金额
                        participants,  # 参与人
                        json.dumps(ext_fields or {}, ensure_ascii=False),  # 分类扩展字段
                        json.dumps(detail_items or [], ensure_ascii=False),  # 明细模块
                        status,  # 单据状态
                        json.dumps(audit_result, ensure_ascii=False) if audit_result else None,  # 审核结果
                        now,  # 提交时间
                        now,  # 创建时间
                        now,  # 更新时间
                    ),
                )
                break  # 插入成功，跳出重试循环
            except sqlite3.IntegrityError:  # 撞了唯一约束：要么单号重复，要么这张发票刚被插进去
                conn.rollback()  # 先把这次失败的写入回滚，事务才好继续用
                cursor.execute("SELECT id FROM expense_form WHERE invoice_id = ?", (invoice_id,))  # 再查一次这张发票有没有单
                if cursor.fetchone() is not None:  # 确实已经有单了
                    return {"ok": False, "error": "这张发票已经填写过报销单了"}  # 这才叫“已经填写过”
                # 否则只是单号撞了，换一个号重试
        else:  # 5 次都没插进去
            return {"ok": False, "error": "报销单号生成失败，请稍后重试"}  # 明确说是单号问题，不再误报成“已填写过”

        form_id = cursor.lastrowid  # 新单据的自增 id
        conn.commit()  # 提交事务
        return {"ok": True, "id": form_id, "form_no": form_no}  # 把结果交给页面
    except Exception as exc:  # 其他异常
        conn.rollback()  # 回滚
        return {"ok": False, "error": str(exc)}  # 把原始错误交给页面显示
    finally:
        conn.close()


FORM_SELECT_SQL = """
    SELECT f.*, i.invoice_number, i.invoice_date, i.seller_name, i.invoice_type,
           i.total_amount AS invoice_amount, i.folder_status, i.original_filename
    FROM expense_form f
    LEFT JOIN invoice i ON i.id = f.invoice_id
"""


def get_expense_form(form_id):
    """按 id 查询单张报销单（附带关联发票的号码/销售方等字段）"""
    if not form_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(FORM_SELECT_SQL + " WHERE f.id = ?", (form_id,))
    row = cursor.fetchone()
    conn.close()
    return _form_row_to_dict(row) if row is not None else None


def get_expense_form_by_invoice(invoice_id):
    """按发票 id 查询报销单（用来判断这张发票是否已经填过单）"""
    if not invoice_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(FORM_SELECT_SQL + " WHERE f.invoice_id = ?", (invoice_id,))
    row = cursor.fetchone()
    conn.close()
    return _form_row_to_dict(row) if row is not None else None


def list_expense_forms(employee_id=None, trip_no=None):
    """列出报销单（默认当前默认员工），可按行程号过滤；按行程号倒序、同行程按 id 倒序"""
    if employee_id is None:
        employee_id = get_default_employee_id()
    sql = FORM_SELECT_SQL + " WHERE f.employee_id = ?"
    params = [employee_id]
    if trip_no is not None:
        sql += " AND IFNULL(f.trip_no, '') = ?"
        params.append(trip_no)
    sql += " ORDER BY IFNULL(f.trip_no, '') DESC, f.id DESC"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = [_form_row_to_dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def list_trip_numbers(employee_id=None):
    """列出已经用过的行程号（填写页的下拉框用它复用同一趟出差）"""
    if employee_id is None:
        employee_id = get_default_employee_id()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT trip_no FROM expense_form
        WHERE employee_id = ? AND trip_no IS NOT NULL AND trip_no != ''
        ORDER BY trip_no DESC
        """,
        (employee_id,),
    )
    trips = [row["trip_no"] for row in cursor.fetchall()]
    conn.close()
    return trips


def update_expense_form_audit(form_id, audit_result=None, status=None):
    """回写 Agent 终审结果 / 单据状态"""
    if not form_id:
        return False
    assignments = ["updated_at = ?"]
    params = [_now()]
    if audit_result is not None:
        assignments.append("audit_result = ?")
        params.append(json.dumps(audit_result, ensure_ascii=False))
    if status is not None:
        assignments.append("status = ?")
        params.append(status)
    params.append(form_id)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE expense_form SET " + ", ".join(assignments) + " WHERE id = ?", params)
    changed = cursor.rowcount
    conn.commit()
    conn.close()
    return changed > 0


def update_expense_form_fields(
    form_id,
    trip_no=None,
    expense_type=None,
    occur_date=None,
    reason=None,
    total_amount=None,
    participants=None,
    ext_fields=None,
    detail_items=None,
):
    """
    更新报销单的可编辑字段（编辑模式用）。
    只更新传入的字段，未传入的保持不变。
    更新后自动把状态重置为"待财务终审"，清空旧审核结果，提交时间更新为当前时间。

    返回：{"ok": True} 或 {"ok": False, "error": "..."}
    """
    if not form_id:
        return {"ok": False, "error": "报销单ID不能为空"}

    assignments = []  # 收集要更新的字段
    params = []       # 对应的参数值

    # 逐个判断：只有传入了非None值才更新
    if trip_no is not None:
        assignments.append("trip_no = ?")
        params.append(trip_no)
    if expense_type is not None:
        assignments.append("expense_type = ?")
        params.append(expense_type)
    if occur_date is not None:
        assignments.append("occur_date = ?")
        params.append(occur_date)
    if reason is not None:
        assignments.append("reason = ?")
        params.append(reason)
    if total_amount is not None:
        assignments.append("total_amount = ?")
        params.append(_to_float(total_amount))
    if participants is not None:
        assignments.append("participants = ?")
        params.append(participants)
    if ext_fields is not None:
        assignments.append("ext_fields = ?")
        params.append(json.dumps(ext_fields, ensure_ascii=False))
    if detail_items is not None:
        assignments.append("detail_json = ?")
        params.append(json.dumps(detail_items, ensure_ascii=False))

    # 编辑后必须重置状态和审核结果，重新走审核流程
    assignments.append("status = ?")
    params.append("待财务终审")
    assignments.append("audit_result = ?")
    params.append(None)
    assignments.append("submit_time = ?")
    params.append(_now())
    assignments.append("updated_at = ?")
    params.append(_now())

    params.append(form_id)  # WHERE条件的参数放最后

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE expense_form SET " + ", ".join(assignments) + " WHERE id = ?",
            params,
        )
        changed = cursor.rowcount
        conn.commit()
        if changed > 0:
            return {"ok": True}
        return {"ok": False, "error": "报销单不存在或无变化"}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


def delete_expense_form(form_id):
    """删除报销单（已驳回时允许删掉重填，否则这张发票会被永久占用）"""
    if not form_id:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expense_form WHERE id = ?", (form_id,))
    changed = cursor.rowcount
    conn.commit()
    conn.close()
    return changed > 0

# 模块加载时自动初始化数据库
init_db()
