# -*- coding: utf-8 -*-
# ============================================================================
# 风控异常检测工具（audit/anomaly_detect.py）
# ============================================================================
# 作用：基于历史报销数据，检测6类异常行为，供Agent调用或流程中自动触发
# 数据来源：expense_form（报销单）JOIN invoice（发票）
# ============================================================================

import sys  # 用于把项目根目录加入Python搜索路径
from pathlib import Path  # 路径处理

# 把项目根目录加入搜索路径（直接运行本文件时需要）
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3  # 数据库操作
from datetime import datetime, timedelta  # 日期计算（前后N天、近N天）

from config.settings import (  # 从全局配置读取风控参数
    APPROVAL_THRESHOLDS,        # 审批阈值列表
    CONSECUTIVE_DAYS_RANGE,     # 连号检测的日期范围（前后N天）
    AMOUNT_THRESHOLD_RATIO,     # 金额临界比例（0.95=95%）
    HIGH_FREQUENCY_DAYS,        # 高频检测统计天数
    HIGH_FREQUENCY_MULTIPLIER,  # 高频倍数阈值
)
from database.db import get_connection  # 复用项目的数据库连接
from rag.utils.logger import info, success, warn, error  # 日志工具（INFO/SUCCESS/WARN/ERROR）


# ============================================================================
# 工具1：重复报销检测
# ============================================================================
def check_duplicate(invoice_number, exclude_form_id=None, status_filter=("已通过",)):
    """
    检测发票号码是否已经报销成功（重复报销）

    注意：默认只查状态为"已通过"的报销单，被驳回/待审核的不算重复报销。
    被驳回的单据用户可以修改后重新提交。

    参数：
        invoice_number: 发票号码（字符串）
        exclude_form_id: 排除的报销单ID（当前正在提交的报销单，避免把自己判为重复）
        status_filter: 要查询的状态列表/元组，默认只查("已通过",)。
                       传 None 或空列表表示查所有状态。
    返回：
        {
            "is_duplicate": bool,           # 是否重复
            "previous_records": [           # 之前的报销记录列表
                {
                    "form_no": str,         # 报销单号
                    "employee_name": str,   # 报销人
                    "submit_time": str,     # 提交时间
                    "total_amount": float,  # 金额
                    "status": str           # 单据状态
                }
            ],
            "message": str                  # 人类可读的描述
        }
    """
    # 参数校验：发票号码为空直接返回不重复
    if not invoice_number or not str(invoice_number).strip():
        return {"is_duplicate": False, "previous_records": [], "message": "发票号码为空，无法检测"}

    conn = get_connection()  # 获取数据库连接
    cursor = conn.cursor()

    # 查报销单表关联发票表，找同发票号码的报销单
    sql = """
        SELECT f.form_no, f.submit_time, f.total_amount, f.status,
               e.name AS employee_name
        FROM expense_form f
        LEFT JOIN invoice i ON i.id = f.invoice_id
        LEFT JOIN employee e ON e.id = f.employee_id
        WHERE i.invoice_number = ?
    """
    params = [str(invoice_number).strip()]  # 查询参数列表

    # 状态过滤：status_filter 为 None 或空时查所有状态，否则查指定状态
    if status_filter:
        placeholders = ", ".join("?" for _ in status_filter)
        sql += f" AND f.status IN ({placeholders})"
        params.extend(status_filter)

    # 如果指定了排除的报销单ID（当前正在提交的），加上排除条件
    if exclude_form_id is not None:
        sql += " AND f.id != ?"
        params.append(exclude_form_id)

    sql += " ORDER BY f.submit_time DESC"
    cursor.execute(sql, params)

    rows = cursor.fetchall()  # 取出所有匹配的记录
    conn.close()  # 关闭连接

    # 组装返回结果
    records = []
    for row in rows:
        records.append({
            "form_no": row["form_no"],
            "employee_name": row["employee_name"] or "未知",
            "submit_time": row["submit_time"] or "-",
            "total_amount": row["total_amount"] or 0,
            "status": row["status"] or "-"
        })

    if records:
        # 有已通过的历史记录 → 真正的重复报销
        return {
            "is_duplicate": True,
            "previous_records": records,
            "message": f"发票号码 {invoice_number} 已报销成功 {len(records)} 次，最近一次：{records[0]['submit_time']}（{records[0]['employee_name']}，单号{records[0]['form_no']}）"
        }
    else:
        # 无已通过的历史记录 → 不重复（被驳回的不算）
        return {
            "is_duplicate": False,
            "previous_records": [],
            "message": f"发票号码 {invoice_number} 无重复报销记录（已驳回/待审核的单据不计入）"
        }


# ============================================================================
# 工具2：连号发票检测
# ============================================================================
def check_consecutive_invoices(employee_id, seller_name, invoice_number, invoice_date):
    """
    检测同一员工、同一销售方、开票日期前后N天内是否有连号发票

    参数：
        employee_id: 员工ID
        seller_name: 销售方名称
        invoice_number: 当前发票号码（字符串，可能是纯数字或带前缀）
        invoice_date: 开票日期（YYYY-MM-DD）
    返回：
        {
            "is_consecutive": bool,      # 是否存在连号
            "consecutive_count": int,    # 连号张数（含当前发票）
            "consecutive_invoices": [    # 连号的发票列表
                {"invoice_number": str, "invoice_date": str, "total_amount": float}
            ],
            "message": str
        }
    """
    # 参数校验
    if not invoice_number or not invoice_date:
        return {"is_consecutive": False, "consecutive_count": 0, "consecutive_invoices": [], "message": "发票号码或日期为空"}

    # 解析日期：把字符串转成datetime对象，用于计算前后N天
    try:
        current_date = datetime.strptime(invoice_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return {"is_consecutive": False, "consecutive_count": 0, "consecutive_invoices": [], "message": "日期格式错误"}

    # 计算日期范围：当前日期前后各N天
    start_date = (current_date - timedelta(days=CONSECUTIVE_DAYS_RANGE)).strftime("%Y-%m-%d")
    end_date = (current_date + timedelta(days=CONSECUTIVE_DAYS_RANGE)).strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()

    # 查该员工、该销售方、日期范围内的所有发票（从报销单关联发票表）
    cursor.execute("""
        SELECT DISTINCT i.invoice_number, i.invoice_date, i.total_amount
        FROM expense_form f
        JOIN invoice i ON i.id = f.invoice_id
        WHERE f.employee_id = ?
          AND i.seller_name = ?
          AND i.invoice_date BETWEEN ? AND ?
        ORDER BY i.invoice_number
    """, (employee_id, seller_name, start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    # 提取发票号码列表，尝试提取数字部分用于连号判断
    # 数电票号码是20位纯数字，普通发票可能有前缀，这里只取末尾的数字串
    def _extract_number(num_str):
        """从发票号码中提取末尾的连续数字，用于连号判断"""
        num_str = str(num_str or "")
        digits = ""
        for ch in reversed(num_str):  # 从末尾往前找数字
            if ch.isdigit():
                digits = ch + digits
            else:
                break
        return int(digits) if digits else None

    # 给每张发票加上数字序号
    numbered = []
    for row in rows:
        num = _extract_number(row["invoice_number"])
        if num is not None:
            numbered.append((num, row["invoice_number"], row["invoice_date"], row["total_amount"]))

    # 当前发票的数字序号
    current_num = _extract_number(invoice_number)

    # 按数字排序，找包含当前发票的连续序列
    numbered.sort(key=lambda x: x[0])

    # 找到当前发票在序列中的位置
    current_idx = None
    for i, (num, _, _, _) in enumerate(numbered):
        if num == current_num:
            current_idx = i
            break

    # 如果当前发票不在历史记录里（新发票），把它加进去再判断连号
    if current_idx is None and current_num is not None:
        numbered.append((current_num, invoice_number, invoice_date, None))
        numbered.sort(key=lambda x: x[0])
        for i, (num, _, _, _) in enumerate(numbered):
            if num == current_num:
                current_idx = i
                break

    # 从当前位置向左右扩展，找连续的号码（差为1）
    consecutive = []
    if current_idx is not None:
        # 先加当前发票
        consecutive.append(numbered[current_idx])
        # 向左找连续
        left = current_idx - 1
        while left >= 0 and numbered[left + 1][0] - numbered[left][0] == 1:
            consecutive.insert(0, numbered[left])
            left -= 1
        # 向右找连续
        right = current_idx + 1
        while right < len(numbered) and numbered[right][0] - numbered[right - 1][0] == 1:
            consecutive.append(numbered[right])
            right += 1

    # 组装结果
    inv_list = [
        {"invoice_number": inv_num, "invoice_date": inv_date, "total_amount": amt or 0}
        for _, inv_num, inv_date, amt in consecutive
    ]

    if len(consecutive) >= 2:
        return {
            "is_consecutive": True,
            "consecutive_count": len(consecutive),
            "consecutive_invoices": inv_list,
            "message": f"检测到连号发票 {len(consecutive)} 张（{seller_name}），号码连续"
        }
    else:
        return {
            "is_consecutive": False,
            "consecutive_count": 1,
            "consecutive_invoices": inv_list,
            "message": "未检测到连号发票"
        }


# ============================================================================
# 工具3：拆分报销检测
# ============================================================================
def check_split_reimbursement(employee_id, invoice_date, total_amount, seller_name):
    """
    检测同一员工、同一天、同一销售方的多张发票是否疑似拆分报销
    逻辑：多张发票合计金额超过审批阈值，但每张都低于阈值 → 疑似拆分

    参数：
        employee_id: 员工ID
        invoice_date: 当前发票开票日期
        total_amount: 当前发票金额
        seller_name: 销售方名称
    返回：
        {
            "is_suspected_split": bool,   # 是否疑似拆分
            "related_invoices": [         # 同一天同销售方的所有发票
                {"invoice_number": str, "total_amount": float}
            ],
            "total_sum": float,           # 合计金额
            "threshold": float,           # 触发的审批阈值
            "message": str
        }
    """
    # 参数校验
    if not invoice_date or not seller_name:
        return {"is_suspected_split": False, "related_invoices": [], "total_sum": 0, "threshold": 0, "message": "日期或销售方为空"}

    conn = get_connection()
    cursor = conn.cursor()

    # 查该员工、同一天、同一销售方的所有已报销发票
    cursor.execute("""
        SELECT DISTINCT i.invoice_number, i.total_amount
        FROM expense_form f
        JOIN invoice i ON i.id = f.invoice_id
        WHERE f.employee_id = ?
          AND i.invoice_date = ?
          AND i.seller_name = ?
        ORDER BY i.total_amount DESC
    """, (employee_id, invoice_date, seller_name))

    rows = cursor.fetchall()
    conn.close()

    # 组装历史发票列表
    related = [
        {"invoice_number": r["invoice_number"] or "-", "total_amount": r["total_amount"] or 0}
        for r in rows
    ]

    # 计算合计金额（历史 + 当前发票）
    history_sum = sum(r["total_amount"] for r in related)
    total_sum = history_sum + float(total_amount or 0)

    # 找最低的那个被超过的阈值（从小到大找第一个 < 合计金额的阈值）
    triggered_threshold = None
    for threshold in sorted(APPROVAL_THRESHOLDS):
        if total_sum > threshold:
            triggered_threshold = threshold
            break  # 找到最低的被超过的阈值就停

    # 判断是否每张都低于该阈值（即没有单张超过阈值，说明是故意拆开）
    all_below_threshold = True
    if triggered_threshold:
        # 检查历史发票每张是否都低于阈值
        for r in related:
            if r["total_amount"] >= triggered_threshold:
                all_below_threshold = False
                break
        # 检查当前发票是否低于阈值
        if float(total_amount or 0) >= triggered_threshold:
            all_below_threshold = False

    # 疑似拆分的条件：合计超过阈值 + 每张都低于阈值 + 至少2张
    is_split = bool(
        triggered_threshold
        and all_below_threshold
        and (len(related) + 1) >= 2
    )

    if is_split:
        return {
            "is_suspected_split": True,
            "related_invoices": related,
            "total_sum": total_sum,
            "threshold": triggered_threshold,
            "message": f"疑似拆分报销：同日同销售方共{len(related) + 1}张发票，合计{total_sum:.2f}元超过{triggered_threshold}元审批线，但单张均低于阈值"
        }
    else:
        return {
            "is_suspected_split": False,
            "related_invoices": related,
            "total_sum": total_sum,
            "threshold": triggered_threshold or 0,
            "message": "未检测到拆分报销特征"
        }


# ============================================================================
# 工具4：金额临界检测
# ============================================================================
def check_amount_threshold(total_amount):
    """
    检测发票金额是否接近审批阈值（在阈值的95%-100%区间内）

    参数：
        total_amount: 发票金额
    返回：
        {
            "is_critical": bool,     # 是否临界
            "near_threshold": float, # 接近哪个阈值
            "gap": float,            # 与阈值的差距（阈值 - 金额）
            "ratio": float,          # 金额/阈值 的比例
            "message": str
        }
    """
    # 参数校验
    if total_amount is None:
        return {"is_critical": False, "near_threshold": 0, "gap": 0, "ratio": 0, "message": "金额为空"}

    amount = float(total_amount)
    if amount <= 0:
        return {"is_critical": False, "near_threshold": 0, "gap": 0, "ratio": 0, "message": "金额为0或负数"}

    # 遍历所有审批阈值，找最接近的那个
    near_threshold = None
    min_gap = float("inf")  # 初始化为无穷大

    for threshold in APPROVAL_THRESHOLDS:
        # 只考虑金额小于阈值的情况（超过阈值就不是"临界"了，是直接超标）
        if amount < threshold:
            gap = threshold - amount  # 与阈值的差距
            ratio = amount / threshold  # 金额占阈值的比例
            # 如果比例在 [AMOUNT_THRESHOLD_RATIO, 1.0) 之间，且差距更小，更新
            if ratio >= AMOUNT_THRESHOLD_RATIO and gap < min_gap:
                min_gap = gap
                near_threshold = threshold

    if near_threshold:
        ratio = amount / near_threshold
        return {
            "is_critical": True,
            "near_threshold": near_threshold,
            "gap": round(min_gap, 2),
            "ratio": round(ratio, 4),
            "message": f"金额{amount:.2f}元接近{near_threshold}元审批线，仅差{min_gap:.2f}元（占阈值的{ratio*100:.1f}%），疑似故意压价"
        }
    else:
        return {
            "is_critical": False,
            "near_threshold": 0,
            "gap": 0,
            "ratio": 0,
            "message": "金额未接近任何审批阈值"
        }


# ============================================================================
# 工具5：高频报销检测
# ============================================================================
def check_high_frequency(employee_id, days=HIGH_FREQUENCY_DAYS):
    """
    检测员工近N天报销次数和金额是否远超部门平均水平

    参数：
        employee_id: 员工ID
        days: 统计天数（默认30天）
    返回：
        {
            "is_abnormal": bool,        # 是否异常
            "employee_count": int,      # 该员工近N天报销次数
            "employee_total": float,    # 该员工近N天报销总金额
            "dept_avg_count": float,    # 部门平均报销次数
            "dept_avg_total": float,    # 部门平均报销金额
            "count_ratio": float,       # 次数倍数（员工/部门平均）
            "amount_ratio": float,      # 金额倍数
            "message": str
        }
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 计算日期范围：近N天
    end_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    # 1. 查该员工的部门
    cursor.execute("SELECT department FROM employee WHERE id = ?", (employee_id,))
    emp_row = cursor.fetchone()
    department = emp_row["department"] if emp_row else None

    # 2. 统计该员工近N天的报销次数和总金额
    cursor.execute("""
        SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS total
        FROM expense_form
        WHERE employee_id = ? AND submit_time BETWEEN ? AND ?
    """, (employee_id, start_date, end_date))
    emp_stats = cursor.fetchone()
    emp_count = emp_stats["cnt"] or 0
    emp_total = emp_stats["total"] or 0

    # 3. 统计同部门所有员工近N天的报销次数和总金额（用于算平均值）
    if department:
        cursor.execute("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(f.total_amount), 0) AS total,
                   COUNT(DISTINCT f.employee_id) AS emp_num
            FROM expense_form f
            JOIN employee e ON e.id = f.employee_id
            WHERE e.department = ? AND f.submit_time BETWEEN ? AND ?
        """, (department, start_date, end_date))
        dept_stats = cursor.fetchone()
        dept_emp_num = dept_stats["emp_num"] or 1  # 避免除以0
        dept_avg_count = (dept_stats["cnt"] or 0) / dept_emp_num
        dept_avg_total = (dept_stats["total"] or 0) / dept_emp_num
    else:
        # 没有部门信息时，用全公司平均
        cursor.execute("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS total,
                   COUNT(DISTINCT employee_id) AS emp_num
            FROM expense_form
            WHERE submit_time BETWEEN ? AND ?
        """, (start_date, end_date))
        all_stats = cursor.fetchone()
        dept_emp_num = all_stats["emp_num"] or 1
        dept_avg_count = (all_stats["cnt"] or 0) / dept_emp_num
        dept_avg_total = (all_stats["total"] or 0) / dept_emp_num

    conn.close()

    # 计算倍数
    count_ratio = emp_count / dept_avg_count if dept_avg_count > 0 else 0
    amount_ratio = emp_total / dept_avg_total if dept_avg_total > 0 else 0

    # 判断是否异常：次数或金额超过部门平均的N倍
    is_abnormal = count_ratio >= HIGH_FREQUENCY_MULTIPLIER or amount_ratio >= HIGH_FREQUENCY_MULTIPLIER

    if is_abnormal:
        reasons = []
        if count_ratio >= HIGH_FREQUENCY_MULTIPLIER:
            reasons.append(f"报销次数{emp_count}次，是部门平均{dept_avg_count:.1f}次的{count_ratio:.1f}倍")
        if amount_ratio >= HIGH_FREQUENCY_MULTIPLIER:
            reasons.append(f"报销金额{emp_total:.2f}元，是部门平均{dept_avg_total:.2f}元的{amount_ratio:.1f}倍")
        return {
            "is_abnormal": True,
            "employee_count": emp_count,
            "employee_total": round(emp_total, 2),
            "dept_avg_count": round(dept_avg_count, 2),
            "dept_avg_total": round(dept_avg_total, 2),
            "count_ratio": round(count_ratio, 2),
            "amount_ratio": round(amount_ratio, 2),
            "message": f"高频报销预警：近{days}天" + "，".join(reasons)
        }
    else:
        return {
            "is_abnormal": False,
            "employee_count": emp_count,
            "employee_total": round(emp_total, 2),
            "dept_avg_count": round(dept_avg_count, 2),
            "dept_avg_total": round(dept_avg_total, 2),
            "count_ratio": round(count_ratio, 2),
            "amount_ratio": round(amount_ratio, 2),
            "message": f"近{days}天报销{emp_count}次、{emp_total:.2f}元，处于部门正常水平"
        }


# ============================================================================
# 工具6：历史行为画像查询
# ============================================================================
def get_employee_behavior_profile(employee_id, days=90):
    """
    查询员工的历史报销行为画像，供Agent参考判断

    参数：
        employee_id: 员工ID
        days: 统计天数（默认90天）
    返回：
        {
            "total_count": int,           # 近N天报销总次数
            "total_amount": float,        # 近N天报销总金额
            "expense_type_dist": {},      # 费用类型分布 {类型: 次数}
            "top_sellers": [],            # 高频销售方TOP5
            "pass_rate": float,           # 通过率（已通过/总数）
            "avg_amount": float,          # 平均单笔金额
            "last_reimburse_date": str,   # 最近一次报销日期
            "message": str
        }
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 计算日期范围
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    # 1. 总体统计：次数、总金额、平均金额、通过数
    cursor.execute("""
        SELECT COUNT(*) AS cnt,
               COALESCE(SUM(total_amount), 0) AS total,
               COALESCE(AVG(total_amount), 0) AS avg_amt,
               SUM(CASE WHEN status = '已通过' THEN 1 ELSE 0 END) AS passed
        FROM expense_form
        WHERE employee_id = ? AND submit_time >= ?
    """, (employee_id, start_date))
    stats = cursor.fetchone()
    total_count = stats["cnt"] or 0
    total_amount = stats["total"] or 0
    avg_amount = stats["avg_amt"] or 0
    passed_count = stats["passed"] or 0
    pass_rate = passed_count / total_count if total_count > 0 else 0

    # 2. 费用类型分布
    cursor.execute("""
        SELECT expense_type, COUNT(*) AS cnt
        FROM expense_form
        WHERE employee_id = ? AND submit_time >= ? AND expense_type IS NOT NULL AND expense_type != ''
        GROUP BY expense_type
        ORDER BY cnt DESC
    """, (employee_id, start_date))
    type_dist = {row["expense_type"]: row["cnt"] for row in cursor.fetchall()}

    # 3. 高频销售方TOP5（关联发票表）
    cursor.execute("""
        SELECT i.seller_name, COUNT(*) AS cnt, COALESCE(SUM(f.total_amount), 0) AS total
        FROM expense_form f
        JOIN invoice i ON i.id = f.invoice_id
        WHERE f.employee_id = ? AND f.submit_time >= ? AND i.seller_name IS NOT NULL AND i.seller_name != ''
        GROUP BY i.seller_name
        ORDER BY cnt DESC
        LIMIT 5
    """, (employee_id, start_date))
    top_sellers = [
        {"seller_name": row["seller_name"], "count": row["cnt"], "total_amount": row["total"] or 0}
        for row in cursor.fetchall()
    ]

    # 4. 最近一次报销日期
    cursor.execute("""
        SELECT MAX(submit_time) AS last_time FROM expense_form WHERE employee_id = ?
    """, (employee_id,))
    last_row = cursor.fetchone()
    last_date = last_row["last_time"] or "无记录"

    conn.close()

    return {
        "total_count": total_count,
        "total_amount": round(total_amount, 2),
        "expense_type_dist": type_dist,
        "top_sellers": top_sellers,
        "pass_rate": round(pass_rate, 4),
        "avg_amount": round(avg_amount, 2),
        "last_reimburse_date": last_date,
        "message": f"近{days}天共报销{total_count}次，总金额{total_amount:.2f}元，通过率{pass_rate*100:.1f}%"
    }


# ============================================================================
# 汇总检测：一次性跑全部6项，返回综合风控报告
# ============================================================================
def run_all_anomaly_checks(employee_id, invoice_data, exclude_form_id=None):
    """
    对一张发票一次性执行全部风控检测，返回综合报告

    参数：
        employee_id: 员工ID
        invoice_data: 发票识别结果字典（中文键）
        exclude_form_id: 排除的报销单ID（当前正在提交的报销单，避免重复报销误判）
    返回：
        {
            "has_risk": bool,           # 是否有风险
            "risk_count": int,          # 风险项数量
            "risk_items": [             # 有风险的检测项
                {"type": str, "level": str, "message": str, "detail": dict}
            ],
            "all_results": {            # 全部检测结果（含无风险的）
                "duplicate": ...,
                "consecutive": ...,
                "split": ...,
                "amount_threshold": ...,
                "high_frequency": ...,
                "behavior_profile": ...
            }
        }
    """
    # 从发票数据中提取检测需要的字段
    invoice_number = invoice_data.get("发票号码", "")
    seller_name = invoice_data.get("销售方名称", "")
    invoice_date = invoice_data.get("开票日期", "")
    total_amount = float(invoice_data.get("价税合计小写", 0) or 0)

    # ========================================================================
    # 逐项执行检测，每项前后输出日志（与基础校验同风格）
    # ========================================================================

    # ---- 检测1/6：重复报销 ----
    info(f"【风控检测1/6】开始：check_duplicate（检测发票号码是否已经报销成功）")
    info(f"    输入参数: 发票号码={invoice_number}, 排除报销单ID={exclude_form_id}, 状态过滤=已通过")
    dup_result = check_duplicate(invoice_number, exclude_form_id=exclude_form_id)
    if dup_result["is_duplicate"]:
        error(f"【风控检测1/6】风险：重复报销 → {dup_result['message']}")
    else:
        success(f"【风控检测1/6】通过：重复报销 → {dup_result['message']}")

    # ---- 检测2/6：连号发票 ----
    info(f"【风控检测2/6】开始：check_consecutive_invoices（检测同一员工同一销售方前后{CONSECUTIVE_DAYS_RANGE}天是否连号）")
    info(f"    输入参数: 员工ID={employee_id}, 销售方={seller_name}, 发票号码={invoice_number}, 开票日期={invoice_date}")
    cons_result = check_consecutive_invoices(employee_id, seller_name, invoice_number, invoice_date)
    if cons_result["is_consecutive"]:
        warn(f"【风控检测2/6】风险：连号发票 → {cons_result['message']}")
    else:
        success(f"【风控检测2/6】通过：连号发票 → {cons_result['message']}")

    # ---- 检测3/6：拆分报销 ----
    info(f"【风控检测3/6】开始：check_split_reimbursement（检测同日同销售方多张发票是否疑似拆分规避审批线）")
    info(f"    输入参数: 员工ID={employee_id}, 开票日期={invoice_date}, 金额={total_amount}, 销售方={seller_name}")
    split_result = check_split_reimbursement(employee_id, invoice_date, total_amount, seller_name)
    if split_result["is_suspected_split"]:
        error(f"【风控检测3/6】风险：拆分报销 → {split_result['message']}")
    else:
        success(f"【风控检测3/6】通过：拆分报销 → {split_result['message']}")

    # ---- 检测4/6：金额临界 ----
    info(f"【风控检测4/6】开始：check_amount_threshold（检测金额是否接近审批阈值{APPROVAL_THRESHOLDS}）")
    info(f"    输入参数: 金额={total_amount}")
    amount_result = check_amount_threshold(total_amount)
    if amount_result["is_critical"]:
        warn(f"【风控检测4/6】风险：金额临界 → {amount_result['message']}")
    else:
        success(f"【风控检测4/6】通过：金额临界 → {amount_result['message']}")

    # ---- 检测5/6：高频报销 ----
    info(f"【风控检测5/6】开始：check_high_frequency（统计员工近{HIGH_FREQUENCY_DAYS}天报销是否超部门平均{HIGH_FREQUENCY_MULTIPLIER}倍）")
    info(f"    输入参数: 员工ID={employee_id}")
    freq_result = check_high_frequency(employee_id)
    if freq_result["is_abnormal"]:
        warn(f"【风控检测5/6】风险：高频报销 → {freq_result['message']}")
    else:
        success(f"【风控检测5/6】通过：高频报销 → {freq_result['message']}")

    # ---- 检测6/6：历史行为画像 ----
    info(f"【风控检测6/6】开始：get_employee_behavior_profile（查询员工近90天报销行为画像）")
    info(f"    输入参数: 员工ID={employee_id}")
    profile_result = get_employee_behavior_profile(employee_id)
    success(f"【风控检测6/6】完成：历史行为画像 → {profile_result['message']}（费用类型分布{len(profile_result['expense_type_dist'])}类，高频销售方{len(profile_result['top_sellers'])}家）")

    # 收集有风险的项
    risk_items = []
    if dup_result["is_duplicate"]:
        risk_items.append({"type": "重复报销", "level": "high", "message": dup_result["message"], "detail": dup_result})
    if cons_result["is_consecutive"]:
        risk_items.append({"type": "连号发票", "level": "medium", "message": cons_result["message"], "detail": cons_result})
    if split_result["is_suspected_split"]:
        risk_items.append({"type": "拆分报销", "level": "high", "message": split_result["message"], "detail": split_result})
    if amount_result["is_critical"]:
        risk_items.append({"type": "金额临界", "level": "low", "message": amount_result["message"], "detail": amount_result})
    if freq_result["is_abnormal"]:
        risk_items.append({"type": "高频报销", "level": "medium", "message": freq_result["message"], "detail": freq_result})

    return {
        "has_risk": len(risk_items) > 0,
        "risk_count": len(risk_items),
        "risk_items": risk_items,
        "all_results": {
            "duplicate": dup_result,
            "consecutive": cons_result,
            "split": split_result,
            "amount_threshold": amount_result,
            "high_frequency": freq_result,
            "behavior_profile": profile_result
        }
    }


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    from database.db import get_default_employee_id

    emp_id = get_default_employee_id()
    print(f"测试员工ID: {emp_id}")

    # 测试金额临界检测
    print("\n--- 金额临界检测（499元）---")
    print(check_amount_threshold(499))

    # 测试高频检测
    print("\n--- 高频报销检测 ---")
    print(check_high_frequency(emp_id))

    # 测试历史行为画像
    print("\n--- 历史行为画像 ---")
    print(get_employee_behavior_profile(emp_id))
