# -*- coding: utf-8 -*-
# ============================================================================
# 审核结论校验层（utils/verdict_guard.py）
# ============================================================================
# 这个文件要回答一个评委一定会问的问题：
#   「大模型要是胡说八道，你怎么知道？」
#
# 答案不是「我们在提示词里写了不要胡说」——那是祈祷，不是工程。
# 这里的做法是：Agent 输出的每一条结论，都必须能被系统里确定性的事实核对上。
# 核对不上就不采信，并且把「为什么不采信」记录下来给人看。
#
# 具体做四类核对：
#   1) 内部一致性：结论说「通过」，但它自己列的证据里有 passed=False → 自相矛盾
#   2) 来源真实性：引用了某个工具，但这一轮根本没调用过该工具 → 幻觉
#   3) 结论覆盖率：工具明明算出「超标」，结论却是「通过」 → 最危险的一种，直接拦截
#   4) 数字可溯源：理由里出现的金额，在发票字段和工具返回值里都找不到 → 疑似编造
#
# 注意：金额一律由 audit/ 里的纯函数计算，LLM 只负责理解和解释。
# 校验层就是这条边界的执行者。
# ============================================================================

import re
# 导入正则表达式库，用来从文本里抽金额数字

from agent.trace import STEP_TOOL_RESULT
# 从trace模块导入步骤类型常量，用来识别工具返回步骤


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
ALLOWED_SOURCES = {
# 证据来源白名单：只能填这5个来源
    "expense_standard_tool",   # 费用标准校验工具
    "rag_policy_search_tool",  # 制度检索工具
    "anomaly_check",           # 风控检测
    "basic_check",             # 基础合规校验
    "invoice_field",           # 发票票面字段
}

TOOL_SOURCES = {
# 其中属于"工具"的来源：必须真的调用过才算数
    "expense_standard_tool",
    "rag_policy_search_tool",
}

MONEY_MIN = 100.0
# 只核对100元以上的数字
# 小于100的一般是天数（2天）、人数（3人），核对金额会误报

_DATE_RE = re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?")
# 编译一个正则：匹配日期格式，比如2026-09-10、2026年9月10日
# 先把日期抠掉，避免日期里的数字被当成金额
_LONG_DIGITS_RE = re.compile(r"\d{12,}")
# 匹配12位以上的连续数字（发票号码是20位的）
# 抠掉避免被当成金额
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 匹配普通数字：可以带千分位逗号和小数点
# 比如：2584、2,584.00、100


# ---------------------------------------------------------------------------
# 小工具函数
# ---------------------------------------------------------------------------
def _flatten_text(value):
    # 把嵌套结构（字典/列表）递归拍平成一段纯文本
    # 目的：方便后面用正则抽数字
    """把字典/列表/字符串递归拍平成一段文本，方便统一抽数字。"""
    if value is None:
        return ""  # 空值返回空字符串
    if isinstance(value, dict):
    # 是字典：递归处理每个值（只取值，不取键名）
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
    # 是列表/元组/集合：递归处理每个元素
        return " ".join(_flatten_text(v) for v in value)
    return str(value)  # 其它类型直接转字符串


def numbers_in(text):
    # 从一段文本里抽出所有100元以上的数字
    """抽出一段文本里 100 元以上的数字（先抠掉日期和长串单号，避免误判）。"""
    if not text:
        return set()  # 空文本返回空集合
    cleaned = _DATE_RE.sub(" ", str(text))
    # 第一步：把日期替换成空格（抠掉日期）
    cleaned = _LONG_DIGITS_RE.sub(" ", cleaned)
    # 第二步：把长串数字（发票号）替换成空格
    result = set()  # 用集合存结果，自动去重
    for raw in _NUMBER_RE.findall(cleaned):
    # 找出所有匹配到的数字
        try:
            value = float(raw.replace(",", ""))
            # 去掉千分位逗号，转成浮点数
        except ValueError:
            continue  # 转不了就跳过
        if value >= MONEY_MIN:
        # 只收100元以上的
            result.add(round(value, 2))
            # 保留两位小数，加进集合
    return result


def _is_standard_missing(result):
    # 判断费用标准工具的返回值是不是"没配标准"
    # 区分两种情况：
    #   情况A：真的超标了（标准200，实际2584）→ 该判不通过
    #   情况B：系统里没配这个职级的标准 → 不能怪员工，不能判不通过
    """区分「真的超标」和「系统里没配这个职级的标准」——后者不该判员工不通过。"""
    standard = result.get("standard")
    message = str(result.get("message") or "")
    # needs_human=True 的情况（城市为空/认不出/职级找不到/费用类型无标准）
    if result.get("needs_human"):
        return True
    return standard in (0, "0") or "未找到职级" in message or "需人工确认" in message


def _tool_violations(trace):
    # 从轨迹里挑出"工具明确判定不合规"的结果
    # 用于校验5：工具说超标了，结论就不能写通过
    """从轨迹里挑出费用标准工具明确判「不符合」的返回值（排除标准缺失的情况）。"""
    violations = []
    for item in trace.results_of("expense_standard_tool"):
    # 遍历所有费用标准工具的返回值
        if item.get("passed") is False and not _is_standard_missing(item):
        # passed=False（明确不通过）且不是因为没配标准
            violations.append(item)  # 记下来
    return violations


def _check(name, level, detail):
    # 构造一条校验明细（小工具函数，避免重复写字典）
    """构造一条校验明细，供界面渲染成清单。level 取值：pass / warn / error。"""
    return {"name": name, "level": level, "detail": detail}
    # name：校验项名称
    # level：pass（通过）/ warn（警告）/ error（错误）
    # detail：具体说明


# ---------------------------------------------------------------------------
# 主函数：核对一份裁决
# ---------------------------------------------------------------------------
def verify_verdict(verdict, trace=None, invoice_data=None, anomaly_result=None):
    # 主函数：核对一份AI裁决，返回校验报告
    # 这是整个"防幻觉"层的入口
    """
    核对一份结构化裁决，返回校验报告。

    参数：
        verdict:        AuditVerdict 字典（由 schema 约束生成，字段确定）
        trace:          AgentTrace，本轮的工具调用轨迹；没有就当「无证据」处理
        invoice_data:   发票识别结果字典（用于数字溯源）
        anomaly_result: 风控检测结果（可选）

    返回：
        {
            "ok":              是否通过全部硬校验（False 表示存在 error）
            "errors":          error 级问题清单（会被拦截）
            "warnings":        warning 级问题清单（记录但不拦截）
            "checks":          逐项校验明细，供界面展示
            "original_verdict":Agent 原本给的结论
            "final_verdict":   校验后实际采用的结论
            "overridden":      结论是否被校验层改写
            "needs_human":     是否建议转人工
        }
    """
    verdict = verdict or {}
    # 兜底：如果verdict是None，当空字典处理
    checks = []   # 逐项校验明细（给界面展示用）
    errors = []   # error级问题（会拦截，强制改结论）
    warnings = [] # warning级问题（记录但不拦截）

    original = verdict.get("verdict")
    # AI原本给的结论（通过/不通过）
    evidence = verdict.get("evidence") or []
    # 证据清单
    reasons = verdict.get("reasons") or []
    # 理由清单
    citations = verdict.get("citations") or []
    # 制度引用清单

    # ---------------- 校验1：结论取值合法 ----------------
    if original in ("通过", "不通过"):
    # 是这两个值之一，合法
        checks.append(_check("结论取值合法", "pass", f"结论为「{original}」"))
    else:
    # 不是，非法（理论上schema已经挡住了，这里是双保险）
        errors.append(f"结论取值非法：{original!r}，只能是「通过」或「不通过」")
        checks.append(_check("结论取值合法", "error", f"结论取值 {original!r} 不在允许范围内"))

    # ---------------- 校验2：证据来源在白名单内 ----------------
    bad_sources = [e.get("source") for e in evidence
                   if e.get("source") not in ALLOWED_SOURCES]
    # 找出不在白名单里的来源
    if bad_sources:
        errors.append("证据里出现了未知来源：" + "、".join(str(s) for s in bad_sources))
        checks.append(_check("证据来源合法", "error", "未知来源：" + "、".join(str(s) for s in bad_sources)))
    else:
        checks.append(_check("证据来源合法", "pass", f"{len(evidence)} 条证据来源均在白名单内"))

    # ---------------- 校验3：引用的工具必须真的被调用过 ----------------
    called = trace.tool_names if trace is not None else set()
    # 本轮实际调用过的工具集合
    ghost = [e.get("source") for e in evidence
             if e.get("source") in TOOL_SOURCES and e.get("source") not in called]
    # 证据里引用了某个工具，但这个工具本轮根本没调用过
    if ghost:
        errors.append("引用了本轮并未实际调用的工具：" + "、".join(sorted(set(ghost))))
        checks.append(_check("工具调用可溯源", "error", "证据引用了未调用的工具：" + "、".join(sorted(set(ghost)))))
    else:
        checks.append(_check("工具调用可溯源", "pass", "证据引用的工具都在本轮调用记录中"))

    # ---------------- 校验4：结论与自身证据不矛盾 ----------------
    failed_evidence = [e for e in evidence if e.get("passed") is False]
    # 找出证据里passed=False的（不支持报销的）
    if original == "通过" and failed_evidence:
    # 结论说通过，但证据里有不通过的 → 矛盾
        detail = failed_evidence[0].get("fact") or ""
        errors.append(f"结论为「通过」，但证据中存在不支持项：{detail}")
        checks.append(_check("结论与证据自洽", "error", f"结论「通过」与证据矛盾：{detail}"))
    else:
        checks.append(_check("结论与证据自洽", "pass", "结论方向与证据一致"))

    # ---------------- 校验5：工具判定的不通过项必须被结论吸收（最关键） ----------------
    violations = _tool_violations(trace) if trace is not None else []
    # 找出工具明确判不合规的结果
    if violations and original == "通过":
    # 工具说不合规，结论却说通过 → 最危险的情况，直接拦截
        detail = str(violations[0].get("message") or violations[0])
        errors.append(f"费用标准工具已判定不合规，结论却为「通过」：{detail}")
        checks.append(_check("结论未遗漏工具判定", "error", f"工具判定不合规但结论为通过：{detail}"))
    elif violations:
    # 工具判不合规，结论也是不通过 → 正确
        checks.append(_check("结论未遗漏工具判定", "pass", f"工具判定的 {len(violations)} 项不合规已体现在结论中"))
    else:
    # 工具没判不合规
        checks.append(_check("结论未遗漏工具判定", "pass", "本轮没有工具判定不合规的项"))

    # ---------------- 校验6：风控高危不得放行 ----------------
    high_risks = []
    if anomaly_result:
        high_risks = [i for i in (anomaly_result.get("risk_items") or []) if i.get("level") == "high"]
        # 挑出高危风险项
    if high_risks and original == "通过":
    # 有高危风险，结论却说通过 → 拦截
        detail = "、".join(str(i.get("type")) for i in high_risks)
        errors.append(f"风控检测存在高风险项，结论却为「通过」：{detail}")
        checks.append(_check("风控高危已被吸收", "error", f"高风险项未拦截：{detail}"))
    else:
        checks.append(_check("风控高危已被吸收", "pass", f"高风险项 {len(high_risks)} 条，结论「{original}」"))

    # ---------------- 校验7：理由里的金额必须可溯源 ----------------
    allowed = set()
    allowed |= numbers_in(_flatten_text(invoice_data))
    # 发票票面里的所有数字
    if trace is not None:
        allowed |= numbers_in(_flatten_text([s.tool_result for s in trace.tool_results]))
        # 工具返回值里的所有数字
    allowed |= numbers_in(_flatten_text(anomaly_result))
    # 风控结果里的所有数字
    claimed = numbers_in(" ".join(str(r) for r in reasons))
    claimed |= numbers_in(" ".join(str(e.get("fact") or "") for e in evidence))
    # AI理由和证据里声称的所有数字
    unsourced = sorted(claimed - allowed)
    # claimed - allowed = 找不到出处的数字（疑似编造）
    if unsourced:
        text = "、".join(f"{n:g}" for n in unsourced)
        warnings.append(f"理由中出现无法溯源的金额：{text}（发票与工具返回值里都找不到）")
        checks.append(_check("金额可溯源", "warn", f"无法溯源的金额：{text}"))
    else:
        checks.append(_check("金额可溯源", "pass", f"核对 {len(claimed)} 个金额，全部有出处"))

    # ---------------- 校验8：制度引用必须来自检索结果 ----------------
    if citations and trace is not None:
        policy_text = _flatten_text([r.get("policy_text", "") for r in trace.results_of("rag_policy_search_tool")])
        # 把RAG检索到的制度原文拍平成一段文本
        if not policy_text:
        # 根本没调检索工具却在引用条款
            warnings.append("结论引用了制度条款，但本轮没有调用制度检索工具")
            checks.append(_check("条款引用可溯源", "warn", "引用了条款但未调用检索工具"))
        else:
            missing = []
            for c in citations:
                key = str(c).strip()
                hit = key[:12] in policy_text if key else False
                # 取引用的前12个字，看能不能在检索原文里找到
                if not hit:
                    missing.append(key)
            if missing:
                warnings.append("部分条款引用无法在检索结果中定位：" + "、".join(m[:20] for m in missing))
                checks.append(_check("条款引用可溯源", "warn", f"{len(missing)} 条引用未命中检索原文"))
            else:
                checks.append(_check("条款引用可溯源", "pass", f"{len(citations)} 条引用均命中检索原文"))
    else:
        checks.append(_check("条款引用可溯源", "pass", "本轮没有引用制度条款"))

    # ---------------- 校验9：通过类结论必须有证据支撑 ----------------
    if original == "通过" and not evidence:
    # 说通过，但一条证据都没有
        warnings.append("结论为「通过」但未给出任何证据，建议人工复核")
        checks.append(_check("通过需有证据", "warn", "结论「通过」但证据清单为空"))
    else:
        checks.append(_check("通过需有证据", "pass", f"证据 {len(evidence)} 条"))

    # ---------------- 校验9.1：判"不通过"必须有制度条款依据（条款门禁） ----------------
    if original == "不通过" and not citations:
        errors.append("结论为「不通过」但未引用任何制度条款，无依据不得拒报销，转人工复核")
        checks.append(_check("条款门禁（不通过需依据）", "error", "判不通过但citations为空，无制度依据"))
    elif original == "不通过" and citations:
        # 有引用，检查是否命中RAG原文
        if trace is not None:
            policy_text = _flatten_text([r.get("policy_text", "") for r in trace.results_of("rag_policy_search_tool")])
            if not policy_text:
                errors.append("结论为「不通过」引用了制度条款，但本轮未调用制度检索工具，依据无法核实")
                checks.append(_check("条款门禁（不通过需依据）", "error", "引用了条款但未检索制度原文"))
            else:
                checks.append(_check("条款门禁（不通过需依据）", "pass", f"判不通过，引用了{len(citations)}条制度条款"))
        else:
            checks.append(_check("条款门禁（不通过需依据）", "warn", "无轨迹记录，无法核实条款"))
    else:
        checks.append(_check("条款门禁（不通过需依据）", "pass", "本轮未判不通过"))

    # ---------------- 校验9.2：判"通过"必须有工具计算支撑 ----------------
    if original == "通过":
        tool_passed = False
        if trace is not None:
            for item in trace.results_of("expense_standard_tool"):
                if item.get("passed") is True:
                    tool_passed = True
                    break
        if not tool_passed:
            errors.append("结论为「通过」但费用标准工具未明确判定通过，无计算依据，转人工复核")
            checks.append(_check("条款门禁（通过需计算）", "error", "判通过但工具未确认通过"))
        else:
            checks.append(_check("条款门禁（通过需计算）", "pass", "费用标准工具已确认通过"))
    else:
        checks.append(_check("条款门禁（通过需计算）", "pass", "本轮未判通过"))

    # ---------------- 校验9.5：工具要求人工确认时，裁决必须标记needs_human ----------------
    tool_needs_human = False
    if trace is not None:
        for item in trace.results_of("expense_standard_tool"):
            if item.get("needs_human"):
                tool_needs_human = True
                break
    if tool_needs_human and not verdict.get("needs_human"):
        warnings.append("费用标准工具要求人工确认（城市/职级/标准缺失），但裁决未标记needs_human")
        checks.append(_check("人工确认已吸收", "warn", "工具要求人工确认，但裁决未标记"))
    elif tool_needs_human:
        checks.append(_check("人工确认已吸收", "pass", "工具要求人工确认，裁决已标记needs_human"))
    else:
        checks.append(_check("人工确认已吸收", "pass", "本轮无需要人工确认的项"))

    # ---------------- 校验10：置信度与转人工 ----------------
    confidence = verdict.get("confidence")
    try:
        confidence = float(confidence)  # 转成浮点数
    except (TypeError, ValueError):
        confidence = 0.5  # 转不了就用中性值
    if confidence > 1:
        confidence = confidence / 100.0
        # 模型可能把0.9写成90，除以100归一到0-1
    if confidence < 0.6:
    # 置信度低于0.6，建议人工看一下
        warnings.append(f"模型自评置信度偏低（{confidence:.2f}），建议人工复核")
        checks.append(_check("置信度水平", "warn", f"置信度 {confidence:.2f} 偏低"))
    else:
        checks.append(_check("置信度水平", "pass", f"置信度 {confidence:.2f}"))

    # ---------------- 汇总 ----------------
    overridden = bool(errors) and original == "通过"
    # 只有"原本说通过但被拦下"才算改写结论
    final_verdict = "不通过" if errors else (original or "不通过")
    # 有error就强制改成不通过（取最保守结论）
    needs_human = bool(verdict.get("needs_human")) or bool(errors) or bool(warnings)
    # 有任何异常都建议人工复核

    return {
        "ok": not errors,
        # 没有error才算校验通过
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "original_verdict": original,
        # AI原始结论
        "final_verdict": final_verdict,
        # 校验后实际采用的结论
        "overridden": overridden,
        # 结论是否被改写
        "needs_human": needs_human,
        "confidence": round(confidence, 3),
        # 归一后的置信度，保留三位小数
    }
