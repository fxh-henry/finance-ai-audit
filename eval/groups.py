# -*- coding: utf-8 -*-
"""
消融实验的五个后处理组（A/B/C/D/E）
================================================================
一句话概括五组的差别：模型只跑一次，五组吃的是同一份 runs/*.json，
唯一的变量是「怎么从这份输出里得出最终结论」。

    C  纯规则       完全不看模型输出，只跑 12 项基础校验 + 6 项风控
    D  自由文本     从模型自然语言结论里用字符串位置猜（冻结的改造前实现）
    B  结构化       直接读结构化裁决的 verdict 字段
    A  结构化+校验  读字段之后再过一遍防幻觉校验（校验层可能改写结论）
    E  A+反思       A 之后若命中可澄清项，用反思轮结论收敛替换

C 组回答「AI 相对硬编码规则有没有增量」；
D→B 回答「结构化裁决值多少」；
B→A 回答「防幻觉校验值多少」；
A→E 回答「反思机制值多少」。
================================================================
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:  # 保证能 import 到项目里的模块
    sys.path.insert(0, str(ROOT))

from agent.trace import AgentTrace, TraceStep  # 轨迹对象：把存下来的字典还原成对象
from utils.verdict_guard import verify_verdict  # 防幻觉校验层（A 组用）
from utils.reflection import reconcile  # 反思收敛（E 组用）
from eval.legacy_text_verdict import legacy_parse  # 冻结的旧解析器（D 组用）

# TraceStep 是 dataclass，按字段名重建即可；先过滤掉多余键，避免历史 runs 格式变化时报错
_TRACE_STEP_FIELDS = {"step", "kind", "text", "tool_name", "tool_args", "tool_result", "ok"}


def trace_from_dict(data):
    """把存下来的轨迹字典还原成 AgentTrace 对象（不需要给主代码加 from_dict）。"""
    data = data or {}  # 兼容 None
    steps = []  # 还原出来的步骤列表
    for raw in (data.get("steps") or []):  # 逐步还原
        kwargs = {k: v for k, v in (raw or {}).items() if k in _TRACE_STEP_FIELDS}  # 只取认识的字段
        steps.append(TraceStep(**kwargs))  # 构造 TraceStep
    return AgentTrace(  # 组装整条轨迹
        steps=steps,
        final_answer=data.get("final_answer") or "",
        structured=data.get("structured") or {},
        error=data.get("error") or "",
    )


# 纯规则组的第三态：规则不敢下结论（数据不足 / 制度里没写标准）。
# 单独拿出来统计：既不当作判对，也不当作判错。
HUMAN = "转人工"


# ---------------------------------------------------------------------------
# C 组：纯规则（不读模型输出）
# ---------------------------------------------------------------------------
def group_C(run):
    """
    纯规则基线：把所有「可以用硬编码实现」的判断全部用上，不调用任何大模型。

    ⚠️ 这一组的强弱直接决定实验是否可信，所以设计上刻意「往强里做」：
       如果故意只跑基础校验、不给它费用标准，C 组会很差，AI 的"增量"就被虚高了，
       那是自证，不是测量。反过来，凡是能硬编码的都给它，AI 的增量才是真的。

    判定口径（全部是确定性函数）：
        1) 12 项基础校验出现 error 级问题        → 不通过
        2) 6 项风控出现 high 级风险              → 不通过
        3) 费用标准（按 费用类型 + 城市 + 职级）
              passed is False → 不通过（明确超标）
              passed is None  → 转人工（数据不足 / 制度没写标准，规则不敢下结论）
        其余                                     → 通过

    为什么保留「转人工」这一态：
        把「制度没写标准」硬算成「不通过」，会出现两种失真：
        a) 合规的单子被冤枉成违规（例如材料费，制度里本来就没有金额标准）；
        b) C 组在这些样本上会「看起来判对」，其实是碰巧，不是能力。
        三态才能把「规则判错」和「规则不敢判」分开，AI 的增量才说得清。
    """
    from audit.basic_check import basic_check  # 12 项基础校验（纯函数）
    from audit.anomaly_detect import run_all_anomaly_checks  # 6 项风控（纯函数）
    from audit.expense_standard import check_expense_standard  # 费用标准（纯函数）
    from utils.expense_form import guess_expense_type  # 发票内容 → 费用类型

    invoice_data = run.get("invoice_data") or {}  # 发票字段
    form = run.get("expense_form") or {}  # 报销单字段（城市、费用类型在这里）
    if not form and not run.get("employee_id"):
        # 连报销单都没有的用例，C 组只能靠基础校验和风控
        form = {}

    # ---- 规则1：基础校验 ----
    basic = basic_check(invoice_data)  # 跑 12 项
    if basic.get("error_count"):  # 有 error 级问题
        return "不通过"

    # ---- 规则2：风控 ----
    anomaly = run.get("anomaly_result")  # 优先用 runs 里存好的结果
    if anomaly is None:  # 没存就现算（只读数据库）
        employee_id = run.get("employee_id")
        anomaly = run_all_anomaly_checks(employee_id, invoice_data) if employee_id else {}
    high = [i for i in (anomaly.get("risk_items") or []) if i.get("level") == "high"]  # high 级风险
    if high:
        return "不通过"

    # ---- 规则3：费用标准（硬编码同样能做到，所以必须给 C 组） ----
    ext = form.get("ext_fields") or {}  # 分类扩展字段
    expense_type = form.get("expense_type") or guess_expense_type(invoice_data)  # 费用类型
    city = ext.get("city") or ""  # 住宿城市
    level = run.get("employee_level") or ""  # 报销人职级
    try:
        amount = float(invoice_data.get("价税合计小写") or 0)  # 票面金额
    except (TypeError, ValueError):
        amount = 0.0
    days = ext.get("nights") or 1  # 天数（住宿按晚数）
    std = check_expense_standard(expense_type, city=city, level=level, amount=amount, days=days)  # 算标准
    if std.get("passed") is False:  # 明确超标
        return "不通过"
    if std.get("passed") is None:  # 数据不足 / 制度没写标准 → 规则不敢下结论
        return HUMAN

    return "通过"


# ---------------------------------------------------------------------------
# D 组：自由文本 + 旧解析器
# ---------------------------------------------------------------------------
def group_D(run):
    """从模型自然语言结论里猜结论（调用的是冻结的改造前实现）。"""
    return legacy_parse(run.get("answer") or "")


# ---------------------------------------------------------------------------
# B 组：结构化裁决，直接采信
# ---------------------------------------------------------------------------
def group_B(run):
    """直接读结构化裁决的 verdict 字段。"""
    return ((run.get("structured") or {}).get("verdict")) or None


# ---------------------------------------------------------------------------
# A 组：结构化 + 防幻觉校验
# ---------------------------------------------------------------------------
def group_A(run):
    """读字段之后过一遍防幻觉校验，取校验层实际采用的结论。"""
    trace = trace_from_dict(run.get("trace"))  # 还原轨迹
    report = verify_verdict(  # 跑校验层
        run.get("structured") or {},
        trace=trace,
        invoice_data=run.get("invoice_data") or {},
        anomaly_result=run.get("anomaly_result"),
    )
    return report.get("final_verdict") or None


# ---------------------------------------------------------------------------
# E 组：A + 反思
# ---------------------------------------------------------------------------
def group_E(run):
    """
    A 组结论，再按反思轮结果收敛。

    反思轮要调模型，回放时不可能现跑，所以 run_model_once.py 必须把反思轮结果
    一并存进 runs（字段 reflection）。没有该字段时 E 组自动退化为 A 组。
    """
    base = group_A(run)  # 先拿 A 组结论
    reflection = run.get("reflection") or {}  # 反思轮记录
    if not reflection.get("triggered"):  # 没触发反思，等同于 A
        return base

    if reflection.get("final_verdict"):  # 最简单的情况：直接存了收敛后的结论
        return reflection["final_verdict"]

    second = reflection.get("second_structured") or {}  # 反思轮的结构化裁决
    if not second:  # 反思轮没产出有效裁决
        return base
    chosen, _note, _used = reconcile(  # 走收敛约束（没有新证据不许放宽结论）
        run.get("structured") or {},
        second,
        trace_from_dict(run.get("trace")),
        trace_from_dict(reflection.get("second_trace")),
    )
    return (chosen or {}).get("verdict") or base


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
GROUPS = {  # 组名 → (人看的说明, 执行函数)
    "C": ("纯规则", group_C),
    "D": ("自由文本（旧解析）", group_D),
    "B": ("结构化无校验", group_B),
    "A": ("结构化+防幻觉校验", group_A),
    "E": ("A+反思（交付态）", group_E),
}


def run_group(name, run):
    """按名称执行某一组，返回该组结论（通过 / 不通过 / None）。"""
    return GROUPS[name][1](run)  # 取第 2 个元素是执行函数