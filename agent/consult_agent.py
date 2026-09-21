# -*- coding: utf-8 -*-
"""
帮助中心咨询Agent（ConsultAgent）
与后端审核Agent（ReviewAgent）的区别：
- ReviewAgent：目标是"裁决"，给出通过/不通过，调用费用标准工具
- ConsultAgent：目标是"解答"，结合发票信息和制度文档回答用户疑问

使用场景：帮助中心 → 发票咨询页面
"""
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage

from config.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from rag import get_rag_service
from rag.utils.logger import info, success, warn, error


class ConsultAgent:
    """
    财务咨询Agent：结合发票信息、基础校验结果和报销制度，解答用户疑问

    与ReviewAgent的区别：
    - 不调用费用标准校验工具（那是审核用的）
    - 只在需要时调用RAG检索制度文档
    - 系统提示词侧重"解释、解答"，不是"裁决"
    """

    def __init__(self, invoice_data, basic_check_text=None):
        """
        初始化咨询Agent

        参数：
            invoice_data: 发票识别数据（字典）
            basic_check_text: 基础校验结果文本（可选，用于让Agent知道校验情况）
        """
        self.invoice_data = invoice_data or {}
        self.basic_check_text = basic_check_text or ""
        self.chat_history = []  # 对话历史，支持多轮

        # 初始化LLM（延迟加载，第一次chat时才真正初始化）
        self.llm = None
        self.chain = None

        info(f"[咨询Agent] 初始化完成，发票号码: {self.invoice_data.get('发票号码', '未知')}")

    def _initialize(self):
        """延迟初始化LLM和链（第一次chat时调用）"""
        if self.llm is not None:
            return

        info("[咨询Agent] 初始化LLM...")
        self.llm = ChatOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            temperature=0.3,  # 咨询场景温度稍高，回答更自然
            streaming=False,
        )

        # 构建系统提示词：把发票信息和基础校验结果都放进去
        system_prompt = self._build_system_prompt()

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{question}"),
        ])

        self.chain = prompt | self.llm | StrOutputParser()
        success("[咨询Agent] LLM初始化完成")

    def _build_system_prompt(self):
        """
        构建系统提示词：把发票信息、基础校验结果都构建进去
        """
        # 从发票数据提取关键字段
        invoice_type = self.invoice_data.get("发票类型", "未知")
        invoice_no = self.invoice_data.get("发票号码", "未知")
        invoice_date = self.invoice_data.get("开票日期", "未知")
        buyer = self.invoice_data.get("购买方名称", "未知")
        seller = self.invoice_data.get("销售方名称", "未知")
        item_name = self.invoice_data.get("项目名称", "未知")
        total_amount = self.invoice_data.get("价税合计小写", "未知")

        # 打印发票数据日志
        info(f"[咨询Agent] 系统提示词-发票信息: 项目名称='{item_name}', 销售方='{seller}', 发票号码='{invoice_no}'")
        info(f"[咨询Agent] invoice_data顶层keys: {list(self.invoice_data.keys())}")
        if item_name == "未知":
            warn(f"[咨询Agent] 警告: 项目名称为'未知'！明细列表第一条='{(self.invoice_data.get('明细列表') or [{}])[0].get('项目名称', '无')}'")

        # 基础校验结果段落
        basic_block = ""
        if self.basic_check_text:
            basic_block = f"""
            【系统已执行的基础合规校验结果】
            {self.basic_check_text}
            """

        system_prompt = f"""你是一位专业的财务咨询助手，负责解答员工关于发票报销的疑问。
            【当前咨询的发票信息】
            - 发票类型：{invoice_type}
            - 发票号码：{invoice_no}
            - 开票日期：{invoice_date}
            - 购买方：{buyer}
            - 销售方：{seller}
            - 项目名称：{item_name}
            - 价税合计：{total_amount}元
            {basic_block}
            【工作方式】
            1. 结合上面的发票信息和基础校验结果，解答用户的问题
            2. 如果问题涉及报销制度、费用标准，调用RAG检索相关制度文档，引用时注明出处（如"根据《通用企业报销制度》第十四条"）
            3. 语气友好、耐心，用通俗的语言解释，不要用太专业的术语
            4. 如果基础校验有不通过项，先解释不通过的原因，再告诉用户怎么补救
            5. 不要直接说"通过"或"不通过"，那是审核员的工作；你的工作是解答疑问、提供建议
            6. 如果用户问的问题与当前发票无关，可以一般性地回答，但要说明"以下是通用解答，具体以您的发票为准"
            【RAG检索工具】
            当用户的问题涉及报销制度、费用标准、发票要求时，先检索知识库再回答。
            """
        return system_prompt

    def _retrieve_policy(self, question):
        """
        用RAG检索相关制度文档

        参数：
            question: 用户问题

        返回：
            检索到的制度文本（拼接好的字符串）
        """
        try:
            rag = get_rag_service()
            context = rag.retrieve(question, top_k=3)
            info(f"[咨询Agent] RAG检索完成，上下文长度 {len(context)} 字符")
            return context
        except Exception as e:
            warn(f"[咨询Agent] RAG检索失败: {e}")
            return ""

    def chat(self, question):
        """
        与用户对话（支持多轮）

        参数：
            question: 用户问题

        返回：
            Agent回答字符串
        """
        self._initialize()  # 确保LLM已初始化

        info(f"[咨询Agent] 用户问题: {question}")

        # 总是检索RAG：咨询场景围绕报销制度，检索结果作为上下文，LLM选择用或不用
        context = self._retrieve_policy(question)
        if context:
            question_with_context = f"""用户问题：{question}

【相关制度文档】
{context}

请结合以上制度文档回答用户问题，引用时注明出处。"""
        else:
            question_with_context = question

        # 调用LLM，传入对话历史
        try:
            answer = self.chain.invoke({
                "chat_history": self.chat_history,
                "question": question_with_context,
            })
            success("[咨询Agent] 回答生成完成")

            # 把这轮对话加入历史
            self.chat_history.append(HumanMessage(content=question))
            self.chat_history.append(AIMessage(content=answer))

            return answer

        except Exception as e:
            error(f"[咨询Agent] 生成回答失败: {e}")
            return f"抱歉，我暂时无法回答这个问题（{e}），请稍后再试或联系人工财务。"

    def get_history(self):
        """获取对话历史（用于页面展示）"""
        result = []
        for msg in self.chat_history:
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content})
        return result

    def clear_history(self):
        """清空对话历史"""
        self.chat_history = []
        info("[咨询Agent] 对话历史已清空")
