# -*- coding: utf-8 -*-
# ============================================================================
# Agent 运行轨迹（agent/trace.py）
# ============================================================================
# 为什么要有这个文件：
#   LangGraph 的 ReAct Agent 在跑的时候会不断产出中间过程：
#       思考 -> 决定调哪个工具 -> 工具返回什么 -> 再思考 -> 最终结论
#   这些过程以前只用 info() 打印到服务器终端，网页上完全看不到，
#   于是「AI 审核」在评委眼里和一个 if-else 没有区别。
#
#   这个文件定义一个轻量的轨迹容器，把每一步收集起来，一份数据两个用途：
#     - 网页把轨迹渲染成时间轴（对应比赛评分里的「展示效果」）
#     - utils/verdict_guard.py 拿轨迹里的工具返回值做程序化对账（防幻觉）
# ============================================================================

from dataclasses import dataclass, field
# dataclass：Python的装饰器，自动生成__init__、__repr__等样板代码
# field：dataclass里用来定义默认值的工具（特别是可变默认值如列表）
from typing import Any, Dict, List
# Any  → 任意类型
# Dict → 字典类型
# List → 列表类型


# ---------------------------------------------------------------------------
# 步骤类型常量：避免各处手写字符串写错
# ---------------------------------------------------------------------------
STEP_THINKING = "thinking"
# 步骤类型常量：LLM在思考阶段，还没决定调工具
# 用常量而不是直接写字符串，是为了避免拼写错误
STEP_TOOL_CALL = "tool_call"
# LLM决定调用某个工具
STEP_TOOL_RESULT = "tool_result"
# 工具执行完了，返回结果
STEP_ANSWER = "answer"
# LLM给出最终的自然语言回答
STEP_STRUCTURED = "structured"
# 结构化裁决抽取完成


@dataclass
# @dataclass装饰器：自动给TraceStep类生成__init__方法
# 不用自己写def __init__(self, step=0, kind=..., ...)了
class TraceStep:
    # 轨迹里的一步，对应网页时间轴上的一个节点
    """Agent 轨迹里的一步，对应界面上时间轴的一个节点。"""

    step: int = 0
    # 第几步，整数，从1开始编号
    kind: str = STEP_THINKING
    # 步骤类型，默认是thinking（思考）
    text: str = ""
    # 这一步的文本内容（思考说了什么、最终回答是什么）
    tool_name: str = ""
    # 工具名，只有调工具时才有值，其他步骤是空字符串
    tool_args: Dict[str, Any] = field(default_factory=dict)
    # 工具入参，字典类型，默认空字典
    # 必须用field(default_factory=dict)，不能直接写{}，因为dict是可变对象
    tool_result: Dict[str, Any] = field(default_factory=dict)
    # 工具返回值，字典类型，默认空字典
    ok: bool = True
    # 这一步是否成功执行，默认成功

    def to_dict(self):
    # 把这一步转成普通字典
    # 为什么需要？因为Streamlit的session_state和JSON序列化都要求普通类型
        """转成普通字典（Streamlit 的 session_state 和 JSON 序列化都需要它）。"""
        return {
            "step": self.step,
            "kind": self.kind,
            "text": self.text,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "ok": self.ok,
        }


@dataclass
class AgentTrace:
    # 一次Agent完整运行的全部轨迹
    """一次 Agent 运行的全部过程记录。"""

    steps: List[TraceStep] = field(default_factory=list)
    # 按时间顺序排列的所有步骤
    final_answer: str = ""
    # 最终的自然语言回答文本
    structured: Dict[str, Any] = field(default_factory=dict)
    # 结构化裁决（AuditVerdict转成的字典）
    error: str = ""
    # 如果整轮运行出异常，这里记错误信息

    # -----------------------------------------------------------------------
    # 写入：追加一步
    # -----------------------------------------------------------------------
    def add(self, kind, text="", tool_name="", tool_args=None, tool_result=None, ok=True):
    # 追加一步到轨迹里
    # 参数：步骤类型、文本、工具名、入参、返回值、是否成功
        """追加一步轨迹，步骤号自动递增。"""
        step = TraceStep(
            step=len(self.steps) + 1,
            # 步骤号 = 当前已有步数 + 1，自动递增
            kind=kind,
            text=text or "",
            # text or ""：如果text是None就用空字符串
            tool_name=tool_name or "",
            # 同上
            tool_args=dict(tool_args or {}),
            # dict(tool_args or {})：复制一份入参，防止外部修改影响轨迹
            tool_result=dict(tool_result or {}),
            # 同上，复制一份返回值
            ok=ok,
        )
        self.steps.append(step)
        # 把这一步加到列表末尾
        return step  # 返回这一步，调用方还能继续补充字段

    # -----------------------------------------------------------------------
    # 读取：给校验层和界面用的几个视图
    # -----------------------------------------------------------------------
    @property
    # @property装饰器：把方法变成属性，访问时不用加括号()
    def tool_names(self):
    # 本轮实际调用过的所有工具名集合
    # 用来检查：证据里引用的工具是不是真的调过
        """本轮实际调用过的工具名集合（用来识别「引用了没调过的工具」这种幻觉）。"""
        return {s.tool_name for s in self.steps
                if s.kind == STEP_TOOL_RESULT and s.tool_name}
        # 集合推导式：
        # 遍历所有步骤，找出kind是tool_result且有工具名的步骤
        # 提取它们的tool_name，去重成集合

    @property
    def tool_results(self):
    # 所有工具返回值步骤的列表
        """所有工具返回的步骤（顺序与调用顺序一致）。"""
        return [s for s in self.steps if s.kind == STEP_TOOL_RESULT]
        # 列表推导式：过滤出工具结果步骤

    def results_of(self, tool_name):
    # 取某个特定工具的所有返回值
    # 比如取expense_standard_tool的所有返回值
        """取指定工具的所有返回值，按调用顺序排列。"""
        return [s.tool_result for s in self.steps
                if s.kind == STEP_TOOL_RESULT and s.tool_name == tool_name]
        # 只取指定工具的返回值，按调用顺序排

    def to_dict(self):
    # 整个轨迹转成普通字典
        """整体转成普通字典（存 session_state / 写库用）。"""
        return {
            "steps": [s.to_dict() for s in self.steps],
            # 每一步都调用它自己的to_dict()
            "final_answer": self.final_answer,
            "structured": self.structured,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# 函数：把工具返回值（可能是 dict，也可能是字符串）统一转成 dict
# ---------------------------------------------------------------------------
def as_dict(value):
    # 工具函数：把工具返回值统一转成字典
    # 为什么需要？因为LangChain不同版本的工具返回值格式不一样：
    # 有的直接返回dict，有的转成JSON字符串，有的是Python repr字符串（单引号）
    """
    把工具返回值统一转成 dict。

    为什么要这个：LangChain 把工具返回值塞进 ToolMessage.content 时，
    不同版本可能保留 dict，也可能转成 JSON 字符串、甚至 Python repr 字符串
    （单引号、True/False 那种）。校验层要读里面的 passed / standard 字段，
    所以这里做一层容错解析。
    """
    if isinstance(value, dict):
    # 本来就是字典，直接返回
        return value
    if isinstance(value, (list, tuple)):
    # 是列表或元组（新版LangChain的消息格式是内容块列表）
        for item in value:
        # 逐个找字典
            if isinstance(item, dict) and "text" not in item:
            # 找到一个不是纯文本块的字典
                return item  # 当作结构化结果
        return {"_raw": str(value)}
        # 找不到就包成原始字符串
    if isinstance(value, str):
    # 是字符串，尝试两种解析方式
        text = value.strip()
        # 去掉首尾空白
        if not text:
        # 空字符串直接返回空字典
            return {}
        try:
        # 第一种：按标准JSON解析（双引号）
            import json
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"_raw": text}
        except Exception:
            pass  # 不是JSON，试下一种
        try:
        # 第二种：按Python字面量解析（单引号、True/False那种repr格式）
            import ast
            parsed = ast.literal_eval(text)
            return parsed if isinstance(parsed, dict) else {"_raw": text}
        except Exception:
            return {"_raw": text}
            # 两种都失败，返回原始字符串
    return {"_raw": str(value)}
    # 其它类型（数字、None等）都包成原始字符串
