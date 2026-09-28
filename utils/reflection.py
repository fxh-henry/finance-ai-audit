# -*- coding: utf-8 -*-
# ============================================================================
# 反思机制（utils/reflection.py）
# ============================================================================
# 要回答的问题：「Agent 一条道走到黑，第一轮判断错了怎么办？」
#
# 答案不是「在提示词里写一句请反思」——那是祈祷，不是机制。
# 这里把反思拆成三步可控的东西：
#   第1步 分流：读校验结果，挑出「可澄清的矛盾」。不重复实现判据，
#               直接消费校验层的 checks，只补两条 verdict_guard 里没有的判据
#   第2步 指令：把矛盾翻译成给 Agent 的第二轮指令（要求追加检索、补齐参数）
#   第3步 收敛：第二轮结论要复检，且「没有新证据就不许放宽结论」
#
# 与 utils/verdict_guard.py 的关系（不是上下两层，是同一份校验的两个出口）：
#   verdict_guard = 校验 + 出口B：事后处置（结论已定，对不上就改写）
#   reflection    = 校验 + 出口A：事中处置（结论还没落槌，退回给 Agent 再查一次）
#   判据只有一份，避免两处口径漂移。
#
# 执行次数：只给一次机会。反思轮之后无论结果如何都不再反思，避免反复循环烧 token。
#
# 顺序：校验 → 分流 → 反思 → 再校验 → 裁决。
#   为什么反思必须在守门「落槌」之前：守门一旦改写结论就是最终裁决，
#   再反思等于去推翻最终裁决，逻辑上说不通。
# ============================================================================

from agent.trace import STEP_TOOL_CALL, STEP_TOOL_RESULT
# 从轨迹模块导入步骤类型常量，避免手写字符串拼错

from utils.verdict_guard import _is_standard_missing
# 直接复用守门模块的判据口径：同一件事（「真超标」还是「系统没配标准」）
# 只能有一份实现，否则两边各自演进就会漂移（实测已出现过判定相反的样本）


# ---------------------------------------------------------------------------
# 常量1：可以触发反思的校验项（白名单）
# ---------------------------------------------------------------------------
# 判据：这个问题「再查一次制度 / 补一个参数」有可能解决。
# 不在这个名单里的问题（编造证据、编造工具调用、风控高危）不给第二次机会。
CHECK_MISSED_TOOL = "结论未遗漏工具判定"
# 来自 verdict_guard 校验5：工具判超标，结论却写通过 → 可能漏了豁免规定
CHECK_CITATION = "条款引用可溯源"
# 来自 verdict_guard 校验8：引用没命中检索原文 → 可能是检索词构造得不好
CHECK_EVIDENCE = "通过需有证据"
# 来自 verdict_guard 校验9：结论通过但没有证据 → 让它去检索
CHECK_CONFIDENCE = "置信度水平"
# 来自 verdict_guard 校验10：模型自评置信度低 → 让它再找一轮依据
CHECK_PARAM_FALLBACK = "参数口径可确认"
# 本文件新增：关键参数没传，工具按默认口径照算 → 补齐参数重算就能修
CHECK_TOOL_CONFLICT = "工具判定与制度一致性"
# 本文件新增：费用标准说超标，但制度原文里有例外表述 → 去查清适用哪种处置

CLARIFIABLE_CHECKS = {
    # 上面这些校验项命中时，才触发反思
    CHECK_MISSED_TOOL,
    CHECK_CITATION,
    CHECK_EVIDENCE,
    CHECK_CONFIDENCE,
    CHECK_PARAM_FALLBACK,
    CHECK_TOOL_CONFLICT,
}


# ---------------------------------------------------------------------------
# 常量2：制度里的「例外 / 豁免」表述
# ---------------------------------------------------------------------------
EXEMPTION_KEYWORDS = (
    # 这些词表示制度对该情形留了口子，也就是「超标不等于一定不通过」
    "提前审批",    # 例如：特殊情况需提前审批
    "特殊情况",    # 例如：特殊情况可实报实销
    "实报实销",    # 例如：总经理及以上实报实销
    "由个人承担",   # 例如：超标部分由个人承担（部分通过，不是全否）
    "可报销",      # 例如：经批准后可报销
    "经批准",      # 同上
    "另行审批",    # 例如：超标需另行审批
    "报总经理",    # 例如：超权限报总经理审批
    "酌情",        # 例如：酌情处理
)


# ---------------------------------------------------------------------------
# 常量3：各费用类型「必须传参才算口径确定」的字段
# ---------------------------------------------------------------------------
REQUIRED_ARGS = {
    # 键是费用类型，值是必须提供的参数名
    "住宿费": ("city",),
    # 住宿费必须知道城市才能定档；不传城市，工具会按「其他城市」算，口径就错了
}
# 这就是那条真实缺陷的正解：参数缺失时不是「照算」，而是「口径无法确认」

# ---------------------------------------------------------------------------
# 小工具函数
# ---------------------------------------------------------------------------
def _standard_missing(result):
    """
    区分「真超标」和「系统没配标准」——后者不该算作工具判定不合规。

    实现上刻意改为直接委托给 utils/verdict_guard._is_standard_missing：
    原先这里独立写了一份等价判断，但两边口径已经漂移（needs_human 分支缺失），
    同一个返回值会出现「守门说是标准缺失、反思说不是」的矛盾。
    判据只能有一份来源，这里保留函数名只是为了不改动调用方。
    """
    return _is_standard_missing(result)  # 委托守门模块的唯一实现


def _pairs(trace, tool_name):
    # 把某个工具的「调用参数」和「返回结果」按顺序配成对
    # 为什么必须配对：判断「参数没传」要同时看调用方传了什么、工具返回了什么
    """按时间顺序把某工具的 tool_call 与 tool_result 配对，返回 [(入参, 返回值), ...]。"""
    pairs = []
    # 存放配对结果
    pending = []
    # 存放「已发起但还没返回」的调用参数
    if trace is None:
        # 没有轨迹就直接返回空列表
        return pairs
    for step in trace.steps:
        # 按顺序遍历每一步
        if step.kind == STEP_TOOL_CALL and step.tool_name == tool_name:
            # 遇到该工具的调用步骤，把入参压进待配对队列
            pending.append(step.tool_args)
        elif step.kind == STEP_TOOL_RESULT and step.tool_name == tool_name:
            # 遇到该工具的返回步骤，从队列头取一个入参配对
            args = pending.pop(0) if pending else {}
            # 队列空说明轨迹不完整，用空字典兜底
            pairs.append((args, step.tool_result))
    return pairs


def _violations_of(trace):
    # 从轨迹里挑出「费用标准工具明确判定不合规」的返回值
    """挑出费用标准工具判 passed=False 且不是「没配标准」的返回值。"""
    if trace is None:
        # 没有轨迹就没有违规项
        return []
    return [
        item for item in trace.results_of("expense_standard_tool")
        # 遍历该工具的所有返回值
        if item.get("passed") is False and not _standard_missing(item)
        # passed=False 表示明确判不合规；排除「没配标准」造成的假不合规
    ]


def _policy_text_of(trace):
    # 把本轮检索到的制度原文拼成一段文本
    """把本轮 RAG 检索返回的制度原文拼成一段文本，供关键词匹配。"""
    if trace is None:
        # 没有轨迹就没有制度文本
        return ""
    return " ".join(
        str(r.get("policy_text") or "")
        # 取每次检索返回的原文
        for r in trace.results_of("rag_policy_search_tool")
        # 遍历该工具的所有返回值
    )


# ---------------------------------------------------------------------------
# 补充判据1：参数口径可确认（对应「静默降级」这类缺陷）
# ---------------------------------------------------------------------------
def _check_param_fallback(trace):
    # 检查「调用费用标准工具时缺关键参数，工具却照样给出结果」
    # 这是最阴的一类缺陷：不报错、用默认值继续算、结论口径错了但没人察觉
    """判断「调用费用标准工具时缺关键参数，工具按默认口径照算」的情况。"""
    checks = []
    # 结果清单
    for args, result in _pairs(trace, "expense_standard_tool"):
        # 遍历该工具的每一次「入参 + 返回」
        expense_type = str(args.get("expense_type") or "")
        # 取本次调用的费用类型
        missing = [k for k in REQUIRED_ARGS.get(expense_type, ()) if not args.get(k)]
        # 找出该费用类型下「必须传但没传」的参数
        if not missing:
            # 参数齐全，这项没问题
            continue
        source = result.get("city_tier_source") or "默认口径"
        # 优先用工具自己声明的口径来源，没有就用中性说法
        checks.append({
            "name": CHECK_PARAM_FALLBACK,
            # 校验项名称
            "level": "error",
            # 错误级：口径错了，结论就没有意义
            "detail": (
                f"调用费用标准工具时未提供「{'、'.join(missing)}」，"
                f"工具按「{source}」继续核算，本次计算的口径无法确认是否正确"
            ),
            # 明细说明
        })
    return checks


# ---------------------------------------------------------------------------
# 补充判据2：工具判定与制度一致性
# ---------------------------------------------------------------------------
def _check_tool_conflict(trace):
    # 检查「费用标准说超标，但检索到的制度原文里写着可审批 / 由个人承担」
    # 说明「超标」可能不是终点，还有第二种处置方式没被考虑
    """判断「费用标准判定超标，但制度原文含例外表述」的情况。"""
    checks = []
    # 结果清单
    if not _violations_of(trace):
        # 没有超标判定，就不存在这种矛盾
        return checks
    policy_text = _policy_text_of(trace)
    # 本轮检索到的制度原文
    if not policy_text:
        # 本轮根本没检索制度，无法判断是否存在例外
        return checks
    hits = [kw for kw in EXEMPTION_KEYWORDS if kw in policy_text]
    # 找出制度原文里出现过的例外表述
    if not hits:
        # 制度没写例外，超标就是超标
        return checks
    checks.append({
        "name": CHECK_TOOL_CONFLICT,
        # 校验项名称
        "level": "warn",
        # 警告级：不是一定错，是可能漏了
        "detail": (
            f"费用标准工具判定超标，但检索到的制度里出现例外表述"
            f"（{'、'.join(hits[:4])}），可能存在「审批后可报」「超标部分个人承担」"
            f"等处置方式没有被纳入本次判断"
        ),
        # 明细说明
    })
    return checks


def extra_checks(trace):
    # 反思模块补充的判据（verdict_guard 里没有的那两条）
    # 返回结构刻意与 verdict_guard 的 checks 保持一致：{name, level, detail}
    # 这样上层可以把两份清单合并渲染，不需要区分来源
    """返回反思模块补充的判据，结构同 verdict_guard 的 checks。"""
    return _check_param_fallback(trace) + _check_tool_conflict(trace)


def collect_checks(report, trace):
    # 把「守门层已有校验项」和「反思层新增校验项」合成一份完整清单
    """合并校验层的 checks 与反思模块补充的判据，返回完整清单。"""
    checks = list((report or {}).get("checks") or [])
    # 复制守门层的校验清单
    checks += extra_checks(trace)
    # 追加反思模块补充的判据
    return checks


# ---------------------------------------------------------------------------
# 主函数1：判断要不要反思
# ---------------------------------------------------------------------------
def should_reflect(report, trace):
    # 反思的触发条件：命中「可澄清白名单」里的校验项
    # 不是所有问题都值得反思——编造证据、风控高危这类，反思解决不了
    """
    判断本次是否应该触发反思轮。

    返回：
        (是否需要反思, 命中的校验项列表)
    """
    hits = [
        c for c in collect_checks(report, trace)
        # 遍历完整校验清单
        if c.get("name") in CLARIFIABLE_CHECKS and c.get("level") in ("error", "warn")
        # 只挑白名单里的项，且必须是真的命中（error / warn）
    ]
    return (bool(hits), hits)


def summarize_checks(checks):
    # 把校验清单压成一行短文本，给日志和界面用
    # 每条说明截断到 40 字，避免把界面撑爆
    """把校验清单压成一行短文本，便于日志打印和界面展示。"""
    return "；".join(
        f"{c.get('name')}（{str(c.get('detail') or '')[:40]}）" for c in checks
    )

# ---------------------------------------------------------------------------
# 主函数2：把矛盾翻译成给 Agent 的第二轮指令
# ---------------------------------------------------------------------------
def build_reflection_prompt(checks, verdict=None):
    # 关键在第4条：反思的出口不只是「改判」，还包括「承认判不了」
    # 没有这个出口，模型会为了凑一个结论去编理由
    """
    构造反思轮的指令文本。

    参数：
        checks: should_reflect 返回的命中校验项
        verdict: 第一轮的结构化裁决（用于告诉 Agent 它上一轮说了什么）
    """
    lines = []
    # 逐行拼装指令
    lines.append("【系统核对发现你的结论存在未解决的矛盾，请先核查再下结论】")
    lines.append("")
    # 空行，让指令更好读
    for c in checks:
        # 逐条列出矛盾点
        lines.append(f"- {c.get('name')}：{c.get('detail')}")
    lines.append("")
    lines.append("请按以下顺序处理：")
    lines.append("1. 先调用【RAG制度检索工具】，查找该情形是否有特殊规定"
                 "（例如超标后可否审批报销、超标部分是否由个人承担）")
    # 第1条：追加检索，这就是「自主追加检索」这一步的落点
    lines.append("2. 如果需要重新核算，请重新调用【费用标准校验工具】，"
                 "把必要参数（如城市、职级、天数）补齐后再算，不要用默认值代替")
    # 第2条：把参数补全，直接针对静默降级
    lines.append("3. 如果确实查到了新的制度依据，请说明依据并修正你的结论")
    # 第3条：允许改判，但必须挂依据
    lines.append("4. 如果没有查到可用依据，请直接给出「不通过」，"
                 "并在建议里写明需要人工复核；不要勉强给出通过结论")
    # 第4条：给反思一个「认输」的出口，防止为了凑结论而编理由
    lines.append("")
    lines.append("注意：不得编造制度条款；不得为了得出某个结论而忽略上述矛盾。")
    if verdict:
        # 如果拿到了第一轮裁决，就把它复述给 Agent，让反思有明确对象
        reasons = "；".join(verdict.get("reasons") or []) or "（未给出理由）"
        lines.append("")
        lines.append(f"你上一轮的结论是「{verdict.get('verdict')}」，理由：{reasons}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主函数3：收敛约束（第二轮结论能不能替换第一轮）
# ---------------------------------------------------------------------------
def _tool_call_names(trace):
    # 取出所有被调用过的工具名（保留重复次数）
    """取出轨迹里所有工具名，保留重复次数。"""
    if trace is None:
        # 没有轨迹返回空列表
        return []
    return [s.tool_name for s in trace.steps if s.kind == STEP_TOOL_CALL]
    # 只取工具调用步骤的工具名


def _new_tool_calls(trace_first, trace_second):
    # 比较两轮，找出第二轮「新增」的工具调用
    """返回第二轮相对第一轮新增的工具调用次数，形如 {"rag_policy_search_tool": 1}。"""
    before = _tool_call_names(trace_first)
    # 第一轮的调用记录
    after = _tool_call_names(trace_second)
    # 第二轮的调用记录
    added = {}
    # 统计结果
    for name in set(after):
        # 遍历第二轮出现过的工具名
        diff = after.count(name) - before.count(name)
        # 第二轮比第一轮多调了几次
        if diff > 0:
            # 只记录调用次数变多了的
            added[name] = diff
    return added


def reconcile(verdict_first, verdict_second, trace_first, trace_second):
    # 收敛约束：第二轮结论能不能替换第一轮
    # 核心规则：结论可以因为「新证据」改变，但不能因为「重新生成」改变
    # 没有这条规则，反思就变成「给 Agent 一次翻案机会」，放宽的口子会越开越大
    """
    决定采用第一轮还是第二轮的裁决。

    参数：
        verdict_first:  第一轮结构化裁决
        verdict_second: 反思轮结构化裁决
        trace_first:    第一轮轨迹
        trace_second:   反思轮轨迹

    返回：
        (采用的裁决, 说明文字, 是否采用了第二轮)
    """
    v1 = verdict_first or {}
    # 第一轮裁决，空值兜底
    v2 = verdict_second or {}
    # 第二轮裁决，空值兜底

    if not v2.get("verdict"):
        # 反思轮没有抽取出有效结论（例如解析失败）→ 一律维持第一轮
        return v1, "反思轮未产出有效结论，维持第一轮结果", False

    old = v1.get("verdict")
    # 第一轮结论
    new = v2.get("verdict")
    # 第二轮结论

    if old == new:
        # 结论没变，但第二轮的证据/理由可能更完整，采用第二轮
        return v2, "结论未变化，采用补充依据后的版本", True

    if old == "通过" and new == "不通过":
        # 结论收紧：允许，因为收紧方向是安全的
        return v2, "反思轮发现新问题，结论收紧", True

    if old == "不通过" and new == "通过":
        # 结论放宽：必须拿出新证据，否则判定为「重新生成导致的改变」
        added_calls = _new_tool_calls(trace_first, trace_second)
        # 第二轮新增的工具调用
        added_clauses = set(v2.get("citations") or []) - set(v1.get("citations") or [])
        # 第二轮新增引用的条款
        if not added_calls or not added_clauses:
            # 两个条件必须同时满足：既真的追加了检索，又引到了新条款
            return v1, (
                "反思轮放宽了结论，但没有新增工具调用或新增条款依据，"
                "判定为「重新生成导致的改变」而非「新证据导致的改变」，维持第一轮结果"
            ), False
        return v2, (
            f"反思轮基于新增依据放宽结论"
            f"（新增工具调用：{'、'.join(added_calls)}；新增条款 {len(added_clauses)} 条）"
        ), True

    return v2, "结论已按反思轮结果更新", True