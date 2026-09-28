# -*- coding: utf-8 -*-
"""
陷阱句注入测量：量化「结构化裁决」相对「字符串位置解析」到底值多少
================================================================
为什么必须用注入法，而不是靠造用例：
    实测模型输出的固定风格是**开头就给结论**（例如「❌ 审核不通过」）。
    这时「不通过」的起始位置比它内部那个「通过」早 1 个字符，
    旧解析器恰好解析正确 —— 也就是说：
        旧解析器的缺陷是「文本属性」，不是「用例属性」。

    光靠造用例，自然触发率就是 0，实验会得出
    「结构化裁决提升 0 个百分点」——那是假结论，不是测量结果。

    所以这里沿用 inject_hallucination.py 的思路：
    不去等模型犯错，而是主动把结论的自然语言措辞，
    改写成「已知会骗过旧解析器」的句式，再看旧解析器翻不翻车。

三条防止自欺的约束：
    1. 注入的句子必须「模型真的可能这么写」，不能生造怪句。
       下面 5 种基础措辞 + 1 族否定嵌套句（每个方向 4 条）全部来自真实审核话术
       （工具叙事 / 分点核验 / 文末裁定 / 双重否定 / 不予通过 等）。
    2. 只改措辞、不改语义：语义仍等于该用例的**金标准**，
       所以对照的金标准没有变，翻转了就是解析器的错。
    3. 必须同时报两个方向的错误：
         错误放行率 = 真值「不通过」却被解析成「通过」（最危险的漏检）
         误伤率     = 真值「通过」却被解析成「不通过」
       只报一个方向就是耍流氓。

另外注意：B / A 两组根本不读自然语言（它们读结构化字段），
所以注入措辞对它们毫无影响 —— 这正好把「结构化裁决的价值」隔离出来了。

用法：
    python -m eval.inject_trap_text --runs eval/runs
    python -m eval.inject_trap_text --runs eval/runs --out eval/reports/trap.json
================================================================
"""
import argparse  # 命令行参数
import json  # 读写
import sys  # 路径
from pathlib import Path  # 路径

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:  # 保证能 import 到项目模块
    sys.path.insert(0, str(ROOT))

from eval.groups import group_A, group_B  # 两个不读自然语言的对照组
from eval.legacy_text_verdict import legacy_parse  # 被测量的对象：改造前的旧解析器
from eval.replay import silence_project_logs  # 复用日志压制


def _reason(run):
    """取一小段真实理由文本，让注入的句子读起来像真实审核话术"""
    text = (run.get("label_reason") or run.get("note") or "该笔费用不符合公司报销制度的规定")
    text = text.strip()  # 去首尾空白
    return text[:70]  # 截断，避免句子过长


# ---------------------------------------------------------------------------
# 五种措辞模板。对同一个语义结论生成 5 条不同写法的自然语言文本。
# 关键：这些句子都让「通过」出现在「不通过」之前（真值为不通过时），
#       或者让「不通过」出现在「通过」之前（真值为通过时，用来测误伤）。
# ---------------------------------------------------------------------------
def phrase_variants(run, label):
    """按金标准生成 5 种措辞，返回 [(措辞名, 文本), ...]"""
    r = _reason(run)  # 理由片段
    if label == "不通过":
        return [
            ("工具叙事式（动词通过在前）",
             "我通过调用费用标准工具逐项核对后确认，" + r + "。最终审核结论：不通过。"),
            ("合规陈述式（先肯定后否定）",
             "经核对，该笔费用的票据要素与费用标准均已通过，但" + r + "，故审核不通过。"),
            ("分点核验式（前两项通过）",
             "1）票据真实性：通过；2）费用标准校验：通过；3）事由与业务相关性：不通过。"),
            ("条款查询式（通过查询开头）",
             "通过查询《通用企业报销制度》相关条款，" + r + "，最终裁定：不通过。"),
            ("叙述式（结论在句末）",
             "本次报销材料我已逐项核验通过，但存在以下问题：" + r + "。因此该笔费用不予通过。"),
        ]
    return [
        ("工具叙事式（动词通过在前）",
         "我通过调用费用标准工具逐项核对后确认，" + r + "。最终审核结论：通过。"),
        ("合规陈述式（先否定风险后肯定）",
         "经核对，未发现不通过的情形，" + r + "，故审核通过。"),
        ("分点核验式（三项全通过）",
         "1）票据真实性：通过；2）费用标准校验：通过；3）事由与业务相关性：通过。"),
        ("条款查询式（通过查询开头）",
         "通过查询《通用企业报销制度》相关条款，" + r + "，最终裁定：通过。"),
        ("叙述式（结论在句末）",
         "本次报销材料我已逐项核验通过，" + r + "。因此该笔费用审核通过。"),
    ]


# ---------------------------------------------------------------------------
# 第六类：否定嵌套句（本轮新增）
# ---------------------------------------------------------------------------
# 为什么单列一族、而不是并进上面 5 种：
#   前 5 种测的是「结论词谁先出现」，本族测的是「否定词与结论词的嵌套结构」，
#   是中文审核话术里另一种高频且更隐蔽的写法。两者的翻车机理不同，
#   如果混在一起统计，会互相稀释，看不出各自的值多少。
def _reason_neutral(run):
    """
    否定嵌套句专用的理由片段：先把理由里可能出现的结论词抹掉。

    原因：label_reason 本身可能带「通过 / 不通过」字样，一旦混进注入句，
    旧解析器的命中位置就不再由我们注入的结构决定，这一族的测量会失真。
    所以统一替换成中性词，保证句子的结论词只来自我们控制的那一处。
    """
    r = _reason(run)  # 复用上面取理由片段的逻辑
    return r.replace("不通过", "不符合").replace("通过", "符合")  # 抹掉结论词


def negation_variants(run, label):
    """
    生成「否定嵌套句」一族措辞（每个方向 4 条）。

    真值「不通过」方向（危险：会被放行）
        「不予通过 / 无法通过 / 不能予以通过 / 不应通过」——
        句子里根本没有「不通过」三连字，旧解析器 find("不通过") = -1，
        直接返回「通过」，于是一张违规单被放行。

    真值「通过」方向（误伤）
        「不存在……不通过的情形」是双重否定，语义为肯定，
        但「不通过」出现在句子前部，旧解析器按位置判成「不通过」，
        于是一张合规单被冤枉。两个方向都要测，只测一个方向就是耍流氓。
    """
    r = _reason_neutral(run)  # 已抹掉结论词的理由片段
    if label == "不通过":  # 真值是不通过：句子只出现「通过」、不出现「不通过」
        return [
            ("否定嵌套式·不予通过",
             "经逐项核验，" + r + "，故本单不予通过。"),
            ("否定嵌套式·无法通过",
             "经复核，" + r + "，该笔费用无法通过审核。"),
            ("否定嵌套式·不能予以通过",
             "综合票据要素与报销单信息，" + r + "，本次申请不能予以通过。"),
            ("否定嵌套式·不应通过",
             "依据公司报销制度，" + r + "，因此不应通过本次审批。"),
        ]
    return [  # 真值是通过：结论词被包在否定结构里，旧解析器按位置判反
        ("否定嵌套式·不存在不通过情形",
         "经核验，不存在该笔费用审核不通过的情形，" + r + "，故准予通过。"),
        ("否定嵌套式·未发现不通过依据",
         "未发现任何足以认定本次报销不通过的依据，" + r + "，最终结论：通过。"),
        ("否定嵌套式·并非不通过",
         "该笔费用的审核结论并非不通过，" + r + "，本次报销准予通过。"),
        ("否定嵌套式·无审核不通过理由",
         "经复核，" + r + "，不存在审核不通过的理由，予以通过。"),
    ]


def measure_negation(runs):
    """
    单独统计「否定嵌套句」这一族：旧解析器判成什么样、两个方向各错多少。
    结构化方案（B / A）不读自然语言，所以本族对它们零影响 —— 这正是要展示的对照。
    """
    usable = [r for r in runs if r.get("label") in ("通过", "不通过")]  # 只算有金标准的
    per_template = {}  # 措辞名 → [判对数, 总数]
    d_correct = 0  # 旧解析器在该族上判对多少条
    d_total = 0  # 该族文本总条数
    false_pass = 0  # 错误放行：真值不通过，被解析成通过（最危险的漏检）
    false_block = 0  # 误伤：真值通过，被解析成不通过
    fail_cases = []  # 判错的用例明细，便于回查
    label_fail_total = 0  # 真值「不通过」方向的文本总条数（放行率分母）
    label_pass_total = 0  # 真值「通过」方向的文本总条数（误伤率分母）

    for run in usable:  # 逐条用例
        label = run.get("label")  # 金标准
        variants = negation_variants(run, label)  # 该用例的 4 条否定嵌套句
        if label == "不通过":  # 累计分母
            label_fail_total += len(variants)
        else:
            label_pass_total += len(variants)
        for name, text in variants:  # 逐条措辞
            pred = legacy_parse(text)  # 旧解析器怎么猜
            hit = (pred == label)  # 是否与金标准一致
            slot = per_template.setdefault(name, [0, 0])  # 该措辞的统计桶
            slot[1] += 1  # 总数 +1
            if hit:  # 判对
                slot[0] += 1
                d_correct += 1
            else:  # 判错，再分方向
                if label == "不通过" and pred == "通过":
                    false_pass += 1
                if label == "通过" and pred == "不通过":
                    false_block += 1
                fail_cases.append({
                    "case_id": run.get("case_id"),
                    "label": label,
                    "template": name,
                    "legacy_pred": pred,
                })
            d_total += 1  # 总数 +1

    def rate(a, b):
        return round(a / b, 4) if b else None

    return {
        "desc": "否定嵌套句（双重否定 / 否定动词+通过）",
        "variant_per_case": 4,  # 每个方向 4 条
        "variant_count": d_total,  # 该族文本总条数
        "D_on_negation": {  # 旧解析器在该族上的成绩
            "correct": d_correct,
            "total": d_total,
            "accuracy": rate(d_correct, d_total),
        },
        "error_directions": {
            "false_pass_count": false_pass,  # 真值不通过 → 解析成通过（最危险）
            "false_pass_rate": rate(false_pass, label_fail_total),
            "false_block_count": false_block,  # 真值通过 → 解析成不通过
            "false_block_rate": rate(false_block, label_pass_total),
        },
        "per_template": {  # 逐措辞分解
            name: {"correct": v[0], "total": v[1], "accuracy": rate(v[0], v[1])}
            for name, v in per_template.items()
        },
        "fail_cases": fail_cases,  # 判错明细
    }


def measure(runs):
    """对每条用例注入 5 种措辞，比较旧解析器与结构化方案的表现"""
    usable = [r for r in runs if r.get("label") in ("通过", "不通过")]  # 只算有金标准的
    per_template = {}  # 措辞名 → [判对数, 总数]
    d_injected_correct = 0  # D 组在注入文本上的判对数
    d_injected_total = 0  # D 组在注入文本上的总数
    b_correct = 0  # B 组（不读文本）
    a_correct = 0  # A 组（不读文本）
    false_pass = 0  # 错误放行：真值不通过，被解析成通过
    false_block = 0  # 误伤：真值通过，被解析成不通过
    detail = []  # 逐条明细，便于回查

    for run in usable:
        label = run.get("label")  # 金标准
        if group_B(run) == label:  # 结构化裁决是否判对
            b_correct += 1
        if group_A(run) == label:  # 结构化+校验是否判对
            a_correct += 1
        for name, text in phrase_variants(run, label):  # 逐个措辞
            pred = legacy_parse(text)  # 旧解析器怎么猜
            hit = (pred == label)  # 是否与金标准一致
            slot = per_template.setdefault(name, [0, 0])  # 该措辞的统计桶
            slot[1] += 1
            if hit:
                slot[0] += 1
                d_injected_correct += 1
            else:
                if label == "不通过" and pred == "通过":  # 最危险的漏检
                    false_pass += 1
                if label == "通过" and pred == "不通过":  # 误伤
                    false_block += 1
            d_injected_total += 1
            detail.append({
                "case_id": run.get("case_id"),
                "label": label,
                "template": name,
                "legacy_pred": pred,
                "correct": hit,
            })

    def rate(a, b):
        return round(a / b, 4) if b else None

    return {
        "case_count": len(usable),
        "variant_count": d_injected_total,
        "groups": {
            "D_on_injected": {
                "desc": "旧解析器（读注入后的自然语言）",
                "correct": d_injected_correct,
                "total": d_injected_total,
                "accuracy": rate(d_injected_correct, d_injected_total),
            },
            "B": {
                "desc": "结构化裁决（不读自然语言）",
                "correct": b_correct,
                "total": len(usable),
                "accuracy": rate(b_correct, len(usable)),
            },
            "A": {
                "desc": "结构化+防幻觉校验（不读自然语言）",
                "correct": a_correct,
                "total": len(usable),
                "accuracy": rate(a_correct, len(usable)),
            },
        },
        "error_directions": {
            "false_pass_count": false_pass,  # 真值不通过 → 解析成通过
            "false_pass_rate": rate(false_pass, sum(1 for r in usable if r.get("label") == "不通过") * 5),
            "false_block_count": false_block,  # 真值通过 → 解析成不通过
            "false_block_rate": rate(false_block, sum(1 for r in usable if r.get("label") == "通过") * 5),
        },
        "per_template": {
            name: {"correct": v[0], "total": v[1], "accuracy": rate(v[0], v[1])}
            for name, v in per_template.items()
        },
        "detail": detail,
    }


def print_report(report):
    """打印成一张能直接贴进报告的表格"""
    print("=" * 84)
    print("陷阱句注入测量    用例数：" + str(report["case_count"])
          + "（每条用例 5 种措辞，共 " + str(report["variant_count"]) + " 条文本）")
    print("=" * 84)
    print(f"{'组':<14}{'说明':<30}{'判对':>7}{'总数':>7}{'准确率':>10}")
    print("-" * 84)
    for key in ("D_on_injected", "B", "A"):
        g = report["groups"][key]
        acc = f"{g['accuracy']*100:.1f}%" if g["accuracy"] is not None else "-"
        print(f"{key:<14}{g['desc']:<30}{g['correct']:>7}{g['total']:>7}{acc:>10}")
    print("-" * 84)
    gd = report["groups"]["D_on_injected"]["accuracy"]
    ga = report["groups"]["A"]["accuracy"]
    if gd is not None and ga is not None:
        print(f"结构化裁决净收益 (A - D_on_injected)：{(ga - gd) * 100:.1f} 个百分点")
    print("-" * 84)
    print("按措辞分解（旧解析器 D 组）：")
    for name, v in report["per_template"].items():
        acc = f"{v['accuracy']*100:.0f}%" if v["accuracy"] is not None else "-"
        print(f"  {name:<26} 判对 {v['correct']}/{v['total']}  准确率 {acc}")
    e = report["error_directions"]
    print("-" * 84)
    print("两个方向的错误必须分开看：")
    print(f"  错误放行（真值不通过 → 被判成通过）：{e['false_pass_count']} 条，"
          f"放行率 {e['false_pass_rate']}")
    print(f"  误伤    （真值通过 → 被判成不通过）：{e['false_block_count']} 条，"
          f"误伤率 {e['false_block_rate']}")
    print("=" * 84)
    neg = report.get("negation")  # 否定嵌套句族（本轮新增）
    if neg:  # 有数据才打印这一段
        print("否定嵌套句（单独一族，共 " + str(neg["variant_count"]) + " 条文本）")
        g = neg["D_on_negation"]  # 旧解析器在该族上的成绩
        acc = f"{g['accuracy']*100:.1f}%" if g["accuracy"] is not None else "-"  # 准确率文本
        print(f"  旧解析器判对 {g['correct']}/{g['total']}  准确率 {acc}")  # 汇总行
        ne = neg["error_directions"]  # 该族两个方向的错误
        print(f"  错误放行（真值不通过 → 被判成通过）：{ne['false_pass_count']} 条，"
              f"放行率 {ne['false_pass_rate']}")  # 危险方向
        print(f"  误伤    （真值通过 → 被判成不通过）：{ne['false_block_count']} 条，"
              f"误伤率 {ne['false_block_rate']}")  # 误伤方向
        for name, v in neg["per_template"].items():  # 逐措辞分解
            a = f"{v['accuracy']*100:.0f}%" if v["accuracy"] is not None else "-"  # 该措辞准确率
            print(f"    {name:<26} 判对 {v['correct']}/{v['total']}  准确率 {a}")  # 明细行
        print("-" * 84)
    print("口径提醒：注入只改措辞、不改语义，语义仍等于金标准；")
    print("          B / A 两组读结构化字段，不受措辞影响，所以这里的差值")
    print("          就是「结构化裁决」相对「字符串位置解析」的净收益。")
    print("=" * 84)


def main():
    parser = argparse.ArgumentParser(description="陷阱句注入测量")
    parser.add_argument("--runs", default=str(Path(__file__).parent / "runs"), help="runs 目录")
    parser.add_argument("--out", default="", help="报告输出路径（JSON），留空则不落盘")
    args = parser.parse_args()

    silence_project_logs()  # 压日志
    runs_dir = Path(args.runs)  # runs 目录
    if not runs_dir.exists():
        print("找不到 runs 目录：" + str(runs_dir))
        return 1

    runs = []  # 读取 runs
    for path in sorted(runs_dir.glob("*.json")):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # 单个坏了不影响
            print("  [跳过] " + path.name + ": " + str(exc))
    if not runs:
        print("runs 目录里没有数据")
        return 1

    report = measure(runs)  # 测量（5 种基础措辞）
    report["negation"] = measure_negation(runs)  # 否定嵌套句单独一族统计
    print_report(report)  # 打印

    if args.out:  # 落盘
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告已写入：" + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
