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
# 工具1：费用标准校验
# ============================================================================
@tool
def expense_standard_tool(expense_type: str, amount: float,
                          city: str = "", level: str = "普通员工",
                          days: int = 1) -> dict:
    """
    检查费用是否符合公司报销标准。

    当需要判断某项费用是否超标时调用此工具。
    例如：住宿费580元是否超标、市内交通费300元是否超标等。

    参数:
        expense_type: 费用类型，如"住宿费"、"市内交通费"、"伙食补助"
        amount: 费用金额（元）
        city: 城市名称（住宿费需要），如"北京"、"上海"
        level: 员工职级，如"普通员工"、"部门经理"、"总经理及以上"，默认"普通员工"
        days: 天数（市内交通费、伙食补助需要），默认1天

    返回:
        字典，包含是否通过、标准金额、超标金额、说明信息
    """
    # 调用audit包里的纯函数，传入所有参数
    # **locals()会把当前函数的所有参数打包成字典传进去
    return check_expense_standard(
        expense_type=expense_type,
        city=city,
        level=level,
        amount=amount,
        days=days
    )


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
