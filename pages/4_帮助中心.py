# -*- coding: utf-8 -*-
"""帮助中心：制度问答（RAG） + 我的发票咨询（ReviewAgent），左侧制度文档导航"""
import importlib
from pathlib import Path

import streamlit as st

from llm.RAGLLM import RAGLLM
from agent.consult_agent import ConsultAgent
from utils.expense_form import invoice_data_from_detail

import database.db as _db
_db = importlib.reload(_db)
list_folder_invoices = _db.list_folder_invoices
get_invoice_detail = _db.get_invoice_detail
list_expense_forms = _db.list_expense_forms

st.set_page_config(page_title="帮助中心", layout="wide")

# 知识库目录路径
KNOWLEDGE_BASE_DIR = Path(__file__).parent.parent / "rag" / "knowledge_base"


def list_knowledge_docs():
    """读取知识库目录下所有.md文件，返回 [(文件名, 内容), ...]"""
    docs = []
    if KNOWLEDGE_BASE_DIR.exists():
        for md_file in sorted(KNOWLEDGE_BASE_DIR.glob("*.md")):
            if md_file.name == "README.md":
                continue  # 跳过README
            content = md_file.read_text(encoding="utf-8")
            docs.append((md_file.stem, content))
    return docs


def render_doc_navigator(docs, key_prefix):
    """渲染左侧制度文档导航，返回选中的文档内容"""
    st.markdown("**制度文档**")
    doc_names = [name for name, _ in docs]
    if not doc_names:
        st.caption("暂无文档")
        return None
    selected = st.radio(
        "选择文档查看原文",
        doc_names,
        key=f"{key_prefix}_doc_select",
        label_visibility="collapsed"
    )
    if selected:
        for name, content in docs:
            if name == selected:
                return content
    return None


def render_pending_consult():
    """Tab2-A：来自「发票上传」页的咨询（自动带着发票识别信息 + 基础校验结果去问 Agent）"""
    pending = st.session_state.get("pending_consult") or {}  # 咨询材料

    with st.container(border=True):  # 顶部：正在咨询的发票摘要
        st.markdown("**正在咨询的发票（来自「发票上传」或「报销单详情」）**")
        c1, c2, c3 = st.columns(3)  # 三列展示摘要
        with c1:  # 第一列
            st.write("发票号码：", pending.get("invoice_number") or "-")  # 发票号码
            st.write("销售方：", pending.get("seller_name") or "-")  # 销售方
        with c2:  # 第二列
            st.write("价税合计：", pending.get("total_amount") or "-")  # 金额
            st.write("基础校验结论：", pending.get("final_result") or "-")  # 基础校验结论
        with c3:  # 第三列：返回入口
            if st.button("结束咨询，返回上传页", use_container_width=True, key="pending_back"):  # 返回按钮
                for stale_key in ("pending_consult", "pending_consult_history", "pending_consult_agent", "pending_consult_asked"):
                    st.session_state.pop(stale_key, None)  # 清掉这次咨询的全部状态
                st.switch_page("pages/1_发票上传.py")  # 跳回发票上传页

    with st.expander("查看发票识别信息与基础校验结果", expanded=False):  # 折叠面板看原始材料
        st.markdown("**发票识别信息**")  # 小标题
        st.json(pending.get("invoice_data") or {})  # 识别结果（JSON 树）
        st.markdown("**基础校验结果**")  # 小标题
        st.code(pending.get("basic_check_text") or "", language=None)  # 逐条校验明细

    agent = st.session_state.get("pending_consult_agent")  # 复用已创建的 Agent，避免每次重跑都初始化大模型
    if agent is None:  # 还没创建
        agent = ConsultAgent(  # 创建绑定了「发票 + 基础校验结果」的咨询 Agent
            pending.get("invoice_data") or {},  # 发票识别数据
            basic_check_text=pending.get("basic_check_text"),  # 基础校验结果（注入到系统提示词）
        )
        st.session_state["pending_consult_agent"] = agent  # 存进会话，后续重跑直接复用

    history = st.session_state.get("pending_consult_history")  # 对话历史
    if history is None:  # 第一次进来还没有历史
        history = []  # 建一个空列表
        st.session_state["pending_consult_history"] = history  # 存进会话

    if not st.session_state.get("pending_consult_asked"):  # 第一次进来：自动替用户发起第一问
        first_question = "请结合基础校验结果，判断这张发票能不能报销，并说明具体原因、我还需要补什么材料。"  # 自动提问内容
        history.append({"role": "user", "content": first_question})  # 先记下问题
        with st.spinner("AI 正在结合发票信息和基础校验结果分析..."):  # 等待提示
            answer = agent.chat(first_question)  # 让 Agent 回答
        history.append({"role": "assistant", "content": answer})  # 记下回答
        st.session_state["pending_consult_asked"] = True  # 标记已自动问过，避免每次重跑都重问

    for msg in history:  # 统一渲染对话历史
        with st.chat_message(msg["role"]):  # 按角色生成气泡
            st.markdown(msg["content"])  # 显示内容

    user_input = st.chat_input("针对这张发票继续提问", key="tab2_pending_chat")  # 继续追问的输入框
    if user_input:  # 用户确实发了消息
        with st.chat_message("user"):  # 用户气泡
            st.markdown(user_input)  # 显示问题
        history.append({"role": "user", "content": user_input})  # 记进历史
        with st.spinner("AI 正在思考..."):  # 等待提示
            answer = agent.chat(user_input)  # 让 Agent 回答
        with st.chat_message("assistant"):  # AI 气泡
            st.markdown(answer)  # 显示回答
        history.append({"role": "assistant", "content": answer})  # 记进历史
        st.rerun()  # 立刻重跑，让新消息渲染出来


def render_folder_consult():
    """Tab2-B：常规入口 —— 从票夹里选一张发票来咨询"""
    invoices = list_folder_invoices()  # 票夹里的发票
    if not invoices:  # 票夹是空的
        st.warning("票夹里还没有发票，请先上传发票并存入票夹。")  # 黄色提示
        if st.button("去上传发票", type="primary"):  # 引导按钮
            st.switch_page("pages/1_发票上传.py")  # 跳转到发票上传页
        return  # 没有数据就不往下渲染了

    inv_options = [inv["id"] for inv in invoices]  # 下拉框的值用发票 id
    inv_label_map = {}  # 发票 id → 下拉框显示文字
    for inv in invoices:  # 逐张发票拼显示文字
        inv_label_map[inv["id"]] = (
            f"{inv.get('invoice_number') or '无号码'} · "
            f"{(inv.get('seller_name') or '未识别销售方')[:20]} · "
            f"¥{float(inv.get('total_amount') or 0):,.2f}"
        )
    default_idx = 0  # 默认选中第一张
    if st.session_state.selected_invoice_id in inv_options:  # 之前选过就沿用
        default_idx = inv_options.index(st.session_state.selected_invoice_id)  # 找回原来的位置

    selected_inv_id = st.selectbox(  # 发票选择框
        "选择要咨询的发票（从票夹中选择）",  # 控件标题
        inv_options,  # 可选值
        index=default_idx,  # 默认选中项
        format_func=lambda v: inv_label_map.get(v, str(v)),  # 显示成“号码 · 销售方 · 金额”
        key="tab2_invoice_select"  # 控件 key
    )

    if selected_inv_id != st.session_state.selected_invoice_id:  # 换了发票：重置 Agent 和对话
        st.session_state.selected_invoice_id = selected_inv_id  # 记住新选的发票
        st.session_state.invoice_review_agent = None  # 丢掉旧 Agent
        st.session_state.invoice_chat_history = []  # 清空对话

    detail = get_invoice_detail(selected_inv_id)  # 查发票详情
    invoice_data = invoice_data_from_detail(detail)  # 转成中文键字典
    stored_audit = detail.get("audit") or {}  # 发票详情里存的审核记录

    all_forms = list_expense_forms()  # 所有报销单
    related_forms = [f for f in all_forms if f.get("invoice_id") == selected_inv_id]  # 这张发票对应的报销单

    with st.container(border=True):  # 发票信息摘要卡
        st.markdown("**当前发票信息**")  # 小标题
        c1, c2, c3 = st.columns(3)  # 三列
        with c1:  # 第一列
            st.write("发票号码：", detail.get("invoice_number") or "-")  # 发票号码
            st.write("开票日期：", detail.get("invoice_date") or "-")  # 开票日期
        with c2:  # 第二列
            st.write("销售方：", detail.get("seller_name") or "-")  # 销售方
            st.write("价税合计：", f"¥{float(detail.get('total_amount') or 0):,.2f}")  # 金额
        with c3:  # 第三列
            st.write("费用类型：", detail.get("expense_type") or "未分类")  # 费用类型
            if related_forms:  # 已经有报销单
                form = related_forms[0]  # 取第一张（1 张发票只会有 1 张单）
                st.write("报销单号：", form.get("form_no") or "-")  # 报销单号
                st.write("单据状态：", form.get("status") or "-")  # 单据状态
            else:  # 还没填报销单
                st.write("报销单：", "尚未填写")  # 提示

    if st.session_state.invoice_review_agent is None:  # 懒加载：选了发票才创建 Agent
        st.session_state.invoice_review_agent = ConsultAgent(  # 创建咨询 Agent
            invoice_data,  # 发票数据
            basic_check_text=stored_audit.get("detail_text"),  # 把当初存库的校验明细也带上
        )

    left_col, right_col = st.columns([1, 3])  # 左文档导航、右对话

    with left_col:  # 左侧
        doc_content = render_doc_navigator(knowledge_docs, "tab2")  # 渲染制度文档导航
        if doc_content:  # 选中了文档
            with st.expander("查看文档原文", expanded=False):  # 折叠面板
                st.markdown(doc_content)  # 显示文档原文

    with right_col:  # 右侧
        for msg in st.session_state.invoice_chat_history:  # 渲染对话历史
            with st.chat_message(msg["role"]):  # 气泡
                st.markdown(msg["content"])  # 内容

        if not st.session_state.invoice_chat_history:  # 还没有对话
            st.info("已加载当前发票信息，您可以针对这张发票提问，例如：这张发票能报销吗？费用类型选什么？")  # 引导提示

        user_input = st.chat_input(  # 提问输入框
            "针对这张发票提问，例如：这张发票能报销吗？费用类型应该选什么？",  # 占位提示
            key="tab2_chat"  # 控件 key
        )

        if user_input:  # 有提问
            with st.chat_message("user"):  # 用户气泡
                st.markdown(user_input)  # 显示问题
            st.session_state.invoice_chat_history.append({"role": "user", "content": user_input})  # 记进历史

            with st.spinner("AI正在结合发票信息和报销制度分析..."):  # 等待提示
                answer = st.session_state.invoice_review_agent.chat(user_input)  # 让 Agent 回答

            with st.chat_message("assistant"):  # AI 气泡
                st.markdown(answer)  # 显示回答
            st.session_state.invoice_chat_history.append({"role": "assistant", "content": answer})  # 记进历史
            st.rerun()  # 立刻重跑



# 初始化 session_state
if "help_rag_llm" not in st.session_state:
    rag_llm = RAGLLM()
    rag_llm.initialize()
    st.session_state.help_rag_llm = rag_llm

if "help_chat_history" not in st.session_state:
    st.session_state.help_chat_history = []

if "invoice_chat_history" not in st.session_state:
    st.session_state.invoice_chat_history = []

if "invoice_review_agent" not in st.session_state:
    st.session_state.invoice_review_agent = None

if "selected_invoice_id" not in st.session_state:
    st.session_state.selected_invoice_id = None

# 页头
with st.container(key="pagehead"):
    head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")
    with head_l:
        st.header("帮助中心")
        st.caption("报销制度智能问答 + 针对发票的专属咨询")
    with head_r:
        if st.button("清空当前对话", use_container_width=True):
            st.session_state.help_chat_history = []
            st.session_state.invoice_chat_history = []
            st.session_state.invoice_review_agent = None
            st.rerun()
    st.divider()

# 两个Tab
default_tab = "我的发票咨询" if st.session_state.get("pending_consult") else None  # 从上传页跳过来时默认打开 Tab2
tab1, tab2 = st.tabs(["制度问答", "我的发票咨询"], default=default_tab)  # 用 default 指定默认页签

# 加载知识库文档列表（两个tab共用）
knowledge_docs = list_knowledge_docs()

# ============================================================================
# Tab1：制度问答
# ============================================================================
with tab1:
    left_col, right_col = st.columns([1, 3])

    with left_col:
        doc_content = render_doc_navigator(knowledge_docs, "tab1")
        if doc_content:
            with st.expander("查看文档原文", expanded=False):
                st.markdown(doc_content)

    with right_col:
        # 常见问题（只在没有对话历史时显示）
        if not st.session_state.help_chat_history:
            st.markdown("**常见问题**")
            q1, q2, q3, q4 = st.columns(4)
            quick_questions = [
                "住宿费报销标准是多少",
                "业务招待费怎么报销",
                "差旅费报销需要哪些附件",
                "发票重复报销怎么处理"
            ]
            for i, col in enumerate([q1, q2, q3, q4]):
                with col:
                    if st.button(quick_questions[i], use_container_width=True, key=f"tab1_q{i}"):
                        st.session_state.quick_q_tab1 = quick_questions[i]
            st.divider()

        # 对话历史
        for msg in st.session_state.help_chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # 用户输入
        user_input = st.chat_input("输入您的问题，例如：住宿费超标了怎么办？", key="tab1_chat")
        if "quick_q_tab1" in st.session_state:
            user_input = st.session_state.pop("quick_q_tab1")

        if user_input:
            with st.chat_message("user"):
                st.markdown(user_input)
            st.session_state.help_chat_history.append({"role": "user", "content": user_input})

            with st.spinner("正在检索知识库并生成回答..."):
                answer = st.session_state.help_rag_llm.ask(user_input)

            with st.chat_message("assistant"):
                st.markdown(answer)
            st.session_state.help_chat_history.append({"role": "assistant", "content": answer})
            st.rerun()

# ============================================================================
# Tab2：我的发票咨询（两个入口：从票夹选发票 / 从「发票上传」页带着材料过来）
# ============================================================================
with tab2:
    if st.session_state.get("pending_consult"):  # 从发票上传页跳过来的：优先渲染这份咨询
        render_pending_consult()
    else:  # 常规入口：从票夹里选一张发票咨询
        render_folder_consult()
