# -*- coding: utf-8 -*-
"""
离线回放：runs/*.json → 五组结论 → 指标表
================================================================
不调用大模型、不烧 token，可以反复跑。
前提是先跑过一次 eval/run_model_once.py 生成了 runs/。

用法：
    python -m eval.replay
    python -m eval.replay --runs eval/runs --out eval/reports/report.json
================================================================
"""
import argparse  # 命令行参数
import json  # 读写 runs / report
import sys  # 路径处理
from pathlib import Path  # 路径处理

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:  # 保证能 import 到项目模块
    sys.path.insert(0, str(ROOT))

from eval.groups import GROUPS, run_group, HUMAN  # 五组实现 + 第三态常量

LABELS = ("通过", "不通过")  # 合法金标准取值


def silence_project_logs():
    """
    把项目日志压到只剩 WARN / ERROR。

    为什么需要：C 组要跑 12 项基础校验，每项都会打 INFO 日志，
    几十个用例的日志会把回放结果表整个淹没。这里只影响控制台输出，不改任何逻辑。
    """
    try:
        from rag.utils import logger as _lg  # 注意：这里导出的是 Logger 实例，不是模块
        original = _lg._log  # 保存原始实现
        def _quiet(level, message, *args):  # 只放行 WARN / ERROR
            if level in ("WARN", "ERROR"):
                original(level, message, *args)
        _lg._log = _quiet  # 替换实例方法（对已 import 的 info/success 同样生效）
    except Exception:
        pass  # 日志压不下去不是致命问题，继续跑


def load_runs(runs_dir):
    """读取 runs 目录下的全部 JSON，按 case_id 排序返回。"""
    runs = []  # 收集结果
    for path in sorted(Path(runs_dir).glob("*.json")):  # 遍历所有 json
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))  # 解析并加入
        except Exception as exc:  # 单个文件坏了不影响整体
            print(f"  [跳过] {path.name}: {exc}")
    runs.sort(key=lambda r: r.get("case_id") or "")  # 按用例号排序，保证输出稳定
    return runs


def _blank_stat():
    """构造一个空的统计桶。"""
    return {"correct": 0, "total": 0, "unresolved": 0, "human": 0,
            "adversarial_correct": 0, "adversarial_total": 0}


def _add(stat, pred, label, is_adv):
    """把一次判定结果累加进统计桶。"""
    stat["total"] += 1  # 总数 +1
    if not pred:  # 没给出结论（解析失败 / 字段为空）
        stat["unresolved"] += 1  # 记为"未产出结论"
    elif pred == HUMAN:  # 规则主动说"不敢判"：既不判对，也不算判错
        stat["human"] += 1
    elif pred == label:  # 判对
        stat["correct"] += 1
    if is_adv:  # 对抗样本子集
        stat["adversarial_total"] += 1
        if pred == label:
            stat["adversarial_correct"] += 1


def _rate(a, b):
    """算比例；分母为 0 时返回 None（不要伪造 0%）。"""
    return round(a / b, 4) if b else None


def evaluate(runs):
    """对全部 runs 跑五组，返回报告字典。"""
    stats = {name: _blank_stat() for name in GROUPS}  # 每组的统计桶
    failures = []  # 判错明细，方便回查是哪几条
    predictions = {}  # case_id → {组名: 结论}

    for run in runs:  # 逐个用例
        label = run.get("label")  # 金标准
        case_id = run.get("case_id") or "?"  # 用例号
        is_adv = bool(run.get("is_adversarial"))  # 是否对抗样本
        if label not in LABELS:  # 没有有效金标准就跳过（避免拿未标注样本算准确率）
            continue
        predictions[case_id] = {}  # 该用例的预测集合
        for name in GROUPS:  # 逐组判定
            pred = run_group(name, run)  # 执行该组
            predictions[case_id][name] = pred  # 记录
            _add(stats[name], pred, label, is_adv)  # 累加统计
            if pred and pred != label and pred != HUMAN:  # 判错（未产出 / 转人工单独统计，不混进错例）
                failures.append({"case_id": case_id, "group": name, "pred": pred, "label": label,
                                 "note": run.get("note") or ""})

    # ---- 反思轮效果：A → E 到底改了几条，改对还是改错 ----
    reflection = {"triggered": 0, "changed": 0, "fixed": 0, "broke": 0}  # 反思统计
    label_by_case = {r.get("case_id"): r.get("label") for r in runs}  # 用例号 → 金标准
    for case_id, preds in predictions.items():  # 遍历每个用例
        a, e = preds.get("A"), preds.get("E")  # A 组与 E 组结论
        if a != e:  # 反思改变了结论
            reflection["changed"] += 1
            label = label_by_case.get(case_id)  # 金标准
            if a != label and e == label:  # 从错改对
                reflection["fixed"] += 1
            elif a == label and e != label:  # 从对改错（误伤）
                reflection["broke"] += 1
    for run in runs:  # 统计触发了多少次反思
        if (run.get("reflection") or {}).get("triggered"):
            reflection["triggered"] += 1

    # ---- 成本 ----
    elapsed = [r.get("meta", {}).get("elapsed_sec") for r in runs if r.get("meta", {}).get("elapsed_sec")]
    cost = {
        "elapsed_sec_total": round(sum(elapsed), 1) if elapsed else None,
        "elapsed_sec_avg": round(sum(elapsed) / len(elapsed), 1) if elapsed else None,
        "measured_cases": len(elapsed),
    }

    groups_out = {}  # 输出用的分组指标
    for name, stat in stats.items():  # 逐组整理
        decided = stat["total"] - stat["human"]  # 敢下结论的样本数
        groups_out[name] = {
            "desc": GROUPS[name][0],
            "correct": stat["correct"],
            "total": stat["total"],
            "accuracy": _rate(stat["correct"], stat["total"]),  # 分母含转人工
            "unresolved": stat["unresolved"],
            "human": stat["human"],  # 转人工条数
            "human_rate": _rate(stat["human"], stat["total"]),  # 转人工率
            "decided": decided,
            "decided_accuracy": _rate(stat["correct"], decided),  # 敢判样本上的准确率
            "adversarial_correct": stat["adversarial_correct"],
            "adversarial_total": stat["adversarial_total"],
            "adversarial_accuracy": _rate(stat["adversarial_correct"], stat["adversarial_total"]),
        }

    def _pp(x, y):  # 两个准确率之差，换算成"百分点"
        if groups_out[x]["accuracy"] is None or groups_out[y]["accuracy"] is None:
            return None
        return round((groups_out[x]["accuracy"] - groups_out[y]["accuracy"]) * 100, 1)

    return {
        "run_count": len(runs),  # 用例总数
        "labeled_count": sum(1 for r in runs if r.get("label") in LABELS),  # 有金标准的数量
        "label_distribution": {lab: sum(1 for r in runs if r.get("label") == lab) for lab in LABELS},
        "groups": groups_out,
        "deltas_pp": {  # 三个关键差值（百分点）
            "B_minus_D": _pp("B", "D"),  # 结构化裁决的净收益
            "A_minus_B": _pp("A", "B"),  # 防幻觉校验的净收益
            "E_minus_A": _pp("E", "A"),  # 反思机制的净收益
        },
        "reflection": reflection,
        "cost": cost,
        "failures": failures,
    }


def print_report(report):
    """把报告打印成一张能直接贴进报告的表格。"""
    print("=" * 78)
    print(f"消融实验回放结果    用例数：{report['run_count']}（有金标准 {report['labeled_count']}）")
    print(f"标签分布：{report['label_distribution']}")
    print("=" * 78)
    print(f"{'组':<4}{'说明':<20}{'判对':>5}{'总数':>5}{'准确率':>9}{'转人工':>7}{'敢判准确':>10}{'未产出':>7}{'对抗集':>8}")
    print("-" * 78)
    for name, g in report["groups"].items():  # 逐组打印
        acc = f"{g['accuracy']*100:.1f}%" if g["accuracy"] is not None else "-"
        dacc = f"{g['decided_accuracy']*100:.1f}%" if g["decided_accuracy"] is not None else "-"
        adv = f"{g['adversarial_correct']}/{g['adversarial_total']}" if g["adversarial_total"] else "-"
        print(f"{name:<4}{g['desc']:<20}{g['correct']:>5}{g['total']:>5}{acc:>9}{g['human']:>7}{dacc:>10}{g['unresolved']:>7}{adv:>8}")
    print("-" * 78)
    print("口径：准确率分母含转人工（转人工不算判对）；敢判准确率只在规则敢下结论的样本上算。")
    print("-" * 78)
    d = report["deltas_pp"]
    print(f"结构化裁决净收益 (B-D)：{d['B_minus_D']} 个百分点")
    print(f"防幻觉校验净收益 (A-B)：{d['A_minus_B']} 个百分点")
    print(f"反思机制净收益  (E-A)：{d['E_minus_A']} 个百分点")
    print("-" * 78)
    r = report["reflection"]
    print(f"反思触发 {r['triggered']} 次，改变结论 {r['changed']} 条（改对 {r['fixed']}，改错 {r['broke']}）")
    c = report["cost"]
    if c["elapsed_sec_avg"] is not None:  # 有耗时数据才打印
        print(f"单用例平均耗时 {c['elapsed_sec_avg']} 秒，总计 {c['elapsed_sec_total']} 秒（{c['measured_cases']} 条实测）")
    if report["failures"]:  # 打印判错明细
        print("-" * 78)
        print(f"判错明细（最多列 20 条，共 {len(report['failures'])} 条）：")
        for f in report["failures"][:20]:
            print(f"  [{f['group']}] {f['case_id']}: 判「{f['pred']}」应为「{f['label']}」  {f['note'][:24]}")
    print("=" * 78)
    print("口径提醒：准确率差值要写成「+X 个百分点」，不要只写相对提升百分比；")
    print("          样本量小于 100 时，1 条 = 1/N，结论只能当趋势看。")
    print("=" * 78)


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="消融实验离线回放")
    parser.add_argument("--runs", default=str(Path(__file__).parent / "runs"), help="runs 目录")
    parser.add_argument("--out", default="", help="报告输出路径（JSON），留空则不落盘")
    args = parser.parse_args()  # 解析参数

    silence_project_logs()  # 先压日志，再跑规则，否则输出会被刷屏

    runs_dir = Path(args.runs)  # runs 目录
    if not runs_dir.exists():  # 目录不存在
        print(f"找不到 runs 目录：{runs_dir}")
        print("请先跑：python -m eval.run_model_once --cases eval/cases --out eval/runs")
        return 1

    runs = load_runs(runs_dir)  # 读取
    if not runs:  # 空目录
        print(f"{runs_dir} 里没有 runs，先跑 eval/run_model_once.py")
        return 1

    report = evaluate(runs)  # 评估
    print_report(report)  # 打印

    if args.out:  # 需要落盘
        out = Path(args.out)  # 输出路径
        out.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")  # 写 JSON
        print(f"报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())