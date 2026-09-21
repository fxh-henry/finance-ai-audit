# -*- coding: utf-8 -*-
"""
审核结论解析工具（utils/audit_verdict.py）
================================================================
为什么需要它：
    「基础校验通过」不等于「最终审核通过」。
    基础校验通过后还会再进 Agent 深度审核，Agent 依然可能判「不通过」。
    所以最终结论必须以 Agent 的回答为准。
    这里统一放一份解析 / 规范化逻辑，让上传页（存库）和详情页（展示）
    用的是同一个判断标准，避免两处结论对不上。
================================================================
"""


def extract_verdict(agent_answer):  # 从 Agent 的回答文本里解析出审核结论
    """把 Agent 回答解析成 "通过" / "不通过" / None（解析不出来）。"""
    text = (agent_answer or "").strip()  # 兼容 None，去掉首尾空白
    if not text:  # 空文本
        return None  # 无法判断

    # 先找“否定结论”的位置：注意「不通过」里也含「通过」，必须比位置而不能只做包含判断
    fail_at = -1  # 否定结论出现的位置，-1 表示没找到
    for word in ("不通过", "未通过"):  # 两种常见的否定写法
        pos = text.find(word)  # 该词在文本中的位置
        if pos != -1 and (fail_at == -1 or pos < fail_at):  # 取更靠前的那个
            fail_at = pos  # 记录位置

    pass_at = text.find("通过")  # 肯定结论的位置

    if fail_at != -1 and (pass_at == -1 or fail_at < pass_at):  # 否定结论出现在前面
        return "不通过"  # 判为不通过
    if pass_at != -1:  # 只有肯定结论
        return "通过"  # 判为通过

    # 兜底：看 Agent 按提示词约定输出的表情符号
    if "❌" in text:  # 提示词里 ❌ 表示不通过
        return "不通过"
    if "✅" in text:  # 提示词里 ✅ 表示通过
        return "通过"
    return None  # 实在解析不出来


def normalize_audit(audit):  # 规范化一条审核记录，让结论与实际情况保持一致
    """修正审核记录里的 final_result。

    现在的流程里，发票这一层的审核记录有两种来源：
    - stage="basic_check"：只跑了基础合规校验（发票上传页写库的就是这种）
    - 进过 Agent 深度审核：记录里会有 agent_answer
    所以判断规则是：
    - 有 Agent 回答          → 以回答里的结论为准（基础校验通过 ≠ 最终通过）
    - 没有回答但进过 Agent   → 说明 Agent 阶段没留下结论，判为「不通过」
    - 只跑了基础校验         → 结论就是基础校验的结论，原样保留
    """
    if not audit:  # None 或空字典
        return audit  # 原样返回

    record = dict(audit)  # 复制一份，不改调用方的原对象
    verdict = extract_verdict(record.get("agent_answer"))  # 解析 Agent 结论
    if verdict:  # 解析成功 → 以 Agent 为准
        record["final_result"] = verdict  # 覆盖可能过时的 final_result
        return record  # 返回

    if record.get("stage") == "agent_check":  # 进过 Agent 阶段却没有回答
        record["final_result"] = "不通过"  # 视为不通过
        return record  # 返回

    if not record.get("final_result"):  # 连结论都没有
        record["final_result"] = "未审核"  # 标成未审核
    return record  # 返回规范化后的记录

