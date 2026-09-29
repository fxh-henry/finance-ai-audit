# -*- coding: utf-8 -*-
# ============================================================================
# 审查Agent（agent/review_agent.py）
# ============================================================================
# 作用：接收基础校验通过的发票数据，调用工具进行深度审核，
#       并把最终结论输出成「受 schema 约束的结构化裁决」
# 技术：LangGraph create_react_agent（ReAct模式：思考→行动→观察→回答）
#       + json_schema 结构化输出（结论字段被钉死成枚举，不再从散文里猜）
#       + AgentTrace 运行轨迹（记录每一次工具调用的真实返回值）
#
# 为什么要改成结构化输出：
#   改造前结论是一段自然语言，系统靠 utils/audit_verdict.py 找字符串位置来猜结论。
#   实测反例：「我通过调用费用标准工具核对后确认，该笔住宿费超标，审核不通过。」
#   旧逻辑输出「通过」——一张超标发票被放行。改成读字段后这个坑就不存在了。
#
# 使用方式：
#   agent = ReviewAgent(invoice_data)      # 一场对话创建一个实例
#   print(agent.chat("请审核这张发票"))      # 只要自然语言回答（旧用法，完全兼容）
#   r = agent.audit()                       # 要结构化裁决 + 运行轨迹（新用法）
#   print(r["verdict"]["verdict"])          # 直接读结论字段，不用猜
# ============================================================================

import json  # 兜底解析工具返回值（正常路径由 agent.trace.as_dict 处理）
import sys
from pathlib import Path

# 把项目根目录加入Python搜索路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 导入LangChain的提示词模板
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# 导入LangGraph的预构建ReAct Agent
from langgraph.prebuilt import create_react_agent

# 导入大模型实例（复用llm包里的RAGLLM）
from llm.RAGLLM import default_rag_llm

# 导入Agent工具列表
from agent.tools.audit_tools import AGENT_TOOLS, set_audit_context
# set_audit_context：把当前报销人的职级注入工具层，模型漏传 level 时自动兜底

# 导入结构化裁决模型：verdict 字段被 Literal 钉死成「通过 / 不通过」
from agent.schemas import AuditVerdict, verdict_to_dict

# 导入运行轨迹容器：把「思考→调工具→看结果→结论」收集成结构化数据
from agent.trace import (
    AgentTrace,        # 整轮运行的轨迹
    STEP_ANSWER,       # 步骤类型：最终回答
    STEP_STRUCTURED,   # 步骤类型：结构化裁决完成
    STEP_THINKING,     # 步骤类型：思考
    STEP_TOOL_CALL,    # 步骤类型：决定调用工具
    STEP_TOOL_RESULT,  # 步骤类型：工具返回结果
    as_dict,           # 把工具返回值统一转成 dict
)

# 导入日志工具，打印Agent运行过程
from rag.utils.logger import info, success, warn, error
# 导入报销单工具：把报销单拼成提示词可用的文字（分类扩展字段、明细等）
from utils.expense_form import build_agent_text


# ============================================================================
# 函数：构建系统提示词
# ============================================================================
def build_system_prompt(invoice_data, expense_form=None, basic_check_text=None, employee=None):
    """
    根据发票数据（以及可选的报销单、基础校验结果、报销人）构建Agent的系统提示词

    作用：把发票 / 报销单 / 基础校验结果 / 报销人职级注入到提示词里，
          让Agent知道在审核什么、以及按哪一档职级标准核算

    参数：
        invoice_data: 识别出的发票数据字典
        expense_form: 报销单数据字典（可选），填了报销单时一起注入
        basic_check_text: 基础合规校验的结果文本（可选）。从「发票上传」页咨询时
                          会把这次的基础校验明细一起带进来，Agent 才能针对不通过项解释
        employee: 当前报销人字典（可选，含 name / department / level）。
                  职级决定住宿费等费用的标准档位，必须注入，否则Agent只能猜

    返回：
        系统提示词字符串
    """
    # 从发票数据中提取关键字段，找不到就用默认值
    invoice_type = invoice_data.get("发票类型", "未知")
    invoice_no = invoice_data.get("发票号码", "未知")
    invoice_date = invoice_data.get("开票日期", "未知")
    buyer = invoice_data.get("购买方名称", "未知")
    seller = invoice_data.get("销售方名称", "未知")
    total_amount = invoice_data.get("价税合计小写", "未知")
    # 项目名称：优先取顶层字段，没有就从明细列表第一条取（很多发票的项目名称只在明细里）
    item_name = invoice_data.get("项目名称", "")
    if not item_name:
        detail_list = invoice_data.get("明细列表", [])
        if detail_list and isinstance(detail_list, list):
            item_name = detail_list[0].get("项目名称", "未知")
    if not item_name:
        item_name = "未知"

    # 报销单段落：没填报销单时是空字符串，填了就把整张单子摊开给Agent看
    form_block = ""
    extra_rules = ""
    if expense_form:
        form_block = "【报销单信息（员工填写，作为补充材料）】" + chr(10) + build_agent_text(expense_form) + chr(10)
        extra_rules = """6. 报销单是员工对本次费用的补充说明：住宿费重点看晚数/房间数/入住人/城市，餐饮费看就餐人数/次数
7. 住宿费请按「每晚每间单价 = 住宿费总额 ÷ 住宿晚数 ÷ 房间数」折算后，再用费用标准校验工具判断是否超标
8. 如果发票信息与报销单信息互相矛盾，以发票为准，并在结论中明确指出矛盾点
9. 如果报销单已经补齐了发票上缺失的信息（例如住宿清单），不要再以“缺少住宿清单”为理由判不通过
"""

    # 基础校验段落：从「发票上传」页咨询时会带上这份结果
    basic_block = ""
    basic_rules = ""
    rule_one = "1. 基础合规校验已通过，你需要做深度审核"
    if basic_check_text:
        basic_block = "【系统已执行的基础合规校验结果】" + chr(10) + str(basic_check_text).strip() + chr(10)
        basic_rules = "10. 上面已经给出系统的基础校验结果，请直接针对其中的不通过项 / 警告项解释原因，并给出可执行的补救办法" + chr(10)
        rule_one = "1. 系统已经跑完基础合规校验（结果见上），请结合它给出你的判断"

    # 报销人段落：把当前员工的职级显式写进提示词
    # 为什么必须注入：费用标准是「职级 × 城市等级」查表的，
    # 提示词里不写职级，模型就只能猜或漏传，最终结果会悄悄按最低档核算
    employee_block = ""  # 报销人段落（没有员工信息时留空）
    employee_rules = ""  # 职级相关的补充规则（没有员工信息时留空）
    if employee:
        emp_name = employee.get("name") or "未知"  # 报销人姓名
        emp_dept = employee.get("department") or "未知"  # 报销人部门
        emp_level = employee.get("level") or "未知"  # 报销人职级（标准档位）
        employee_block = (
            "【当前报销人（职级决定费用标准档位）】" + chr(10)
            + f"- 姓名：{emp_name}" + chr(10)
            + f"- 部门：{emp_dept}" + chr(10)
            + f"- 职级：{emp_level}" + chr(10)
        )  # 三行文字注入提示词
        employee_rules = (
            f"11. 调用【费用标准校验工具】时，level 必须填「{emp_level}」，不要留空、不要自己改档" + chr(10)
            + "12. 住宿费必须同时传 city（出差城市）与 level，缺任何一个都会返回待人工确认，不得据此判员工不通过" + chr(10)
        )  # 两条硬规则，防止模型漏参

    # 组装系统提示词
    system_prompt = f"""你是一位专业的财务审核专家，负责审核员工报销的发票。

            【当前审核的发票信息】
            - 发票类型：{invoice_type}
            - 发票号码：{invoice_no}
            - 开票日期：{invoice_date}
            - 购买方：{buyer}
            - 销售方：{seller}
            - 项目名称：{item_name}
            - 价税合计：{total_amount}元
            {employee_block}{form_block}{basic_block}
            【审核规则】
            {rule_one}
            2. 根据发票的项目名称，判断费用类型（住宿费/餐饮费/交通费/办公费等）
            3. 住宿费、交通费等有明确标准的费用，调用【费用标准校验工具】检查是否超标
            4. 需要查找制度依据时，调用【RAG制度检索工具】获取相关条款
            5. 综合所有工具返回的结果，给出审核结论
            {extra_rules}{basic_rules}{employee_rules}
            【输出要求】
            如果审核通过，输出：
            ✅ 审核通过
            理由：（简要说明为什么通过）
            
            如果审核不通过，输出：
            ❌ 审核不通过
            原因：（具体说明哪里有问题）
            建议：（告诉员工应该怎么办）
            
            注意：
            - 不要自己计算金额，所有计算都通过工具完成
            - 不要编造制度条款，需要时调用RAG工具检索
            - 回答要简洁明了，让员工一眼看懂"""

    return system_prompt


# ============================================================================
# 函数：带日志的Agent执行（stream模式，逐步打印运行过程并收集轨迹）
# ============================================================================
def _emit(on_event, event):
    """把事件回调给调用方；回调本身出错不影响审核流程。"""
    if not on_event:  # 没传回调
        return  # 什么都不做
    try:  # 回调是外部代码（可能是 Streamlit 组件），必须防它抛错把审核带崩
        on_event(event)  # 触发回调
    except Exception as exc:  # 回调出错
        warn(f"【Agent】事件回调异常（已忽略）：{exc}")  # 记一条警告，继续跑


def run_agent_stream(agent, messages, recursion_limit=10, on_event=None):
    """
    执行Agent，把运行过程逐事件抛出，并收集成结构化轨迹。

    用stream模式而不是invoke模式：stream会逐步产出每一步的结果，
    这样既能收集「工具到底返回了什么」（供结构化抽取和后续校验使用），
    也能把过程实时推给界面渲染。

    参数：
        agent: LangGraph Agent实例
        messages: 消息列表，格式[("user", "..."), ("assistant", "...")]
        recursion_limit: 最大循环次数，防止死循环
        on_event: 可选回调，每产生一个事件调用一次，格式 {"type": ..., ...}

    返回：
        AgentTrace：完整运行轨迹（含最终回答；结构化裁决字段由调用方补充）
    """
    info("=" * 60)
    info("【Agent】开始执行")
    info("=" * 60)

    trace = AgentTrace()  # 新建轨迹容器

    try:  # 整个循环包起来，任何异常都记进轨迹而不是直接崩掉页面
        for chunk in agent.stream(  # stream模式：逐步产出，每一步是一个chunk
            {"messages": messages},  # 输入消息（含历史）
            config={"recursion_limit": recursion_limit}  # 限制最大步数
        ):
            # chunk格式：{节点名: 节点输出}
            # 节点名有三种：
            #   "agent"  → LLM思考节点，输出AIMessage
            #   "tools"  → 工具执行节点，输出ToolMessage
            #   "generate_structured_response" → 结构化裁决节点（本项目改造后不使用，兼容保留）
            for node_name, node_output in chunk.items():
                # ------------------------------------------------------------
                # 情况1：节点是"agent" → LLM的思考 / 决定调工具 / 最终回答
                # ------------------------------------------------------------
                if node_name == "agent":
                    for msg in (node_output or {}).get("messages", []) or []:
                        tool_calls = getattr(msg, "tool_calls", None) or []  # 本步决定调用的工具
                        if tool_calls:  # 调工具前模型可能先说了句思考
                            if msg.content:  # 有思考内容
                                step = trace.add(STEP_THINKING, text=msg.content)  # 记一步思考
                                info(f"【Agent第{step.step}步】LLM思考: {msg.content[:200]}")  # 打日志
                                _emit(on_event, {"type": STEP_THINKING, "step": step.step, "text": msg.content})  # 推给界面
                            for tc in tool_calls:  # 可能同时调用多个工具
                                step = trace.add(STEP_TOOL_CALL, tool_name=tc["name"], tool_args=tc.get("args") or {})  # 记一步调用
                                info(f"【Agent第{step.step}步】LLM决定调用工具: {tc['name']}")  # 打日志
                                info(f"  调用参数: {tc.get('args')}")  # 打日志
                                _emit(on_event, {"type": STEP_TOOL_CALL, "step": step.step,
                                                 "tool_name": tc["name"], "tool_args": tc.get("args") or {}})  # 推给界面
                        else:  # tool_calls是空列表 → 这是最终回答
                            trace.final_answer = msg.content or ""  # 记下最终回答
                            step = trace.add(STEP_ANSWER, text=trace.final_answer)  # 记一步回答
                            success(f"【Agent第{step.step}步】LLM生成最终回答")  # 打日志
                            _emit(on_event, {"type": STEP_ANSWER, "step": step.step, "text": trace.final_answer})  # 推给界面
                # ------------------------------------------------------------
                # 情况2：节点是"tools" → 工具执行结果
                # ------------------------------------------------------------
                elif node_name == "tools":
                    for msg in (node_output or {}).get("messages", []) or []:
                        tool_name = getattr(msg, "name", "") or "未知工具"  # 工具名
                        result = as_dict(msg.content)  # 把返回值统一转成 dict（供结构化抽取/校验读字段）
                        # 工具执行失败时，LangChain 会把错误文本塞进 ToolMessage.content，
                        # 经 as_dict() 变成 {"_raw": ...}；据此标记 ok=False，让详情页能把失败步骤标红
                        tool_ok = not ("_raw" in result or "error" in result)  # 判定这一步是否成功
                        step = trace.add(STEP_TOOL_RESULT, tool_name=tool_name, tool_result=result, ok=tool_ok)  # 记一步结果
                        success(f"【Agent第{step.step}步】工具[{tool_name}]执行完成")  # 打日志
                        info(f"  返回结果: {str(result)[:150]}...")  # 打日志（只打前150字）
                        _emit(on_event, {"type": STEP_TOOL_RESULT, "step": step.step,
                                         "tool_name": tool_name, "tool_result": result})  # 推给界面
                # ------------------------------------------------------------
                # 情况3：节点是"generate_structured_response" → 图内结构化裁决（兼容保留）
                # ------------------------------------------------------------
                elif node_name == "generate_structured_response":
                    data = verdict_to_dict((node_output or {}).get("structured_response")) or {}  # 取结构化对象
                    trace.structured = data  # 记进轨迹
                    step = trace.add(STEP_STRUCTURED, text=data.get("verdict", ""))  # 记一步
                    _emit(on_event, {"type": STEP_STRUCTURED, "step": step.step, "data": data})  # 推给界面
    except Exception as exc:  # 运行异常（网络、解析、超步数等）
        trace.error = str(exc)  # 记进轨迹，交给调用方决定怎么兜底
        error(f"【Agent】执行异常: {exc}")  # 打日志

    info("=" * 60)
    success(f"【Agent】执行完成，共{len(trace.steps)}步")  # 打印总步数
    info("=" * 60)

    return trace  # 返回轨迹


def _run_agent_with_logging(agent, messages, recursion_limit=10):
    """
    执行Agent并逐步打印运行过程（保留旧接口，内部已改用 run_agent_stream）

    参数：
        agent: LangGraph Agent实例
        messages: 消息列表
        recursion_limit: 最大循环次数

    返回：
        Agent的最终回答字符串
    """
    trace = run_agent_stream(agent, messages, recursion_limit=recursion_limit)  # 复用新的轨迹化执行
    return trace.final_answer  # 旧调用方只关心最终回答


# ============================================================================
# 结构化裁决抽取：把「一轮审核过程」整理成字段确定的裁决
# ============================================================================
# 为什么单独抽一步，而不是让审核Agent直接输出结构化数据：
#   1) 职责分离：审核Agent负责「调查 + 解释」，抽取器负责「把结论形式化」。
#      两个环节各自可测，出问题能定位到是哪一环。
#   2) 输入更干净：抽取器拿到的是「发票字段 + 工具真实返回值 + 自然语言结论」，
#      而不是一长串对话，抽取准确率更高（已实测）。
#   3) 更快：抽取任务不需要深度推理，可以关掉思考模式，
#      实测 52.2 秒 → 13.7 秒（同一个任务、同样正确的结论）。
# ============================================================================

# 抽取器的系统提示词
EXTRACT_SYSTEM_PROMPT = """你是财务审核结论的「结构化抽取器」。

下面会给你一轮已经跑完的审核过程记录，包含三部分：
1. 发票票面字段
2. 系统确定性工具的真实返回值（费用标准工具的计算结果、制度检索工具返回的条款原文）
3. 审核助手给出的自然语言结论

你的任务：把这次审核整理成结构化裁决。规则：
- verdict 必须忠实于「审核过程的最终判断」，不要自己重新判定，也不要改变原判断方向
- reasons 说明「是什么事实导致了这个结论」，逐条列清楚
- evidence 每一条都必须来自上面第 2 部分出现过的内容，禁止编造；
  费用标准工具返回 passed=false 时，对应证据的 passed 填 false；
  制度条款只是参考依据，不表示「符合/不符合」，其 passed 必须留空（null）
- evidence 里的数字必须与第 1、2 部分的数字完全一致，禁止四舍五入、改写或估算
- citations 只能引用第 2 部分制度检索真正返回过的条款，没有引用就留空，禁止编造条款编号
- confidence 填 0 到 1 之间的小数
- explanation 用自然语言写给员工看：结论、原因、建议怎么办，不要出现 JSON 或字段名
"""

# 抽取器的用户消息模板（payload 由 build_extraction_payload 生成）
_EXTRACT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", EXTRACT_SYSTEM_PROMPT),  # 固定的抽取规则
    ("human", "{payload}"),             # 本轮审核的过程记录
])

# 模块级缓存：抽取专用的大模型实例（关闭思考模式，避免每次重复初始化）
_extractor_llm = None


def _get_extractor_llm():
    """获取抽取专用的大模型（延迟初始化 + 复用；关闭思考模式换取 4 倍速度）。"""
    global _extractor_llm  # 声明要用模块级变量
    if _extractor_llm is None:  # 第一次调用才初始化
        from config.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL  # 延迟导入配置
        from langchain_openai import ChatOpenAI  # 延迟导入，避免模块加载时就依赖
        _extractor_llm = ChatOpenAI(  # 构造抽取专用模型
            api_key=LLM_API_KEY,       # 密钥（来自环境变量 / Streamlit Secrets）
            base_url=LLM_BASE_URL,     # 接入地址
            model=LLM_MODEL,           # 模型名
            temperature=0,             # 抽取要稳定，温度设 0
            extra_body={"enable_thinking": False},  # 关掉思考模式：抽取任务不需要深度推理，实测快 4 倍
        )
    return _extractor_llm  # 返回复用的实例


def build_extraction_payload(invoice_data, answer, trace=None, expense_form=None,
                             basic_check_text=None):
    """
    把一轮审核过程拼成抽取器要看的文本。

    参数：
        invoice_data: 发票识别结果字典
        answer: 审核助手给出的自然语言结论
        trace: AgentTrace（用来取工具的真实返回值）
        expense_form: 报销单数据（可选）
        basic_check_text: 基础校验结果文本（可选）

    返回：
        拼好的文本字符串
    """
    lines = ["【一、发票票面字段】"]  # 第一部分：发票字段
    fields = [  # 需要展示给抽取器的关键字段
        ("发票类型", "发票类型"), ("发票号码", "发票号码"), ("开票日期", "开票日期"),
        ("购买方", "购买方名称"), ("销售方", "销售方名称"),
        ("项目名称", "项目名称"), ("价税合计", "价税合计小写"),
    ]
    for label, key in fields:  # 逐个字段拼行
        value = invoice_data.get(key)  # 取字段值
        if value:  # 有值才拼
            lines.append(f"{label}：{value}")  # 拼一行
    details = invoice_data.get("明细列表")  # 商品明细
    if details:  # 有明细就带上
        lines.append(f"商品明细：{json.dumps(details, ensure_ascii=False)}")  # 明细转 JSON 文本

    lines.append("")  # 空行分隔
    lines.append("【二、工具真实返回值】")  # 第二部分：工具返回值
    if trace is not None and trace.tool_results:  # 有工具调用记录
        call_no = 0  # 工具调用序号
        for step in trace.tool_results:  # 遍历每一次工具返回
            call_no += 1  # 序号递增
            lines.append(f"[工具{call_no}] {step.tool_name} 返回：")  # 工具名
            lines.append(json.dumps(step.tool_result, ensure_ascii=False))  # 返回值原文
    else:  # 没有工具调用记录
        lines.append("（本次审核没有调用工具）")  # 明确说明，避免抽取器凭空补证据

    if basic_check_text:  # 有基础校验结果就带上
        lines.append("")  # 空行分隔
        lines.append("【三、系统基础合规校验结果】")  # 第三部分
        lines.append(str(basic_check_text).strip())  # 基础校验明细

    if expense_form:  # 有报销单就带上
        lines.append("")  # 空行分隔
        lines.append("【四、员工填写的报销单】")  # 第四部分
        lines.append(build_agent_text(expense_form))  # 报销单文本

    lines.append("")  # 空行分隔
    lines.append("【五、审核助手的自然语言结论】")  # 最后一部分：自然语言结论
    lines.append((answer or "").strip() or "（无结论）")  # 结论原文

    return "\n".join(lines)  # 拼成完整文本


def extract_structured_verdict(invoice_data, answer, trace=None, expense_form=None,
                               basic_check_text=None):
    """
    用 schema 约束生成，把一轮审核过程抽成 AuditVerdict。

    返回：
        AuditVerdict 对象；抽取失败时返回 None（调用方自行兜底）
    """
    payload = build_extraction_payload(  # 拼装给抽取器看的文本
        invoice_data, answer, trace=trace, expense_form=expense_form,
        basic_check_text=basic_check_text,
    )
    try:  # 抽取失败不能阻断主流程
        extractor = _get_extractor_llm().with_structured_output(  # 关键：schema 约束
            AuditVerdict,          # 目标结构：verdict 字段只能是「通过 / 不通过」
            method="json_schema",  # 该端点实测只支持 json_schema 方式（function_calling 被思考模式挡住）
        )
        chain = _EXTRACT_PROMPT | extractor  # 提示词 → 结构化输出
        info("【结构化裁决】开始抽取结论字段...")  # 打日志
        result = chain.invoke({"payload": payload})  # 执行抽取
        data = verdict_to_dict(result) or {}  # 转成字典
        success(f"【结构化裁决】抽取完成，结论字段 = {data.get('verdict')}")  # 打日志
        # 打印完整的结构化裁决内容（JSON格式，方便调试）
        info("【结构化裁决】完整内容：")
        info(json.dumps(data, ensure_ascii=False, indent=2))
        return result  # 返回结构化对象
    except Exception as exc:  # 抽取异常
        error(f"【结构化裁决】抽取失败：{exc}")  # 打日志
        return None  # 交给调用方回退到文本解析


# ============================================================================
# 类：审查Agent（一场对话创建一个实例，循环调用chat方法）
# ============================================================================
class ReviewAgent:
    """
    财务审查Agent类

    使用方式：
        agent = ReviewAgent(invoice_data)  # 创建实例，绑定发票数据
        answer1 = agent.chat("请审核这张发票")  # 第1轮（只要自然语言）
        r = agent.audit()                       # 要结构化裁决 + 运行轨迹
        answer2 = agent.chat("为什么不通过？")   # 第2轮追问
    """

    def __init__(self, invoice_data, expense_form=None, basic_check_text=None,
                 employee=None, use_structured=True, recursion_limit=10):
        """
        初始化审查Agent

        参数：
            invoice_data: 发票数据字典（基础校验已通过）
            expense_form: 报销单数据字典（可选），填了报销单时一起注入提示词
            basic_check_text: 基础合规校验的结果文本（可选），从发票上传页咨询时会带上
            employee: 当前报销人字典（可选，含 name / department / level）。
                      职级决定费用标准档位，既写进提示词，也注入工具层做缺参兜底
            use_structured: 是否启用结构化裁决抽取（False 时 audit() 只返回自然语言结论）
            recursion_limit: ReAct 最大循环次数，防止死循环
        """
        # 保存发票数据，后续对话不用再传
        self.invoice_data = invoice_data
        # 保存报销单数据（没填报销单时为 None），供构建提示词使用
        self.expense_form = expense_form
        # 保存基础校验结果（没有时为 None），供构建提示词使用
        self.basic_check_text = basic_check_text
        # 保存当前报销人（没有时为 None）
        self.employee = employee

        # ---- 把发票/报销单的关键参数注入工具层，防止Agent自己填错 ----
        # 金额从发票识别结果取，城市和天数从报销单取
        _ctx_employee = dict(employee) if employee else {}
        try:
            _amt = float(invoice_data.get("价税合计小写", 0) or 0)
        except (ValueError, TypeError):
            _amt = 0.0
        _ctx_employee["invoice_amount"] = _amt
        if expense_form:
            _ctx_employee["expense_city"] = expense_form.get("城市") or expense_form.get("出差城市") or ""
            # 住宿费取晚数字段，其他费用取天数
            _days = expense_form.get("住宿晚数") or expense_form.get("天数") or 1
            try:
                _ctx_employee["expense_days"] = int(_days)
            except (ValueError, TypeError):
                _ctx_employee["expense_days"] = 1
        set_audit_context(_ctx_employee)
        # 是否启用结构化裁决
        self.use_structured = use_structured
        # ReAct 最大步数
        self.recursion_limit = recursion_limit

        # 对话历史，类内部维护，用户不用管
        self.history = []

        # 最近一次运行的轨迹（供界面展示工具调用过程）
        self.last_trace = None

        # 第1步：初始化大模型（复用RAGLLM里的实例，不重复初始化）
        default_rag_llm.initialize()
        llm = default_rag_llm.llm

        # 第2步：构建系统提示词（把发票信息注入进去）
        system_prompt = build_system_prompt(invoice_data, expense_form=expense_form,
                                            basic_check_text=basic_check_text, employee=employee)

        # 第3步：构建提示词模板
        # 格式：system + MessagesPlaceholder（对话历史）
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])

        # 第4步：创建ReAct Agent实例（创建一次，后续反复使用）
        # 这里不带 response_format：审核Agent负责调查与解释，结构化由后面的抽取器负责
        self.agent = create_react_agent(
            model=llm,
            tools=AGENT_TOOLS,
            prompt=prompt,
        )

        info(f"【ReviewAgent】实例创建完成，绑定发票号码: {invoice_data.get('发票号码', '未知')}")

    def chat(self, user_message, on_event=None):
        """
        与Agent进行一轮对话（只返回自然语言回答，不做结构化抽取）

        参数：
            user_message: 用户输入的消息
            on_event: 可选事件回调，用于界面实时渲染运行过程

        返回：
            Agent的回答字符串
        """
        # 组装消息列表：历史消息 + 当前用户消息
        all_messages = self.history + [("user", user_message)]

        # 执行Agent（收集轨迹）
        trace = run_agent_stream(self.agent, all_messages, recursion_limit=self.recursion_limit, on_event=on_event)

        # 更新历史：把当前用户消息和Agent回答都加进去
        self.history.append(("user", user_message))
        self.history.append(("assistant", trace.final_answer))

        # 记住轨迹，界面可以取来自用
        self.last_trace = trace

        return trace.final_answer

    def audit(self, user_message=None, on_event=None):
        """
        跑一轮完整审核：ReAct调查 → schema约束抽取结构化裁决

        参数：
            user_message: 审核指令，默认「请审核这张发票，给出审核结论。」
            on_event: 可选事件回调，用于界面实时渲染运行过程

        返回：
            {
                "verdict": 结构化裁决字典（含 verdict / reasons / evidence / citations / confidence / explanation）
                "answer":  自然语言结论文本
                "trace":   AgentTrace 运行轨迹
            }
        """
        if user_message is None:  # 没传指令就用默认的
            user_message = "请审核这张发票，给出审核结论。"

        # 第1步：ReAct调查（真实调用工具，过程记进 trace）
        trace = run_agent_stream(
            self.agent,
            self.history + [("user", user_message)],
            recursion_limit=self.recursion_limit,
            on_event=on_event,
        )
        answer = trace.final_answer  # 自然语言结论

        # 第2步：把「发票 + 工具真实返回值 + 自然语言结论」抽成结构化裁决
        if self.use_structured:  # 启用了结构化才抽
            structured = extract_structured_verdict(
                self.invoice_data, answer, trace=trace,
                expense_form=self.expense_form, basic_check_text=self.basic_check_text,
            )
            trace.structured = verdict_to_dict(structured) or {}  # 记进轨迹
            if trace.structured:  # 抽取成功
                step = trace.add(STEP_STRUCTURED, text=trace.structured.get("verdict", ""))  # 补一步轨迹
                _emit(on_event, {"type": STEP_STRUCTURED, "step": step.step, "data": trace.structured})  # 推给界面

        # 更新对话历史
        self.history.append(("user", user_message))
        self.history.append(("assistant", answer))

        # 记住轨迹
        self.last_trace = trace

        return {"verdict": trace.structured or {}, "answer": answer, "trace": trace}

    def reset(self):
        """重置对话历史（开始一场新对话）"""
        self.history = []
        info("【ReviewAgent】对话历史已重置")


# ============================================================================
# 便捷函数：单轮审核（上传发票后自动调用）
# ============================================================================
def review_invoice(invoice_data, expense_form=None, basic_check_text=None, employee=None):
    """
    对发票进行单轮深度审核（便捷函数）

    内部创建ReviewAgent实例，发一句"请审核这张发票"，返回结果。
    如果需要多轮对话，请直接使用ReviewAgent类。

    参数：
        invoice_data: 发票数据字典（基础校验已通过）
        expense_form: 报销单数据（可选）
        basic_check_text: 基础校验结果文本（可选）

    返回：
        {
            "verdict": 结构化裁决字典（verdict 字段是确定的枚举值）
            "answer":  自然语言结论文本
            "agent":   ReviewAgent实例（供后续多轮对话使用）
            "trace":   AgentTrace 运行轨迹
        }
    """
    agent = ReviewAgent(invoice_data, expense_form=expense_form,
                        basic_check_text=basic_check_text, employee=employee)
    result = agent.audit()  # 一轮完整审核（含结构化抽取）
    return {
        "verdict": result["verdict"],  # 结构化裁决
        "answer": result["answer"],    # 自然语言结论
        "agent": agent,                # 返回实例，用户可以继续调用agent.chat()追问
        "trace": result["trace"],      # 运行轨迹
    }


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    # 测试用发票数据
    test_invoice = {
        "发票类型": "电子发票（普通发票）",
        "发票号码": "26127000000370193970",
        "开票日期": "2026年07月27日",
        "购买方名称": "泗阳县益亿再生资源有限公司",
        "销售方名称": "去哪儿网（天津）国际旅行社有限公司武清分公司",
        "项目名称": "*生产生活服务*代订房费",
        "价税合计小写": "2584.00"
    }

    print("=" * 60)
    print("测试：一轮审核，同时拿到自然语言结论和结构化裁决")
    print("=" * 60)

    # 一场对话创建一个Agent实例
    agent = ReviewAgent(test_invoice)

    # 跑一轮完整审核
    result = agent.audit()

    print("\n----- 自然语言结论 -----")
    print(result["answer"])

    print("\n----- 结构化裁决（结论是字段，不是猜出来的）-----")
    print(json.dumps(result["verdict"], ensure_ascii=False, indent=2))

    print("\n----- 运行轨迹（每一步工具调用的真实返回值）-----")
    for step in result["trace"].steps:
        print(f"  [{step.step}] {step.kind} {step.tool_name} {step.text[:60]}")

    print("\n----- 第2轮：追问 -----")
    print(agent.chat("如果超标了怎么办？"))
