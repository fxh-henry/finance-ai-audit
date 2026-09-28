# -*- coding: utf-8 -*-
"""
ReviewAgent 深度审核全流程测试（不改动原有代码）
运行：python tests/test_review_agent_flow.py
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ========== 测试数据：2584元住宿费（超标案例） ==========
TEST_INVOICE = {
    "发票类型": "电子发票（普通发票）",
    "发票号码": "26127000000370193970",
    "开票日期": "2026-09-10",
    "购买方名称": "苏州城市学院",
    "销售方名称": "去哪儿网（天津）国际旅行社有限公司武清分公司",
    "项目名称": "*生产生活服务*代订房费",
    "价税合计小写": "2584.00",
    "明细列表": [{"项目名称": "*生产生活服务*代订房费", "金额": "2584.00", "税率": "免税"}]
}

TEST_EXPENSE_FORM = {
    "trip_no": "TRIP20260905",
    "expense_type": "住宿费",
    "occur_date": "2026-09-05",
    "reason": "异地客户拜访，酒店住宿",
    "total_amount": 2584.00,
    "participants": "张三",
    "ext_fields": {"住宿晚数": 2, "房间数": 1, "入住城市": "天津"},
    "detail_items": []
}

def sep(title):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")

def show_step(step):
    kind_map = {"thinking":"思考","tool_call":"调用工具","tool_result":"工具返回","answer":"最终回答","structured":"结构化裁决"}
    print(f"\n  [第{step.step}步] {kind_map.get(step.kind, step.kind)}")
    if step.text: print(f"  文本: {step.text[:300]}")
    if step.tool_name: print(f"  工具: {step.tool_name}")
    if step.tool_args: print(f"  入参: {json.dumps(step.tool_args, ensure_ascii=False)}")
    if step.tool_result:
        print(f"  返回: {json.dumps(step.tool_result, ensure_ascii=False)[:400]}")

def main():
    sep("ReviewAgent 深度审核全流程测试")
    print("\n【输入】")
    print("发票:", json.dumps(TEST_INVOICE, ensure_ascii=False)[:300])
    print("报销单:", json.dumps(TEST_EXPENSE_FORM, ensure_ascii=False)[:300])

    # 第1步：创建Agent
    sep("第1步: ReviewAgent(invoice_data, expense_form=...)")
    from agent.review_agent import ReviewAgent
    agent = ReviewAgent(invoice_data=TEST_INVOICE, expense_form=TEST_EXPENSE_FORM)
    print("✓ Agent创建完成（内部：初始化LLM + 系统提示词 + 注册工具 + LangGraph ReAct Agent）")

    # 第2步：执行审核
    sep("第2步: agent.audit() 完整审核")
    print("内部: ReAct循环(思考→调工具→看结果→结论) → 结构化抽取")
    result = agent.audit()

    # 第3步：打印轨迹
    sep("第3步: 运行轨迹 trace（每一步干了什么）")
    trace = result["trace"]
    print(f"共 {len(trace.steps)} 步")
    for step in trace.steps:
        show_step(step)

    # 第4步：自然语言结论
    sep("第4步: 自然语言结论 answer（展示给员工的）")
    print(result["answer"])

    # 第5步：结构化裁决
    sep("第5步: 结构化裁决 verdict JSON（机器可读）")
    print(json.dumps(result["verdict"], ensure_ascii=False, indent=2))

    # 第6步：防幻觉校验
    sep("第6步: verify_verdict 防幻觉硬校验")
    from utils.verdict_guard import verify_verdict
    report = verify_verdict(verdict=result["verdict"], trace=trace,
                            invoice_data=TEST_INVOICE, anomaly_result=None)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 第7步：最终结论
    sep("第7步: extract_verdict 最终结论")
    from utils.audit_verdict import extract_verdict
    print("最终结论:", extract_verdict(result["verdict"]))

    sep("全流程结束")

if __name__ == "__main__":
    main()
