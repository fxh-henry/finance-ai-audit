# -*- coding: utf-8 -*-
"""
审核结论解析工具（utils/audit_verdict.py）
================================================================
这个文件解决什么问题：
    「基础校验通过」不等于「最终审核通过」。
    基础校验通过后还会再进 Agent 深度审核，Agent 依然可能判「不通过」。
    所以最终结论必须以 Agent 的判定为准；而且上传页（存库）和详情页（展示）
    必须用同一套判断标准，否则两处结论会对不上。

结论从哪里来（按可靠性排序）：
    1. 结构化裁决 → 直接读 verdict 字段（主路径）
       Agent 的结论被 Pydantic schema 约束过，verdict 只可能是「通过」或「不通过」，
       不存在解析歧义。
    2. 自然语言回答 → 字面解析（兜底路径）
       只用于兼容改造前写入的历史数据。字面解析天生不精确，反例见下。

为什么必须做结构化（实测反例，说明兜底解析为什么会出错）：
    输入文本：「我通过调用费用标准工具核对后确认，该笔住宿费超标，审核不通过。」
    改造前的实现输出「通过」——因为它在文本里先找到了裸的「通过」两个字，
    而「不通过」里也含「通过」，靠位置比较就判反了。一张超标发票被放行。
================================================================
"""

# 两个合法的结论取值，全部逻辑都围绕它们展开
VERDICT_PASS = "通过"
VERDICT_FAIL = "不通过"
VALID_VERDICTS = (VERDICT_PASS, VERDICT_FAIL)


def _as_dict(value):
    """
    把任意形态的输入统一转成 dict，转不了返回空字典。

    为什么需要：结构化裁决在不同环节的形态不一样——
    抽取出来时是 Pydantic 对象，存进 session_state 或数据库后变成普通 dict，
    从数据库读出来有时还是 JSON 字符串。这里统一处理。
    """
    if value is None:  # 空值
        return {}  # 返回空字典
    if isinstance(value, dict):  # 本来就是字典
        return value  # 直接返回
    if hasattr(value, "model_dump"):  # Pydantic v2 对象
        try:  # 转字典可能失败（字段校验异常等）
            return value.model_dump()  # 转成字典
        except Exception:  # 转换失败
            return {}  # 当空处理
    if isinstance(value, str):  # 字符串：可能是一段 JSON
        import json  # 局部导入，避免模块级依赖
        try:  # 尝试解析
            parsed = json.loads(value)  # 解析
            return parsed if isinstance(parsed, dict) else {}  # 是字典才要
        except Exception:  # 不是 JSON
            return {}  # 当空处理
    return {}  # 其它类型一律当空


def structured_verdict_of(source):
    """
    从各种形态的输入里取出「结构化裁决」字典。

    能识别三层包装：
        AuditVerdict 对象           → 直接转字典
        {"verdict": "不通过", ...}  → 本身就是裁决
        {"structured": {...}}       → 领域结果里嵌了一层
        {"verdict": {"verdict": ...}} → 终审结果里键名也叫 verdict（嵌套一层）
    取不到时返回空字典。
    """
    data = _as_dict(source)  # 先统一成字典
    if not data:  # 空输入
        return {}  # 返回空

    # 第1层：本身就是一份裁决（verdict 字段已经是合法枚举值）
    if data.get("verdict") in VALID_VERDICTS:  # 直接命中
        return data  # 返回

    # 第2层：嵌在 "structured" 键里
    inner = data.get("structured")  # 取出内层
    inner_dict = _as_dict(inner)  # 统一成字典
    if inner_dict.get("verdict") in VALID_VERDICTS:  # 内层是合法裁决
        return inner_dict  # 返回内层

    # 第3层：键名也叫 verdict，但值是嵌套对象（终审返回结构的形态）
    nested = _as_dict(data.get("verdict"))  # 把 verdict 当作嵌套结构再取一次
    if nested.get("verdict") in VALID_VERDICTS:  # 内层是合法裁决
        return nested  # 返回内层

    return {}  # 三层都没命中，说明拿不到结构化裁决


def _last_index_of(text, words):
    """返回一组词在文本中最后出现的位置；都没出现返回 -1。"""
    positions = [text.rfind(w) for w in words]  # 逐个词找最后一次出现的位置
    positions = [p for p in positions if p != -1]  # 丢掉没找到的（-1）
    return max(positions) if positions else -1  # 取最靠后的那个


# 否定写法的两种常见形态：「不通过」「未通过」。它们内部都含有「通过」两个字
_FAIL_WORDS = ("不通过", "未通过")


def _mask_fail_words(text):
    """
    把「不通过 / 未通过」整段替换成等长的占位符。

    为什么需要：这几个词内部含有「通过」两个字，如果不先遮掉，
    去找「通过」时会把「不通过」里面的「通过」也当成肯定结论，
    位置一比较就判反了（这正是改造前判错的那个坑）。
    替换成等长占位符是为了让下标位置保持不变，方便继续比较位置。
    """
    masked = text  # 从原文开始
    for word in _FAIL_WORDS:  # 逐个否定写法
        masked = masked.replace(word, "○" * len(word))  # 整段替换为等长占位符
    return masked  # 返回遮罩后的文本


def _parse_from_text(text):
    """
    （兜底路径）从自然语言文本里解析审核结论。

    仅在没有结构化裁决时使用（兼容改造前的历史数据）。
    相比改造前的实现做了两处修正：
      1. 优先匹配带「审核 / 结论」字样的完整结论句，而不是裸的「通过」两个字；
      2. 位置比较从「谁先出现」改成「谁最后出现」——结论通常在文末，
         而反例句「我通过……核对后确认，该笔住宿费超标，审核不通过」里
         真正的结论恰恰在最后。
    """
    text = (text or "").strip()  # 兼容 None，去掉首尾空白
    if not text:  # 空文本
        return None  # 没有结论可解析

    # 先把「不通过 / 未通过」遮掉，得到一份「只剩肯定写法」的文本，下标位置不变
    masked = _mask_fail_words(text)

    # 第一步：找带「审核 / 结论」字样的完整结论句，取最后出现的那一处
    strong_fail = _last_index_of(text, (  # 明确的否定结论（在原文里找）
        "审核不通过", "审核未通过", "结论：不通过", "结论为不通过",
        "最终结论：不通过", "最终结论为不通过",
    ))
    strong_pass = _last_index_of(masked, (  # 明确的肯定结论（在遮罩后的文本里找）
        "审核通过", "结论：通过", "结论为通过",
        "最终结论：通过", "最终结论为通过",
    ))
    if strong_fail != -1 or strong_pass != -1:  # 至少命中一个
        return VERDICT_FAIL if strong_fail > strong_pass else VERDICT_PASS  # 取最后出现的那个

    # 第二步：退一步，只看裸的「通过」，同样在遮罩后的文本里找最后出现的位置
    weak_fail = _last_index_of(text, _FAIL_WORDS)  # 裸的否定写法（在原文里找）
    weak_pass = _last_index_of(masked, ("通过",))  # 裸的肯定写法（在遮罩后的文本里找）
    if weak_fail != -1 or weak_pass != -1:  # 至少命中一个
        return VERDICT_FAIL if weak_fail > weak_pass else VERDICT_PASS  # 取最后出现的那个

    # 第三步：兜底看提示词里约定过的表情符号
    if "❌" in text:  # 提示词约定 ❌ 表示不通过
        return VERDICT_FAIL  # 判为不通过
    if "✅" in text:  # 提示词约定 ✅ 表示通过
        return VERDICT_PASS  # 判为通过
    return None  # 实在解析不出来


def extract_verdict(source):
    """
    取出最终结论，返回 "通过" / "不通过" / None（取不到）。

    参数可以是：
        - 结构化裁决（dict 或 AuditVerdict 对象）→ 读 verdict 字段（主路径）
        - 一段自然语言文本（str）→ 回退到字面解析（兜底）
        - 一条审核记录 dict（含 structured / agent_answer）→ 逐层回退
        - None → None
    """
    # 主路径：能拿到结构化裁决就直接读字段，完全不经过字符串解析
    structured = structured_verdict_of(source)  # 尝试取结构化裁决
    if structured:  # 拿到了
        return structured["verdict"]  # 直接返回字段值

    # 兜底路径：输入是一条记录但不含结构化裁决，用记录里的文本字段
    data = _as_dict(source)  # 统一成字典（字符串输入会得到空字典）
    for key in ("agent_answer", "answer", "final_answer", "text"):  # 依次尝试常见文本字段
        if data.get(key):  # 找到非空文本
            return _parse_from_text(data[key])  # 字面解析

    # 输入本身就是一段文本
    if isinstance(source, str):  # 字符串输入
        return _parse_from_text(source)  # 字面解析

    return None  # 以上都不成立


def resolve_final_verdict(audit_result):
    """
    从「一次终审的返回结果」里取出最终结论，带完整回退链。

    回退顺序（可靠性从高到低）：
        1. 结构化裁决的 verdict 字段 —— 主路径
        2. 记录里已有的 final_result —— 历史数据
        3. Agent 自然语言回答的字面解析 —— 最后兜底

    参数：
        audit_result: run_expense_form_audit / review_invoice 的返回，或审核记录 dict

    返回：
        "通过" / "不通过" / None
    """
    if not audit_result:  # 空输入
        return None  # 没有结论

    if isinstance(audit_result, dict):  # 字典形态：按回退链逐层尝试
        structured = structured_verdict_of(audit_result.get("verdict"))  # 第1层：结构化裁决
        if structured:  # 命中
            return structured["verdict"]  # 返回字段值

        structured = structured_verdict_of(audit_result.get("structured"))  # 第1层（另一种包装）
        if structured:  # 命中
            return structured["verdict"]  # 返回字段值

        if audit_result.get("final_result") in VALID_VERDICTS:  # 第2层：已有的最终结论
            return audit_result["final_result"]  # 直接返回

        for key in ("agent_answer", "answer"):  # 第3层：自然语言回答
            if audit_result.get(key):  # 有文本
                return _parse_from_text(audit_result[key])  # 字面解析

        return None  # 都没有

    return extract_verdict(audit_result)  # 其它类型交给统一入口


def normalize_audit(audit):
    """
    规范化一条审核记录，让 final_result 与实际情况保持一致。

    现在的流程里，发票这一层的审核记录有两种来源：
    - stage="basic_check"：只跑了基础合规校验（发票上传页写库的就是这种）
    - 进过 Agent 深度审核：记录里会有结构化裁决（structured）或回答文本（agent_answer）

    所以判断规则是（按可靠性排序）：
    - 有结构化裁决          → 以裁决字段为准（最可靠）
    - 只有自然语言回答      → 从回答里解析结论（兜底，兼容历史数据）
    - 都没有但有 final_result → 原样保留（例如基础校验自己的结论）
    - 什么都没有            → 标成「未审核」
    """
    if not audit:  # None 或空字典
        return audit  # 原样返回

    record = dict(audit)  # 复制一份，不改调用方的原对象

    # 第1优先级：结构化裁决
    structured = structured_verdict_of(record.get("structured"))  # 取结构化裁决
    if structured:  # 拿到了
        record["final_result"] = structured["verdict"]  # 直接采用字段值
        return record  # 返回

    # 第2优先级：自然语言回答（兼容改造前的旧记录）
    verdict = extract_verdict(record.get("agent_answer"))  # 字面解析
    if verdict:  # 解析成功
        record["final_result"] = verdict  # 覆盖可能过时的 final_result
        return record  # 返回

    # 第3优先级：什么都没有
    if not record.get("final_result"):  # 连结论都没有
        record["final_result"] = "未审核"  # 标成未审核
    return record  # 返回规范化后的记录

