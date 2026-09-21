# -*- coding: utf-8 -*-
"""
报销事由语义审核：用LLM判断报销事由与发票内容、费用类型是否匹配
纯代码做不了语义判断，这是LLM能发挥作用的地方
"""
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from rag.utils.logger import info, success, warn, error


# 全局缓存的LLM实例（避免重复初始化）
_llm = None


def _get_llm():
    """获取或初始化LLM实例（单例）"""
    global _llm
    if _llm is None:
        info("[事由语义审核] 初始化LLM...")
        _llm = ChatOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            temperature=0.1,  # 低温度，保证判断稳定
            streaming=False,
        )
        success("[事由语义审核] LLM初始化成功")
    return _llm


def check_reason_semantic(invoice_data, reason, expense_type=None):
    """
    报销事由语义审核：判断报销事由与发票内容、费用类型是否匹配

    参数：
        invoice_data: 识别出的发票数据字典（中文键）
        reason: 用户填写的报销事由
        expense_type: 选择的费用类型（可选，用于辅助判断）

    返回：
        {
            "passed": bool,           # 是否通过（匹配=通过）
            "level": "pass"|"warning",# 通过/警告
            "message": str,           # 简短结论
            "details": str            # 详细分析
        }
    """
    # 参数校验：事由为空直接返回警告
    if not reason or not str(reason).strip():
        return {
            "passed": False,
            "level": "warning",
            "message": "报销事由为空",
            "details": "请填写报销事由，说明这笔费用的用途和背景。"
        }

    # 从发票数据提取关键信息
    seller = invoice_data.get("销售方名称", "") or ""
    item_name = invoice_data.get("项目名称", "") or ""
    invoice_type = invoice_data.get("发票类型", "") or ""
    total_amount = invoice_data.get("价税合计小写", "") or ""

    # 打印输入日志
    info(f"[事由语义审核] 输入: 项目名称='{item_name}', 销售方='{seller}', 费用类型='{expense_type}', 事由='{reason}'")
    info(f"[事由语义审核] invoice_data顶层keys: {list(invoice_data.keys())}")
    if not item_name:
        warn(f"[事由语义审核] 警告: 顶层项目名称为空！明细列表第一条项目名称='{(invoice_data.get('明细列表') or [{}])[0].get('项目名称', '无')}'")

    # 构造审核提示词
    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是企业财务审核员，负责判断报销事由与发票内容是否匹配。
        【审核规则】
        1. 报销事由必须与发票内容（销售方、项目名称）逻辑一致
        2. 事由应包含：时间、地点、用途三要素中的至少两项
        3. 明显不匹配的情况（如：住宿费发票写"办公用品采购"）→ 不通过
        4. 事由过于简单（如只写"报销"、"费用"）→ 警告，建议补充
        5. 事由与发票内容匹配且信息充分 → 通过
        【输出格式】
        第一行：结论（通过/警告/不通过）
        第二行：简短原因（不超过50字）
        第三行起：详细分析（说明判断依据）"""),
                ("human", """请审核以下报销单：
        【发票信息】
        - 发票类型：{invoice_type}
        - 销售方：{seller}
        - 项目名称：{item_name}
        - 金额：{total_amount}
        - 费用类型：{expense_type}
        【报销事由】
        {reason}
        请给出审核结论。""")
    ])

    # 构造LLM链
    chain = prompt | _get_llm() | StrOutputParser()

    try:
        info(f"[事由语义审核] 开始审核：事由='{reason}'")
        result_text = chain.invoke({
            "invoice_type": invoice_type,
            "seller": seller,
            "item_name": item_name,
            "total_amount": total_amount,
            "expense_type": expense_type or "未选择",
            "reason": reason,
        })
        success("[事由语义审核] 审核完成")

        # 解析LLM输出
        lines = result_text.strip().split("\n")
        first_line = lines[0] if lines else ""

        # 判断结论
        if "通过" in first_line and "不通过" not in first_line:
            passed = True
            level = "pass"
        elif "警告" in first_line:
            passed = True
            level = "warning"
        else:
            passed = False
            level = "warning"

        # 提取简短原因（第二行）
        message = lines[1].strip() if len(lines) > 1 else first_line
        # 详细分析（第三行起）
        details = "\n".join(lines[2:]).strip() if len(lines) > 2 else result_text

        result = {
            "passed": passed,
            "level": level,
            "message": message,
            "details": details
        }
        success(f"[事由语义审核] 输出: passed={result['passed']}, level={result['level']}, message={result['message']}")
        return result

    except Exception as e:
        error(f"[事由语义审核] 审核异常: {e}")
        return {
            "passed": True,  # 审核异常时不阻断流程
            "level": "warning",
            "message": "语义审核暂不可用",
            "details": f"审核服务异常：{e}"
        }
