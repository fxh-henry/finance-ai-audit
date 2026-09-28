# -*- coding: utf-8 -*-
"""
对用例集跑一次模型，把原始输出固化成 runs/*.json
================================================================
这是整个消融实验里**唯一烧 token 的一步**，每个用例只跑一次。
跑完之后五组（C/D/B/A/E）共用这一份 runs，差异只来自后处理层，
不会被模型随机性掩盖。可以反复回放，不用重复花钱。

【为什么不用现成的 run_expense_form_audit】
pipeline.run_expense_form_audit 在内部会把 verdict["verdict"] **原地改写**成
校验层/反思轮最终采用的结论。也就是说它返回的 structured 已经是"事后"的了。

如果直接拿它当 runs 存下来：
    B 组读到的就是已被校验层改写过的结论  →  B 组 == A 组
    实验会得出"防幻觉校验收益 0 个百分点"  →  这是测量错误，不是真实结论。

所以这里自己走一遍同样的流程，但在**校验层改写之前**把原始裁决存下来：
    structured_raw  → B 组输入（模型原始判断）
    trace_raw       → A 组输入（用于交叉核对）
    reflection.*    → E 组输入（反思轮结果）

【runs 里存什么，决定回放能算什么】
    invoice_data / employee_id / anomaly_result   → C 组要独立跑规则
    answer                                        → D 组要拿自然语言去猜
    structured                                    → B 组要读字段
    trace                                         → A 组要交叉核对
    reflection                                    → E 组要用
    meta                                          → 耗时、模型名

用法：
    python -m eval.run_model_once --cases eval/cases --out eval/runs
    python -m eval.run_model_once --cases eval/cases --out eval/runs --limit 3
    python -m eval.run_model_once --cases eval/cases --out eval/runs --no-reflection
================================================================
"""
import argparse  # 命令行参数
import json  # 读写
import sys  # 路径
import time  # 计时
from pathlib import Path  # 路径

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:  # 保证能 import 到项目模块
    sys.path.insert(0, str(ROOT))

from config.settings import REFLECTION_ENABLED, REFLECTION_MAX_ROUNDS  # 反思开关
from database.db import get_employee  # 按ID查员工（取职级）
from audit.anomaly_detect import run_all_anomaly_checks  # 6 项风控
from agent.review_agent import ReviewAgent  # 审查 Agent
from utils.verdict_guard import verify_verdict  # 防幻觉校验
from utils.reflection import should_reflect, build_reflection_prompt  # 反思触发与指令构造
from pipeline import format_anomaly_for_agent  # 风控结果 → Agent 可读文本


def load_cases(cases_dir):
    """读取用例目录下的全部 JSON。"""
    cases = []  # 收集
    for path in sorted(Path(cases_dir).glob("*.json")):  # 遍历
        cases.append(json.loads(path.read_text(encoding="utf-8")))  # 解析
    return cases


def run_one(case, allow_reflection=True):
    """对一个用例跑完整流程，返回可写入 runs 的字典。"""
    case_id = case.get("case_id") or "case"  # 用例号
    employee_id = case.get("employee_id")  # 员工ID（决定职级档位）
    employee = get_employee(employee_id) if employee_id else None  # 查员工
    invoice_data = case.get("invoice_data") or {}  # 发票字段
    expense_form = case.get("expense_form")  # 报销单字段（可为 None）

    started = time.time()  # 开始计时

    # ---- 第1步：风控（纯规则，先跑出来作为 Agent 的上下文） ----
    anomaly_result = run_all_anomaly_checks(employee_id, invoice_data)  # 6 项检测
    anomaly_context = format_anomaly_for_agent(anomaly_result)  # 转文本

    # ---- 第2步：Agent 第一轮审核（拿原始裁决，不经校验层改写） ----
    agent = ReviewAgent(invoice_data, expense_form=expense_form, employee=employee)  # 创建 Agent
    initial_prompt = (  # 与线上完全一致的初始指令
        "请审核这张发票及其对应的报销单，给出最终审核结论。\n\n"
        "【风控检测结果】\n"
        f"{anomaly_context}\n\n"
        "请结合以上风控检测结果和报销制度，给出最终审核结论。"
        "如果风控检测发现风险，请在结论中重点说明。"
    )
    audit = agent.audit(initial_prompt)  # 第一轮：结构化裁决 + 轨迹
    answer = audit["answer"]  # 自然语言结论（D 组输入）
    structured_raw = audit["verdict"] or {}  # 原始结构化裁决（B 组输入）
    trace_raw = audit["trace"]  # 第一轮轨迹（A 组输入）

    # ---- 第3步：校验一遍，判断要不要反思（结果只用于记录，不改写裁决） ----
    guard_report = verify_verdict(  # 跑防幻觉校验
        verdict=structured_raw,
        trace=trace_raw,
        invoice_data=invoice_data,
        anomaly_result=anomaly_result,
    )

    reflection = {"enabled": bool(REFLECTION_ENABLED), "triggered": False}  # 反思记录
    if allow_reflection and REFLECTION_ENABLED and REFLECTION_MAX_ROUNDS >= 1:  # 允许反思才走
        need_reflect, hits = should_reflect(guard_report, trace_raw)  # 判断是否触发
        if need_reflect:  # 命中可澄清矛盾
            prompt = build_reflection_prompt(hits, structured_raw)  # 构造第二轮指令
            audit2 = agent.audit(prompt)  # 第二轮：真实调用模型（这是唯一额外成本）
            reflection = {  # 完整记录反思轮，供 E 组离线回放
                "enabled": True,
                "triggered": True,
                "signals": [dict(c) for c in hits],
                "second_answer": audit2["answer"],
                "second_structured": audit2["verdict"] or {},
                "second_trace": audit2["trace"].to_dict(),
            }

    elapsed = round(time.time() - started, 1)  # 耗时

    return {  # 组装 runs 记录
        "case_id": case_id,
        "source": case.get("source"),  # 用例来源（制度反推 / 故障注入 / 语义类）
        "note": case.get("note"),  # 人看的说明
        "label": case.get("label"),  # 金标准
        "label_reason": case.get("label_reason"),  # 金标准依据
        "is_adversarial": bool(case.get("is_adversarial")),  # 是否对抗样本
        "employee_id": employee_id,
        "employee_level": (employee or {}).get("level"),  # 本次核算用的职级（便于复核）
        "invoice_data": invoice_data,
        "expense_form": expense_form,
        "anomaly_result": anomaly_result,  # C/A 组要用
        "answer": answer,  # D 组要用
        "structured": structured_raw,  # B 组要用（未经校验层改写）
        "trace": trace_raw.to_dict(),  # A 组要用
        "guard_report": {  # 第一遍校验的结论，纯记录，回放时不直接采用
            "original_verdict": guard_report.get("original_verdict"),
            "final_verdict": guard_report.get("final_verdict"),
            "overridden": guard_report.get("overridden"),
            "errors": guard_report.get("errors"),
            "warnings": guard_report.get("warnings"),
        },
        "reflection": reflection,  # E 组要用
        "meta": {"elapsed_sec": elapsed, "model": _model_name()},  # 成本信息
    }


def _model_name():
    """取当前配置的模型名（失败不影响主流程）。"""
    try:
        from config.settings import LLM_MODEL  # 延迟导入
        return LLM_MODEL
    except Exception:
        return "unknown"


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="跑一次模型，生成 runs")
    parser.add_argument("--cases", default=str(Path(__file__).parent / "cases"), help="用例目录")
    parser.add_argument("--out", default=str(Path(__file__).parent / "runs"), help="runs 输出目录")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个用例（调试用，0=全部）")
    parser.add_argument("--no-reflection", action="store_true", help="不跑反思轮（省一半 token）")
    parser.add_argument("--force", action="store_true", help="已存在的 runs 也重跑")
    args = parser.parse_args()

    cases_dir = Path(args.cases)  # 用例目录
    out_dir = Path(args.out)  # 输出目录
    if not cases_dir.exists():  # 用例目录不存在
        print(f"找不到用例目录：{cases_dir}")
        return 1

    cases = load_cases(cases_dir)  # 读用例
    if args.limit:  # 限制数量
        cases = cases[: args.limit]
    if not cases:  # 没有用例
        print(f"{cases_dir} 里没有用例 JSON")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    print(f"将处理 {len(cases)} 个用例，输出到 {out_dir}（反思轮：{'关闭' if args.no_reflection else '开启'}）")

    done = 0  # 成功计数
    for case in cases:  # 逐个用例
        case_id = case.get("case_id") or "case"  # 用例号
        target = out_dir / f"{case_id}.json"  # 目标文件
        if target.exists() and not args.force:  # 已跑过且不强制重跑
            print(f"  [跳过] {case_id}（已存在，--force 可强制重跑）")
            continue
        print(f"  [运行] {case_id} ...")
        try:
            record = run_one(case, allow_reflection=not args.no_reflection)  # 跑一遍
        except Exception as exc:  # 单条失败不影响整批
            print(f"  [失败] {case_id}: {exc}")
            continue
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")  # 落盘
        done += 1
        print(f"         → 结论={record['structured'].get('verdict')} 金标准={record.get('label')} "
              f"耗时={record['meta']['elapsed_sec']}s")

    print(f"完成：成功 {done} / 共 {len(cases)}")
    print("接下来跑：python -m eval.replay")
    return 0


if __name__ == "__main__":
    sys.exit(main())