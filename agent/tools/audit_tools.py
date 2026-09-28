# -*- coding: utf-8 -*-
# ============================================================================
# Agent工具封装（agent/tools/audit_tools.py）
# ============================================================================
# 作用：把audit包里的纯函数，包装成LangChain Tool格式，供Agent调用
# 原则：审核逻辑在audit包里，这里只做一层薄包装
# ============================================================================

import sys
from pathlib import Path

# 把项目根目录加入Python搜索路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# 导入LangChain的tool装饰器，用来把普通函数包装成Agent可调用的工具
from langchain_core.tools import tool

# 导入audit包里的纯函数（实际的审核逻辑在这里）
from audit.expense_standard import check_expense_standard
from audit.rag_check import rag_check


# ============================================================================
# 审核上下文：把「当前报销人」的职级注入工具层
# ----------------------------------------------------------------------------
# 为什么需要它：
#   费用标准是「职级 × 城市等级」查表算出来的，同一个金额在不同职级下结论相反
#   （例：上海住宿480元，部门经理标准500元=合规；普通员工标准400元=超标80元）。
#   如果只把职级写进提示词、指望模型传参，模型一旦漏传就会退化成默认值静默降级，
#   给出一个「看起来合理、其实口径错了」的结论，用户和 Agent 都不会察觉。
#   所以在工具层留一个上下文槽：流程开始前由 pipeline 注入，
#   模型缺参时自动兜底；上下文也空时保持空值下传，由纯函数升级为「待人工确认」。
# ============================================================================
_AUDIT_CONTEXT = {"employee_name": "", "employee_level": "", "department": "",
                            "invoice_amount": 0.0, "expense_city": "", "expense_days": 1}


def set_audit_context(employee=None):
    """
    注入当前报销人信息（姓名 / 部门 / 职级），供工具缺参时兜底。

    参数：
        employee: 员工字典（含 name / department / level）；传 None 表示清空上下文
    返回：
        注入后的上下文字典（浅拷贝，方便调用方打印确认）
    """
    employee = employee or {}  # 允许传 None，统一成空字典处理
    _AUDIT_CONTEXT["employee_name"] = employee.get("name") or ""  # 报销人姓名
    _AUDIT_CONTEXT["employee_level"] = employee.get("level") or ""  # 报销人职级（费用标准档位）
    _AUDIT_CONTEXT["department"] = employee.get("department") or ""  # 报销人部门
    # 新增：发票/报销单的关键参数，从系统注入，Agent不应该自己编
    _AUDIT_CONTEXT["invoice_amount"] = float(employee.get("invoice_amount") or 0)  # 发票价税合计
    _AUDIT_CONTEXT["expense_city"] = employee.get("expense_city") or ""  # 出差城市（从报销单取）
    _AUDIT_CONTEXT["expense_days"] = int(employee.get("expense_days") or 1)  # 住宿晚数/出差天数
    return dict(_AUDIT_CONTEXT)  # 返回副本，避免外部直接改到全局状态


def get_audit_context():
    """读取当前审核上下文（返回浅拷贝，避免调用方误改全局状态）。"""
    return dict(_AUDIT_CONTEXT)


# ============================================================================
# 工具1：费用标准校验
# ============================================================================
@tool
def expense_standard_tool(expense_type: str, amount: float,
                          city: str = "", level: str = "",
                          days: int = 1) -> dict:
    """
    检查费用是否符合公司报销标准。

    当需要判断某项费用是否超标时调用此工具。
    例如：住宿费580元是否超标、市内交通费300元是否超标等。

    参数:
        expense_type: 费用类型，如"住宿费"、"市内交通费"、"伙食补助"
        amount: 费用金额（元）
        city: 城市名称（住宿费需要），如"北京"、"上海"
        level: 员工职级，如"普通员工"、"部门经理"、"总经理及以上"。
               留空时系统会自动使用当前报销人的职级，不要自己猜一个填进来。
        days: 天数（市内交通费、伙食补助需要），默认1天

    返回:
        字典，包含是否通过、标准金额、超标金额、口径说明、是否需要人工确认
    """
    ctx = get_audit_context()

    # ---- 职级兜底：模型没传 level 时，用审核上下文里注入的当前员工职级 ----
    if not level:
        level = ctx.get("employee_level", "")

    # ---- 金额兜底：模型传0或没传时，用发票实际金额（防止Agent填错金额）----
    overrides = []  # 记录Agent覆盖了哪些系统值（供校验层追踪）
    if not amount or amount <= 0:
        amount = ctx.get("invoice_amount", 0)

    # ---- 城市兜底：模型没传时，用报销单里的城市（防止Agent填错城市）----
    if not city:
        city = ctx.get("expense_city", "")
    elif city != ctx.get("expense_city", "") and ctx.get("expense_city"):
        overrides.append(f"城市: 系统={ctx.get('expense_city')} Agent={city}")

    # ---- 天数兜底：模型用默认1天时，用报销单里的实际天数 ----
    if days == 1 and ctx.get("expense_days", 1) != 1:
        days = ctx.get("expense_days", 1)
    elif days != ctx.get("expense_days", 1) and ctx.get("expense_days", 1) != 1:
        overrides.append(f"天数: 系统={ctx.get('expense_days')} Agent={days}")

    # 调用audit包里的纯函数，传入所有参数
    result = check_expense_standard(
        expense_type=expense_type,
        city=city,
        level=level,
        amount=amount,
        days=days
    )
    # 如果Agent覆盖了系统值，在返回结果里标注（校验层可以看到）
    if overrides:
        result["_warning"] = "Agent覆盖了系统默认参数：" + "; ".join(overrides)
    return result


# ============================================================================
# 工具2：RAG制度检索
# ============================================================================
@tool
def rag_policy_search_tool(item_name: str, amount: str = "",
                           seller: str = "") -> dict:
    """
    检索报销制度相关条款，为审核提供制度依据。

    当需要查找某项费用的报销规定、审批要求、特殊情况处理时调用此工具。
    例如：住宿费超标怎么办、业务招待费有什么规定等。

    参数:
        item_name: 发票项目名称，如"*生产生活服务*代订房费"
        amount: 发票金额，如"2584.00"，可选
        seller: 销售方名称，可选

    返回:
        字典，包含检索用的查询词、检索到的制度条款文本、条款数量
    """
    # 构造发票数据字典，传给rag_check
    invoice_data = {
        "项目名称": item_name,
        "价税合计小写": amount,
        "销售方名称": seller
    }
    # 调用audit包里的RAG检索函数
    return rag_check(invoice_data)


# ============================================================================
# 工具列表：Agent启动时传入
# ============================================================================
# 把所有工具收集到一个列表里，create_react_agent需要这个列表
AGENT_TOOLS = [
    expense_standard_tool,
    rag_policy_search_tool,
]
