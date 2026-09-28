# -*- coding: utf-8 -*-
"""
防幻觉校验的效果测量：注入已知幻觉，看校验层抓不抓得到
================================================================
核心前提（不承认这一点，整个测量就是假的）：
    防幻觉校验只在模型真的犯错时才有价值。
    如果测试集里模型全对，校验层的得分必然是 0 —— 白干一场。

所以这里不"等模型出错"，而是**主动往模型输出里注入已知幻觉**，
再调用 utils/verdict_guard.verify_verdict 看它抓不抓得到。
好处：可控、可重复、每一类都有确定答案。

同时必须测两件事，缺一不可：
    召回率 = 注入的幻觉里，被抓到的比例        ← 高了不代表好
    误伤率 = 未注入的正常样本里，被误报的比例  ← 「永远报警」的校验层召回 100% 但毫无用处

用法：
    python -m eval.inject_hallucination
    python -m eval.inject_hallucination --runs eval/runs --out eval/reports/hallucination.json
================================================================
"""
import argparse  # 命令行参数
import copy  # 深拷贝，避免污染原始 runs
import json  # 读写
import sys  # 路径
from pathlib import Path  # 路径

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:  # 保证能 import 到项目模块
    sys.path.insert(0, str(ROOT))

from utils.verdict_guard import verify_verdict  # 被测量的对象：防幻觉校验层
from eval.groups import trace_from_dict  # 轨迹字典 → AgentTrace 对象
from eval.replay import silence_project_logs  # 复用日志压制（注入过程也会打很多日志）


# ---------------------------------------------------------------------------
# 六个注入器：每个对应一类真实出现过的幻觉形态
#   每个注入器返回 (注入后的 run, 是否适用)；不适用返回 (None, False)
# ---------------------------------------------------------------------------
def inj_verdict_contradicts_tool(run):
    """幻觉1：工具明确判超标，结论却写「通过」（结论与工具返回矛盾）。"""
    trace = run.get("trace") or {}  # 轨迹
    has_fail = False  # 是否存在"工具判不通过"的结果
    for step in (trace.get("steps") or []):  # 遍历轨迹步骤
        result = step.get("tool_result") or {}  # 该步的工具返回值
        if step.get("kind") == "tool_result" and result.get("passed") is False:
            has_fail = True  # 找到了
            break
    if not has_fail:  # 没有可矛盾的对象，注入不适用
        return None, False
    mutated = copy.deepcopy(run)  # 深拷贝
    mutated["structured"]["verdict"] = "通过"  # 强行改成通过
    return mutated, True


def inj_unknown_evidence_source(run):
    """幻觉2：证据来源编了一个不存在的工具名。"""
    if not (run.get("structured") or {}).get("evidence"):  # 没有证据可改
        return None, False
    mutated = copy.deepcopy(run)  # 深拷贝
    mutated["structured"]["evidence"][0]["source"] = "拍脑袋想的来源"  # 非法来源
    return mutated, True


def inj_uninvoked_tool(run):
    """幻觉3：引用了一个本轮根本没调用过的工具作为证据来源。"""
    trace = run.get("trace") or {}  # 轨迹
    called = {s.get("tool_name") for s in (trace.get("steps") or [])
              if s.get("kind") == "tool_result"}  # 实际调用过的工具名
    target = "expense_standard_tool" if "expense_standard_tool" not in called else None
    if target is None:  # 该工具真的调过，换个没调过的
        target = "rag_policy_search_tool" if "rag_policy_search_tool" not in called else None
    if target is None:  # 两个工具都调过，注入不适用
        return None, False
    mutated = copy.deepcopy(run)  # 深拷贝
    mutated["structured"]["evidence"] = list(mutated["structured"].get("evidence") or [])
    mutated["structured"]["evidence"].append({  # 追加一条"引用没调过的工具"的证据
        "source": target, "fact": "该工具核算结果为合规", "passed": True,
    })
    return mutated, True


def inj_unsourceable_amount(run):
    """幻觉4：证据里出现一个票面和工具返回值里都没有的金额。"""
    mutated = copy.deepcopy(run)  # 深拷贝
    mutated["structured"]["evidence"] = list(mutated["structured"].get("evidence") or [])
    mutated["structured"]["evidence"].append({  # 追加一条数字无法溯源的证据
        "source": "invoice_field", "fact": "价税合计 999999.00 元", "passed": True,
    })
    return mutated, True


def inj_fabricated_citation(run):
    """幻觉5：引用了一条制度检索从未返回过的条款。"""
    mutated = copy.deepcopy(run)  # 深拷贝
    citations = list(mutated["structured"].get("citations") or [])  # 原有引用
    citations.append("第九十九条 根本不存在的条款")  # 编造的条款
    mutated["structured"]["citations"] = citations
    return mutated, True


def inj_pass_without_evidence(run):
    """幻觉6：结论写通过，但一条证据都没有。"""
    mutated = copy.deepcopy(run)  # 深拷贝
    mutated["structured"]["verdict"] = "通过"  # 改成通过
    mutated["structured"]["evidence"] = []  # 清空证据
    return mutated, True


INJECTORS = [  # 注入器清单：名称 → (说明, 函数)
    ("结论与工具矛盾", "工具判超标，结论写成通过", inj_verdict_contradicts_tool),
    ("证据来源非法", "source 编了一个不存在的名字", inj_unknown_evidence_source),
    ("引用未调用的工具", "引用了本轮没调用过的工具当证据", inj_uninvoked_tool),
    ("金额无法溯源", "证据里的金额票面和工具返回都没有", inj_unsourceable_amount),
    ("编造制度条款", "引用了检索从未返回过的条款", inj_fabricated_citation),
    ("通过但无证据", "结论通过且证据为空", inj_pass_without_evidence),
]


def _flag(report):
    """从校验报告里取出「是否报警」：硬拦截(error) 和 软提醒(warning) 分开记。"""
    errors = report.get("errors") or []  # error 级问题
    warnings = report.get("warnings") or []  # warning 级问题
    return bool(errors), bool(errors or warnings)  # (硬拦截, 任意报警)


def _verify(run):
    """对一个 run 跑校验层。"""
    return verify_verdict(
        run.get("structured") or {},
        trace=trace_from_dict(run.get("trace")),
        invoice_data=run.get("invoice_data") or {},
        anomaly_result=run.get("anomaly_result"),
    )


def load_runs(runs_dir):
    """读取 runs 目录。"""
    runs = []  # 结果
    for path in sorted(Path(runs_dir).glob("*.json")):  # 遍历
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass  # 坏文件跳过
    return runs


def measure(runs):
    """跑基线误伤率 + 六类注入召回率。"""
    # ---- 基线：误伤率只该在「模型本身判对了」的样本上算 ----
    # 为什么这样定义：如果模型本来就判错了（例如结论与工具返回矛盾），
    # 校验层报警正是它该做的事。把这类样本算进"误报"，会让误伤率虚高，
    # 甚至得出"校验层几乎全是误报"的错误结论。
    clean = [r for r in runs
             if (r.get("structured") or {}).get("verdict") == r.get("label")
             and not r.get("is_adversarial")]  # 模型判对且非对抗样本
    baseline = {"total": 0, "hard": 0, "any": 0}  # 误伤统计桶
    for run in clean:  # 逐条干净样本
        baseline["total"] += 1
        hard, any_flag = _flag(_verify(run))  # 跑校验
        baseline["hard"] += 1 if hard else 0
        baseline["any"] += 1 if any_flag else 0

    # ---- 透明披露：全样本的报警率（含模型自己判错的样本），不作为误伤率 ----
    all_flag = {"total": 0, "hard": 0, "any": 0}  # 全样本统计桶
    for run in runs:  # 逐条样本
        all_flag["total"] += 1
        hard, any_flag = _flag(_verify(run))  # 跑校验
        all_flag["hard"] += 1 if hard else 0
        all_flag["any"] += 1 if any_flag else 0

    # ---- 注入：每类幻觉的召回率 ----
    details = []  # 逐类结果
    for name, desc, fn in INJECTORS:  # 逐类注入
        eligible = 0  # 适用样本数
        hard_hit = 0  # 硬拦截命中数
        any_hit = 0  # 任意报警命中数
        for run in runs:  # 逐条样本
            mutated, ok = fn(run)  # 注入
            if not ok:  # 不适用就跳过（不要拿不适用的样本算分母）
                continue
            eligible += 1
            hard, any_flag = _flag(_verify(mutated))  # 跑校验看抓不抓得到
            hard_hit += 1 if hard else 0
            any_hit += 1 if any_flag else 0
        details.append({
            "name": name,
            "desc": desc,
            "eligible": eligible,
            "hard_hit": hard_hit,
            "any_hit": any_hit,
            "hard_recall": round(hard_hit / eligible, 4) if eligible else None,
            "any_recall": round(any_hit / eligible, 4) if eligible else None,
        })

    total_eligible = sum(d["eligible"] for d in details)  # 注入总数
    total_hard = sum(d["hard_hit"] for d in details)  # 硬拦截总数
    total_any = sum(d["any_hit"] for d in details)  # 任意报警总数

    return {
        "runs": len(runs),  # 样本数
        "false_positive": {  # 误伤率（只在模型判对的干净样本上算）
            "basis": "模型结论与金标准一致、且非对抗样本",
            "total": baseline["total"],
            "hard": baseline["hard"],
            "any": baseline["any"],
            "hard_rate": round(baseline["hard"] / baseline["total"], 4) if baseline["total"] else None,
            "any_rate": round(baseline["any"] / baseline["total"], 4) if baseline["total"] else None,
        },
        "all_samples_flag_rate": {  # 透明披露：全样本报警率（含模型自己判错的样本）
            "total": all_flag["total"],
            "hard": all_flag["hard"],
            "any": all_flag["any"],
            "hard_rate": round(all_flag["hard"] / all_flag["total"], 4) if all_flag["total"] else None,
            "any_rate": round(all_flag["any"] / all_flag["total"], 4) if all_flag["total"] else None,
        },
        "recall": {  # 召回率（注入的幻觉被抓到）
            "injected": total_eligible,
            "hard_hit": total_hard,
            "any_hit": total_any,
            "hard_rate": round(total_hard / total_eligible, 4) if total_eligible else None,
            "any_rate": round(total_any / total_eligible, 4) if total_eligible else None,
        },
        "by_type": details,
    }


def print_report(report):
    """打印结果表。"""
    print("=" * 78)
    print(f"防幻觉校验效果测量    样本数：{report['runs']}")
    print("=" * 78)
    print("【召回率】注入的幻觉里，被校验层抓到的比例")
    print(f"{'幻觉类型':<20}{'适用':>6}{'硬拦截':>8}{'任意报警':>10}{'硬拦截召回':>12}{'任意召回':>10}")
    print("-" * 78)
    for d in report["by_type"]:  # 逐类打印
        hard = f"{d['hard_recall']*100:.0f}%" if d["hard_recall"] is not None else "-"
        any_r = f"{d['any_recall']*100:.0f}%" if d["any_recall"] is not None else "-"
        print(f"{d['name']:<20}{d['eligible']:>6}{d['hard_hit']:>8}{d['any_hit']:>10}{hard:>12}{any_r:>10}")
    print("-" * 78)
    r = report["recall"]
    hr = f"{r['hard_rate']*100:.1f}%" if r["hard_rate"] is not None else "-"
    ar = f"{r['any_rate']*100:.1f}%" if r["any_rate"] is not None else "-"
    print(f"{'合计':<20}{r['injected']:>6}{r['hard_hit']:>8}{r['any_hit']:>10}{hr:>12}{ar:>10}")
    print("=" * 78)
    print("【误伤率】在「模型本身判对且非对抗」的样本上，校验层误报的比例")
    print("  口径：严格的「误伤」只看硬拦截(error)——把对的结论改错了才叫误伤；")
    print("        warning 按设计就不拦截，只作提示，因此单独列出、不计入误伤。")
    f = report["false_positive"]
    fh = f"{f['hard_rate']*100:.1f}%" if f["hard_rate"] is not None else "-"
    fa = f"{f['any_rate']*100:.1f}%" if f["any_rate"] is not None else "-"
    print(f"  干净样本 {f['total']} 条：硬拦截误报 {f['hard']} 条（{fh}），任意报警误报 {f['any']} 条（{fa}）")
    a = report["all_samples_flag_rate"]  # 透明披露
    ah = f"{a['hard_rate']*100:.1f}%" if a["hard_rate"] is not None else "-"
    aa = f"{a['any_rate']*100:.1f}%" if a["any_rate"] is not None else "-"
    print(f"  （透明披露）全样本 {a['total']} 条：硬拦截 {a['hard']} 条（{ah}），任意报警 {a['any']} 条（{aa}）")
    print("  注：全样本里含模型自己判错的样本，它们被报警是校验层该做的事，不计入误伤。")
    print("=" * 78)
    print("口径提醒：只报召回率是耍流氓。「永远报警」的校验层召回 100%、误伤 100%，毫无用处。")
    print("          两个数字必须一起给。")
    print("=" * 78)


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="防幻觉校验效果测量")
    parser.add_argument("--runs", default=str(Path(__file__).parent / "runs"), help="runs 目录")
    parser.add_argument("--out", default="", help="报告输出路径（JSON）")
    args = parser.parse_args()

    silence_project_logs()  # 先压日志，再跑测量

    runs_dir = Path(args.runs)  # runs 目录
    if not runs_dir.exists():  # 不存在
        print(f"找不到 runs 目录：{runs_dir}")
        return 1
    runs = load_runs(runs_dir)  # 读取
    if not runs:  # 空
        print(f"{runs_dir} 里没有 runs")
        return 1

    report = measure(runs)  # 测量
    print_report(report)  # 打印
    if args.out:  # 落盘
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())