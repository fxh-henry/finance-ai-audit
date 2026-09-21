# -*- coding: utf-8 -*-
# ============================================================================
# 审查Agent（agent/review_agent.py）
# ============================================================================
# 作用：接收基础校验通过的发票数据，调用工具进行深度审核，支持多轮对话
# 技术：LangGraph create_react_agent（ReAct模式：思考→行动→观察→回答）
# 使用方式：
#   agent = ReviewAgent(invoice_data)   # 一场对话创建一个实例
#   print(agent.chat("请审核这张发票"))  # 第1轮
#   print(agent.chat("为什么不通过？"))   # 第2轮
#   print(agent.chat("那怎么办？"))      # 第3轮
# ============================================================================

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
from agent.tools.audit_tools import AGENT_TOOLS

# 导入日志工具，打印Agent运行过程
from rag.utils.logger import info, success, warn, error
# 导入报销单工具：把报销单拼成提示词可用的文字（分类扩展字段、明细等）
from utils.expense_form import build_agent_text


# ============================================================================
# 函数：构建系统提示词
# ============================================================================
def build_system_prompt(invoice_data, expense_form=None, basic_check_text=None):
    """
    根据发票数据（以及可选的报销单、基础校验结果）构建Agent的系统提示词

    作用：把发票 / 报销单 / 基础校验结果注入到提示词里，让Agent知道在审核什么

    参数：
        invoice_data: 识别出的发票数据字典
        expense_form: 报销单数据字典（可选），填了报销单时一起注入
        basic_check_text: 基础合规校验的结果文本（可选）。从「发票上传」页咨询时
                          会把这次的基础校验明细一起带进来，Agent 才能针对不通过项解释

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
            {form_block}{basic_block}
            【审核规则】
            {rule_one}
            2. 根据发票的项目名称，判断费用类型（住宿费/餐饮费/交通费/办公费等）
            3. 住宿费、交通费等有明确标准的费用，调用【费用标准校验工具】检查是否超标
            4. 需要查找制度依据时，调用【RAG制度检索工具】获取相关条款
            5. 综合所有工具返回的结果，给出审核结论
            {extra_rules}{basic_rules}
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
# 函数：带日志的Agent执行（stream模式，逐步打印运行过程）
# ============================================================================
def _run_agent_with_logging(agent, messages, recursion_limit=10):
    """
    执行Agent并逐步打印运行过程（思考过程、工具调用、工具返回）

    用stream模式而不是invoke模式，stream会逐步产出每一步的结果。

    参数：
        agent: LangGraph Agent实例
        messages: 消息列表，格式[("user", "..."), ("assistant", "...")]
        recursion_limit: 最大循环次数，防止死循环

    返回：
        Agent的最终回答字符串
    """
    info("=" * 60)
    info("【Agent】开始执行")
    info("=" * 60)

    final_answer = ""
    step_count = 0  # 记录执行到第几步

    # stream模式：逐步产出结果，每一步是一个chunk
    # chunk格式：{节点名: 节点输出}
    # 节点名只有两种：
    #   "agent" → LLM思考节点，输出AIMessage
    #   "tools" → 工具执行节点，输出ToolMessage
    for chunk in agent.stream(
        {"messages": messages},
        config={"recursion_limit": recursion_limit}
    ):
        step_count += 1

        # 遍历这个chunk里的所有节点（通常只有一个节点）
        for node_name, node_output in chunk.items():
            # 取出该节点产出的消息列表
            node_messages = node_output.get("messages", [])

            for msg in node_messages:
                # ============================================================
                # 情况1：节点是"agent" → LLM的思考/输出
                # ============================================================
                if node_name == "agent":
                    # AIMessage有tool_calls属性（即使是空列表）
                    # 用 msg.tool_calls 是否非空来判断是否调用工具
                    if msg.tool_calls:
                        # AI决定调用工具（可能同时调用多个）
                        for tc in msg.tool_calls:
                            info(f"【Agent第{step_count}步】LLM决定调用工具: {tc['name']}")
                            info(f"  调用参数: {tc['args']}")
                        # 如果AI在调用工具前有思考内容，也打印出来
                        if msg.content:
                            info(f"  LLM思考: {msg.content[:200]}")
                    else:
                        # tool_calls是空列表 → 这是AI的最终回答
                        final_answer = msg.content
                        success(f"【Agent第{step_count}步】LLM生成最终回答")

                # ============================================================
                # 情况2：节点是"tools" → 工具执行结果
                # ============================================================
                elif node_name == "tools":
                    # ToolMessage是工具返回的结果
                    tool_name = getattr(msg, "name", "未知工具")
                    success(f"【Agent第{step_count}步】工具[{tool_name}]执行完成")
                    # 只打印前150字，避免日志太长
                    result_preview = str(msg.content)[:150]
                    info(f"  返回结果: {result_preview}...")

    info("=" * 60)
    success(f"【Agent】执行完成，共{step_count}步")
    info("=" * 60)

    return final_answer


# ============================================================================
# 类：审查Agent（一场对话创建一个实例，循环调用chat方法）
# ============================================================================
class ReviewAgent:
    """
    财务审查Agent类

    使用方式：
        agent = ReviewAgent(invoice_data)  # 创建实例，绑定发票数据
        answer1 = agent.chat("请审核这张发票")  # 第1轮
        answer2 = agent.chat("为什么不通过？")   # 第2轮
        answer3 = agent.chat("那怎么办？")      # 第3轮
    """

    def __init__(self, invoice_data, expense_form=None, basic_check_text=None):
        """
        初始化审查Agent

        参数：
            invoice_data: 发票数据字典（基础校验已通过）
            expense_form: 报销单数据字典（可选），填了报销单时一起注入提示词
            basic_check_text: 基础合规校验的结果文本（可选），从发票上传页咨询时会带上
        """
        # 保存发票数据，后续对话不用再传
        self.invoice_data = invoice_data
        # 保存报销单数据（没填报销单时为 None），供构建提示词使用
        self.expense_form = expense_form
        # 保存基础校验结果（没有时为 None），供构建提示词使用
        self.basic_check_text = basic_check_text

        # 对话历史，类内部维护，用户不用管
        self.history = []

        # 第1步：初始化大模型（复用RAGLLM里的实例，不重复初始化）
        default_rag_llm.initialize()
        llm = default_rag_llm.llm

        # 第2步：构建系统提示词（把发票信息注入进去）
        system_prompt = build_system_prompt(invoice_data, expense_form=expense_form, basic_check_text=basic_check_text)

        # 第3步：构建提示词模板
        # 格式：system + MessagesPlaceholder（对话历史）
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])

        # 第4步：创建ReAct Agent实例（创建一次，后续反复使用）
        self.agent = create_react_agent(
            model=llm,
            tools=AGENT_TOOLS,
            prompt=prompt,
        )

        info(f"【ReviewAgent】实例创建完成，绑定发票号码: {invoice_data.get('发票号码', '未知')}")

    def chat(self, user_message):
        """
        与Agent进行一轮对话

        参数：
            user_message: 用户输入的消息

        返回：
            Agent的回答字符串
        """
        # 组装消息列表：历史消息 + 当前用户消息
        all_messages = self.history + [("user", user_message)]

        # 执行Agent（带日志）
        answer = _run_agent_with_logging(self.agent, all_messages)

        # 更新历史：把当前用户消息和Agent回答都加进去
        self.history.append(("user", user_message))
        self.history.append(("assistant", answer))

        return answer

    def reset(self):
        """重置对话历史（开始一场新对话）"""
        self.history = []
        info("【ReviewAgent】对话历史已重置")


# ============================================================================
# 便捷函数：单轮审核（上传发票后自动调用）
# ============================================================================
def review_invoice(invoice_data, expense_form=None, basic_check_text=None):
    """
    对发票进行单轮深度审核（便捷函数）

    内部创建ReviewAgent实例，发一句"请审核这张发票"，返回结果。
    如果需要多轮对话，请直接使用ReviewAgent类。

    参数：
        invoice_data: 发票数据字典（基础校验已通过）

    返回：
        {
            "answer": 审核结果字符串,
            "agent": ReviewAgent实例（供后续多轮对话使用）
        }
    """
    agent = ReviewAgent(invoice_data, expense_form=expense_form, basic_check_text=basic_check_text)
    answer = agent.chat("请审核这张发票，给出审核结论。")
    return {
        "answer": answer,
        "agent": agent  # 返回实例，用户可以继续调用agent.chat()追问
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
    print("测试：一场对话，多轮交流")
    print("=" * 60)

    # 一场对话创建一个Agent实例
    agent = ReviewAgent(test_invoice)

    # 第1轮：审核
    print("\n----- 第1轮：请审核这张发票 -----")
    print(agent.chat("请审核这张发票，给出审核结论。"))

    # 第2轮：追问
    print("\n----- 第2轮：如果超标了怎么办？ -----")
    print(agent.chat("如果超标了怎么办？"))

    # 第3轮：继续追问
    print("\n----- 第3轮：需要准备什么材料？ -----")
    print(agent.chat("需要准备什么材料？"))
