# -*- coding: utf-8 -*-
# ============================================================================
# 结构化审核结论模型（agent/schemas.py）
# ============================================================================
# 为什么要有这个文件：
#   改造前，Agent 的最终结论是一段自然语言（"✅ 审核通过 / ❌ 审核不通过"），
#   系统再到 utils/audit_verdict.py 里靠「字符串出现位置」去猜结论。
#   这套做法在真实句子上会判错，实测反例：
#       输入：我通过调用费用标准工具核对后确认，该笔住宿费超标，审核不通过。
#       旧函数输出：通过            ← 一张超标发票被放行
#
#   现在换成「schema 约束生成」：
#   把结论定义成一个 Pydantic 模型，模型在解码阶段就只能往 verdict 字段里
#   填「通过」或「不通过」这两个字面量之一，LLM 没有机会把结论写模糊。
#   一句话：结论从「需要猜的文本」变成「可以直接读的字段」。
# ============================================================================

from typing import List, Literal, Optional
# 从typing导入三个类型工具：
#   List      → 表示一个列表类型
#   Literal   → 把变量钉死成几个固定值之一（枚举效果）
#   Optional  → 表示这个字段可以为None

from pydantic import BaseModel, Field
# 从pydantic（数据校验库）导入：
#   BaseModel → 所有数据模型的父类，继承它就能自动做类型校验
#   Field     → 给字段加描述文字，这段描述会变成LLM看到的JSON Schema提示词


# ---------------------------------------------------------------------------
# 模型1：一条证据
#   证据不是让模型自由发挥，而是要求它「引用系统里某个确定性来源给出的事实」。
#   下游 utils/verdict_guard.py 会拿这些 evidence 逐条和真实工具返回值对账。
# ---------------------------------------------------------------------------
class EvidenceItem(BaseModel):
    # 定义一条"证据"的数据结构
    # 继承BaseModel后，LLM输出的内容会被自动校验类型，不合法就报错
    """一条支撑结论的事实，必须来自确定性来源，不能是模型自己的推断。"""

    source: str = Field(
        # source字段：字符串类型，表示这条证据来自哪里
        # Field(...)的description参数会告诉LLM"这个字段该填什么"
        description=(
            "证据来源，只能填以下之一："
            "expense_standard_tool（费用标准工具的计算结果）、"
            "rag_policy_search_tool（制度检索工具返回的条款）、"
            "anomaly_check（风控检测结果）、"
            "basic_check（基础合规校验结果）、"
            "invoice_field（发票票面字段）"
        )
        # description就是给LLM看的填写说明：
        # 证据来源只能是这5个之一，不能瞎编一个来源
    )
    fact: str = Field(
        # fact字段：证据的具体内容，一句话
        description="证据内容，一句话；其中出现的数字必须与来源返回值完全一致，禁止四舍五入、改写或估算"
        # 强调数字必须原样照搬，不能自己算或改
    )
    passed: Optional[bool] = Field(
        # passed字段：可以是True/False/None三种
        # Optional[bool] = bool | None，意思是"这个字段可以不填，不填就是None"
        default=None,
        # default=None表示：如果LLM没填这个字段，默认就是None
        description="该证据是否支持报销：支持=True；不支持=False；仅作参考的制度条款=None",
        # 三种取值的含义：
        #   True  → 这条证据支持报销通过
        #   False → 这条证据不支持报销（比如超标了）
        #   None  → 这条只是制度参考，不直接判断通过不通过
    )


# ---------------------------------------------------------------------------
# 模型2：最终裁决
#   这是整个审核链路的产出物，也是写进数据库的那份结论。
# ---------------------------------------------------------------------------
class AuditVerdict(BaseModel):
    # 定义整张报销单的"最终裁决"数据结构
    # 这是整个审核系统最核心的数据契约
    """Agent 给出的最终裁决（字段受 schema 约束，可被程序直接校验）。"""

    verdict: Literal["通过", "不通过"] = Field(
        # verdict字段：最终结论
        # Literal["通过", "不通过"]是关键——LLM在生成时只能填这两个词之一
        # 填"基本通过""原则可行"都会被pydantic校验拒绝
        description="最终审核结论，只能填「通过」或「不通过」；不允许写「基本通过」「原则上通过」这类模糊说法"
    )
    expense_type: str = Field(
        # expense_type字段：认定的费用类型
        default="其他",
        # 默认值是"其他"，LLM没填就归到其他类
        description="认定的费用类型，如：住宿费 / 餐饮费 / 市内交通费 / 差旅交通费 / 办公费 / 其他"
    )
    reasons: List[str] = Field(
        # reasons字段：理由列表，是一个字符串数组
        default_factory=list,
        # default_factory=list表示：默认值是一个空列表[]
        # 不能直接写default=[]，因为列表是可变对象，pydantic要求用factory
        description="判定理由，逐条列出，每条一句话，说明「是什么事实导致了这个结论」"
    )
    evidence: List[EvidenceItem] = Field(
        # evidence字段：证据链列表，每个元素都是EvidenceItem类型
        default_factory=list,
        # 默认空列表
        description="支撑结论的证据清单；每一条都必须能在工具返回值里找到对应物，不允许凭空编造"
    )
    citations: List[str] = Field(
        # citations字段：引用的制度条款出处列表
        default_factory=list,
        description="引用的制度条款出处（文件名 + 条款标题）；没有引用就留空，禁止编造条款"
    )
    confidence: float = Field(
        # confidence字段：置信度，浮点数
        default=0.5,
        # 默认0.5（中等置信度）
        description="结论置信度，填 0 到 1 之间的小数（例如 0.9），不要填百分数"
    )
    needs_human: bool = Field(
        # needs_human字段：是否建议转人工，布尔值
        default=False,
        # 默认不需要转人工
        description="当发票信息不足、材料互相矛盾、或制度没有覆盖该情形时置 True，表示建议转人工复核"
    )
    explanation: str = Field(
        # explanation字段：给员工看的自然语言说明
        default="",
        # 默认空字符串
        description="给员工看的完整中文说明：结论 + 原因 + 建议怎么办，保持自然语言口吻，不要出现 JSON 或字段名"
    )


# ---------------------------------------------------------------------------
# 函数：把裁决对象转成可直接存库的普通字典
# ---------------------------------------------------------------------------
def verdict_to_dict(verdict):
    # 工具函数：把AuditVerdict对象转成普通字典
    # 为什么需要这个？因为存数据库、写session_state、转JSON时都需要普通字典
    """把 AuditVerdict 转成 dict；传入本来就是 dict 时原样返回（便于旧数据兼容）。"""
    if verdict is None:
    # 如果传入None，直接返回None
        return None
    if isinstance(verdict, dict):
    # isinstance检查：如果传入的本来就是字典（比如从数据库读出来的）
        return verdict  # 原样返回，不用转
    if hasattr(verdict, "model_dump"):
    # hasattr检查：如果对象有model_dump方法（Pydantic v2版本）
        return verdict.model_dump()  # 调用它转成字典
    if hasattr(verdict, "dict"):
    # 如果对象有dict方法（Pydantic v1版本，兼容旧版）
        return verdict.dict()  # 旧版方法名不同
    return None  # 都不是，返回None表示无效
