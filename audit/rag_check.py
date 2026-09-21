# -*- coding: utf-8 -*-
# ============================================================================
# RAG制度校验工具
# ============================================================================
# 作用：根据发票信息，自动检索相关的报销制度条款
# 特点：不做判断，只检索制度原文，给LLM提供判断依据
# 与expense_standard的区别：
#   expense_standard = 纯代码计算（住宿费超不超标）
#   rag_check        = 检索制度原文（找依据、找特殊规定）
# ============================================================================

import sys
from pathlib import Path

# 把项目根目录加入Python搜索路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag import get_rag_service
from rag.utils.logger import info, success, warn


# ============================================================================
# 工具函数：根据发票信息构造检索查询词
# ============================================================================
def build_query(invoice_data):
    """
    根据发票数据智能构造RAG检索查询词

    策略：
    1. 从项目名称中识别费用类型（住宿费/餐饮费/交通费等）
    2. 结合金额、销售方等信息，构造精准查询
    3. 如果识别不出类型，用通用查询

    参数：
        invoice_data: 发票识别结果字典

    返回：
        查询词字符串
    """
    item_name = invoice_data.get("项目名称", "")
    amount = invoice_data.get("价税合计小写", "")
    seller = invoice_data.get("销售方名称", "")

    # 根据项目名称关键词匹配费用类型
    if any(kw in item_name for kw in ["住宿", "酒店", "宾馆", "旅店", "房费"]):
        return f"住宿费报销标准 超标 特殊审批"

    if any(kw in item_name for kw in ["餐饮", "餐费", "招待", "宴会", "食品"]):
        return f"业务招待费报销标准 人均标准 审批要求"

    if any(kw in item_name for kw in ["运输", "客运", "出租车", "网约车", "地铁", "公交", "打车"]):
        return f"市内交通费报销标准 连号票 不予报销"

    if any(kw in item_name for kw in ["铁路", "航空", "机票", "火车", "高铁", "飞机", "行程单"]):
        return f"差旅费 交通工具标准 火车票 机票 报销"

    if any(kw in item_name for kw in ["办公", "文具", "打印", "耗材", "用品"]):
        return f"办公费报销标准 采购申请 明细清单"

    if any(kw in item_name for kw in ["培训", "会议", "学习", "考试"]):
        return f"培训费 会议费 报销标准 审批要求"

    if any(kw in item_name for kw in ["通讯", "电话", "手机", "话费"]):
        return f"通讯费报销标准 补贴标准"

    # 识别不出具体类型，用通用查询
    info(f"未识别出费用类型，项目名称：{item_name}，使用通用查询")
    return f"费用报销 审核要点 发票要求 {item_name}"


# ============================================================================
# 主函数：RAG制度检索
# ============================================================================
def rag_check(invoice_data, top_k=3):
    """
    根据发票信息检索相关的报销制度条款

    参数：
        invoice_data: 发票识别结果字典
        top_k: 返回前N条检索结果，默认3

    返回：
        {
            "query": 检索用的查询词,
            "policy_text": 检索到的制度条款文本,
            "source_count": 检索到的条款数量
        }
    """
    # 第1步：构造查询词
    query = build_query(invoice_data)
    info(f"[RAG制度校验] 构造查询词: {query}")

    # 第2步：调用RAG检索
    rag = get_rag_service()
    policy_text = rag.retrieve(query, top_k=top_k)

    # 第3步：统计检索到的资料数量（检索结果格式是 [资料1]...[资料2]...）
    source_count = policy_text.count("[资料")

    success(f"[RAG制度校验] 检索完成，共{source_count}条制度依据")

    return {
        "query": query,
        "policy_text": policy_text,
        "source_count": source_count
    }


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("RAG制度校验测试")
    print("=" * 60)

    # 测试1：住宿费发票
    print("\n测试1：住宿费发票")
    invoice1 = {
        "项目名称": "*生产生活服务*代订房费",
        "价税合计小写": "2584.00",
        "销售方名称": "去哪儿网（天津）国际旅行社有限公司"
    }
    result1 = rag_check(invoice1)
    print(f"  查询词: {result1['query']}")
    print(f"  检索到{result1['source_count']}条制度依据")
    print(f"  制度内容前100字: {result1['policy_text'][:100]}...")

    # 测试2：办公费发票
    print("\n测试2：办公费发票")
    invoice2 = {
        "项目名称": "*办公用品*打印纸",
        "价税合计小写": "500.00",
        "销售方名称": "某办公用品公司"
    }
    result2 = rag_check(invoice2)
    print(f"  查询词: {result2['query']}")
    print(f"  检索到{result2['source_count']}条制度依据")
