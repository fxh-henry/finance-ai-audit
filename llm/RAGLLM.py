from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from rag.utils import success, info, error
from rag import get_rag_service
from config.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


class RAGLLM:
    def __init__(self):
        """构造函数，不自动加载模型，调用initialize才真正初始化"""
        self.llm = None
        self.rag_chain = None

    def initialize(self):
        """手动初始化大模型，延迟加载"""
        info("开始初始化 LLM 大模型...")
        self.llm = ChatOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            temperature=0.1,
            streaming=True,
            extra_body={
                "enable_thinking": True
            }
        )
        success("LLM大模型初始化成功")

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是知识库问答助手。
                            严格参考【参考上下文】回答用户问题。
                            如果上下文中没有答案，如实告知不知道，禁止编造幻觉。
                            【参考上下文】{context}"""),
            ("human", "{question}")
        ])
        self.rag_chain = prompt | self.llm | StrOutputParser()

    @staticmethod
    def build_context_from_retrieval_result(retrieval_result):
        """拼接检索结果为上下文字符串"""
        context_parts = []
        for doc, score in retrieval_result:
            context_parts.append(doc["content"])
        return "\n---\n".join(context_parts)

    def ask(self, user_question, top_k=3):
        """
        完整的RAG问答：检索 + 生成，一步到位

        内部流程：
            1. 调用RAG检索相关知识库内容（返回拼接好的上下文字符串）
            2. 调用大模型生成回答
            3. 返回回答字符串

        参数：
            user_question: 用户问题
            top_k: 检索返回的文档数量，默认3

        返回：
            大模型生成的回答字符串
        """
        if self.rag_chain is None:
            raise RuntimeError("请先调用 .initialize() 初始化模型！")

        # 第1步：调用RAG检索（返回的是拼接好的上下文字符串，带[资料1]标记）
        info(f"[RAG问答] 用户问题: {user_question}")
        rag = get_rag_service()
        context = rag.retrieve(user_question, top_k=top_k)
        info(f"[RAG问答] 检索完成，上下文长度 {len(context)} 字符")

        # 第2步：调用大模型生成回答
        info("[RAG问答] 大模型生成回答中...")
        answer = self.rag_chain.invoke({
            "context": context,
            "question": user_question
        })
        success("[RAG问答] 回答生成完成")

        return answer

    def print_result(self, user_question, retrieval_result):
        """流式输出（用于调试，需要外部先检索好结果传入）"""
        if self.rag_chain is None:
            raise RuntimeError("请先调用 .initialize() 初始化模型！")

        context = self.build_context_from_retrieval_result(retrieval_result)
        print("====输出回答====")
        for chunk in self.rag_chain.stream({
            "context": context,
            "question": user_question
        }):
            print(chunk, end="", flush=True)


# 对外暴露一个默认实例，方便简单导入使用
default_rag_llm = RAGLLM()
