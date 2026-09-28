# -*- coding: utf-8 -*-
"""
生成「演示用」runs，仅用于验证 eval/replay.py 与 eval/inject_hallucination.py 能跑通
================================================================
⚠️ 这些不是真实实验数据。里面的模型输出是手工构造的，目的是让每个脚本在
   "还没烧 token" 的情况下也能立刻看到输出长什么样、指标怎么算。

真实实验数据请用：
    python -m eval.run_model_once --cases eval/cases --out eval/runs

每个演示用例覆盖一种关键情形：
    demo_01  合规住宿，正常通过           → 五组都应判对
    demo_02  超标住宿，正常不通过         → 五组都应判对
    demo_03  陷阱句（否定嵌套）           → D 组会判反，其余判对
    demo_04  模型幻觉：结论与工具返回矛盾  → B 组会错，A 组被校验层改写救回
    demo_05  反思触发但不改变结论          → A 与 E 结论一致

用法：
    python -m eval.make_demo_runs
================================================================
"""
import json  # 写 JSON
import sys  # 路径
from pathlib import Path  # 路径

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = Path(__file__).parent / "runs_demo"  # 输出目录（刻意与真实 runs 分开）


def _invoice(no, amount, item="*住宿服务*住宿费"):
    """
    构造一份字段完整的发票。

    注意：演示数据本身必须能过基础校验。如果因为「发票号不是20位」「明细缺税额」
    这类演示数据自身的瑕疵让 C 组判不通过，测出来的就不是规则的能力，而是造数据的失误。
    """
    return {
        "发票类型": "电子发票（普通发票）",
        "发票号码": no,  # 必须是 20 位数字
        "开票日期": "2026年09月14日",
        "购买方名称": "苏州城市学院",
        "购买方税号": "12320500MB1F99368P",  # 用配置里的本公司税号，避免抬头校验干扰
        "销售方名称": "某某酒店管理有限公司",
        "销售方税号": "91321323MA1WBFAU8N",
        "项目名称": item,
        "价税合计小写": str(amount),
        "价税合计大写": "金额大写演示",  # 非空即可（该检查只判空）
        "不含税金额": str(amount),
        "税额": "0.00",
        "明细列表": [{"项目名称": item, "金额": str(amount), "税额": "0.00",
                      "税率": "免税", "数量": "1", "单价": str(amount)}],
    }


def _tool_step(result):
    """构造一条费用标准工具的返回步骤。"""
    return {"step": 2, "kind": "tool_result", "text": "", "tool_name": "expense_standard_tool",
            "tool_args": {}, "tool_result": result, "ok": True}


def _rag_step(text="第十四条 住宿费标准（元/天）：一线城市普通员工400元，部门经理500元。"):
    """
    构造一条制度检索工具的返回步骤。

    为什么必须有：校验层会核对「结论引用的条款是否真的由检索返回过」。
    手工构造的 runs 如果只放费用标准工具、却带着 citations，
    会被正确地判为「引用无法核实」—— 那是校验层判对了，不是误报。
    """
    return {"step": 3, "kind": "tool_result", "text": "", "tool_name": "rag_policy_search_tool",
            "tool_args": {}, "tool_result": {"query_terms": "住宿费 标准", "policy_text": text,
                                             "clause_count": 1}, "ok": True}


def _std(passed, standard, actual, exceed, level="普通员工", city_tier="一线城市"):
    """构造费用标准工具的返回值。"""
    return {"passed": passed, "standard": standard, "actual": actual, "exceed": exceed,
            "city_tier": city_tier, "city_recognized": True, "level_recognized": True,
            "source_clause": "第十四条 住宿费标准（元/天）",
            "caliber": f"上海（{city_tier}），{level}，标准{standard}元/天",
            "needs_human": False,
            "message": f"住宿费{actual}元，标准{standard}元/天"}


def _run(case_id, note, label, answer, structured, tool_result, is_adv=False, reflection=None):
    """组装一条 runs 记录。"""
    return {
        "case_id": case_id, "source": "演示数据", "note": note,
        "label": label, "label_reason": "演示用，非真实标注", "is_adversarial": is_adv,
        "employee_id": 1, "employee_level": "普通员工",
        "invoice_data": _invoice("2632" + case_id.replace("demo_", "").zfill(16),  # 4+16=20 位数字，格式校验能过
                                 tool_result.get("actual", 0)),
        "expense_form": {"expense_type": "住宿费", "trip_no": "TRIP-DEMO",
                         "ext_fields": {"city": "上海", "nights": 1, "rooms": 1, "guests": 1},
                         "detail_items": [], "total_amount": tool_result.get("actual", 0)},
        "anomaly_result": {"has_risk": False, "risk_count": 0, "risk_items": [], "all_results": {}},
        "answer": answer,
        "structured": structured,
        "trace": {"steps": [_tool_step(tool_result), _rag_step()], "final_answer": answer,
                  "structured": structured, "error": ""},
        "guard_report": {},
        "reflection": reflection or {"enabled": True, "triggered": False},
        "meta": {"elapsed_sec": 42.0, "model": "demo"},
    }


def build():
    """构造全部演示用例。"""
    runs = []  # 收集

    # demo_01：合规（350 <= 400），结论通过，证据与工具返回一致
    runs.append(_run(
        "demo_01", "合规住宿350元（标准400）", "通过",
        "✅ 审核通过\n理由：住宿费350元未超过一线城市普通员工标准400元/天。",
        {"verdict": "通过", "expense_type": "住宿费",
         "reasons": ["住宿费350元未超过标准400元/天"],
         "evidence": [{"source": "expense_standard_tool",
                       "fact": "住宿费350元，一线城市普通员工标准400元/天，未超标", "passed": True}],
         "citations": ["第十四条 住宿费标准"], "confidence": 0.9,
         "needs_human": False, "explanation": "住宿费未超标，可以报销。"},
        _std(True, 400, 350, 0),
    ))

    # demo_02：超标（480 > 400），结论不通过
    runs.append(_run(
        "demo_02", "超标住宿480元（标准400）", "不通过",
        "❌ 审核不通过\n原因：住宿费480元超过标准400元/天，超标80元。",
        {"verdict": "不通过", "expense_type": "住宿费",
         "reasons": ["住宿费480元超过标准400元/天，超标80元"],
         "evidence": [{"source": "expense_standard_tool",
                       "fact": "住宿费480元，一线城市普通员工标准400元/天，超标80元", "passed": False}],
         "citations": ["第十四条 住宿费标准"], "confidence": 0.9,
         "needs_human": False, "explanation": "住宿费超标80元，需个人承担或补充审批。"},
        _std(False, 400, 480, 80.0),
    ))

    # demo_03：陷阱句 —— 结论在文末，D 组按"谁先出现"会判成「通过」
    runs.append(_run(
        "demo_03", "陷阱句：否定嵌套，结论在文末", "不通过",
        "我通过调用费用标准工具核对后确认，该笔住宿费超标，审核不通过",
        {"verdict": "不通过", "expense_type": "住宿费",
         "reasons": ["住宿费480元超过标准400元/天，超标80元"],
         "evidence": [{"source": "expense_standard_tool",
                       "fact": "住宿费480元，一线城市普通员工标准400元/天，超标80元", "passed": False}],
         "citations": ["第十四条 住宿费标准"], "confidence": 0.85,
         "needs_human": False, "explanation": "住宿费超标80元。"},
        _std(False, 400, 480, 80.0),
        is_adv=True,
    ))

    # demo_04：模型幻觉 —— 工具明明判超标，结论却写「通过」；A 组应被校验层改写回「不通过」
    runs.append(_run(
        "demo_04", "模型幻觉：工具判超标但结论写通过", "不通过",
        "✅ 审核通过\n理由：住宿费用合理。",
        {"verdict": "通过", "expense_type": "住宿费",
         "reasons": ["住宿费用合理"],
         "evidence": [{"source": "expense_standard_tool",
                       "fact": "住宿费480元，一线城市普通员工标准400元/天，超标80元", "passed": False}],
         "citations": ["第十四条 住宿费标准"], "confidence": 0.8,
         "needs_human": False, "explanation": "住宿费合理。"},
        _std(False, 400, 480, 80.0),
        is_adv=True,
    ))

    # demo_05：反思触发（命中可澄清项），但两轮结论一致 → A == E
    runs.append(_run(
        "demo_05", "反思触发但结论不变", "不通过",
        "❌ 审核不通过\n原因：住宿费480元超标80元。",
        {"verdict": "不通过", "expense_type": "住宿费",
         "reasons": ["住宿费480元超过标准400元/天"],
         "evidence": [{"source": "expense_standard_tool",
                       "fact": "住宿费480元，一线城市普通员工标准400元/天，超标80元", "passed": False}],
         "citations": ["第十四条 住宿费标准"], "confidence": 0.4,
         "needs_human": False, "explanation": "住宿费超标。"},
        _std(False, 400, 480, 80.0),
        reflection={"enabled": True, "triggered": True,
                    "signals": [{"name": "置信度水平", "level": "warn", "detail": "置信度偏低"}],
                    "second_answer": "❌ 审核不通过\n原因：复核后仍超标80元。",
                    "second_structured": {"verdict": "不通过", "citations": ["第十四条 住宿费标准"],
                                          "evidence": [], "reasons": ["复核后仍超标"]},
                    "second_trace": {"steps": [_tool_step(_std(False, 400, 480, 80.0)), _rag_step()],
                                     "final_answer": "", "structured": {}, "error": ""}},
    ))
    return runs


def main():
    """写入演示 runs。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    runs = build()  # 构造
    for run in runs:  # 逐个写文件
        path = OUT_DIR / (run["case_id"] + ".json")  # 目标文件
        path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")  # 落盘
    print(f"已生成 {len(runs)} 个演示 runs → {OUT_DIR}")
    print("试跑：python -m eval.replay --runs eval/runs_demo")
    print("     python -m eval.inject_hallucination --runs eval/runs_demo")
    return 0


if __name__ == "__main__":
    sys.exit(main())