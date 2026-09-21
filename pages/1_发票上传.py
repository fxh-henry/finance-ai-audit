# -*- coding: utf-8 -*-
"""发票上传：上传发票 → 自动识别 → 基础合规校验（不调用Agent）→ 四个后续操作入口"""
import hashlib  # 用来给上传的文件算指纹，判断用户是否换了文件
import importlib  # 用来重新加载模块，避免 Streamlit 热重载时用到旧代码
import tempfile  # 用来生成临时文件（识别接口需要一个磁盘路径）
from datetime import datetime  # 用来记录校验时间

import streamlit as st  # 导入 Streamlit

from pipeline import run_basic_check_pipeline, format_for_user  # 只引入“识别 + 基础校验”流水线（本页不加载 Agent，页面打开更快）
from utils.audit_verdict import extract_verdict  # 引入结论解析工具
from utils.current_user import get_current_employee_id  # 轻量多员工

import database.db as _db  # 引入数据库模块（用于“存入票夹”）

_db = importlib.reload(_db)  # 强制重新加载 db 模块，保证用的是最新代码
save_invoice_to_folder = _db.save_invoice_to_folder  # 取出“保存发票到票夹”的函数备用


def build_audit_record(result, agent_answer=None):
    """
    把基础校验结果整理成可 JSON 存储的审核记录

    result: run_basic_check_pipeline 的返回（check_results 和 summary 在顶层）
    agent_answer: AI 咨询的回答（可选，本次流程一般为 None）
    """
    summary = result.get("summary") or {}  # 基础校验汇总

    if agent_answer:  # 有 AI 回答时，结论以 AI 为准
        final_result = extract_verdict(agent_answer) or summary.get("final_result") or "未审核"
    else:  # 没有 AI 回答：本次只跑到基础校验，结论就是基础校验的结论
        final_result = summary.get("final_result", "未审核")

    return {
        "final_result": final_result,  # 结论：通过 / 不通过 / 未审核
        "agent_answer": agent_answer,  # AI 回答原文（本流程一般为 None）
        "detail_text": format_for_user(result),  # 基础校验明细文本（发票详情页会展示）
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 校验时间
        "stage": "basic_check",  # 标记结论来源：基础校验（不是 Agent 深度审核）
    }


def _notify(success, message):  # 轻提示：显示在页面右上角，几秒后自动消失，不打断操作
    """用轻提示把「存入票夹」的结果告诉用户（成功 / 失败都会说清楚）"""
    st.toast(message, icon="✅" if success else "❌")  # 成功给绿色对勾，失败给红色叉号


def _alive_saved_id():  # 内部函数：取本次会话记录过的发票 id，并确认它现在还在票夹里
    """会话里记的发票 id 可能已经过期（用户把发票从票夹删掉了），所以要去数据库确认一次"""
    saved_id = st.session_state.get("saved_invoice_id")  # 本次上传流程里存过的发票 id
    if not saved_id:  # 没存过（或已清空）
        return None  # 直接返回空，让上层重新走存入流程
    detail = _db.get_invoice_detail(saved_id)  # 去数据库查这条发票记录还在不在
    if detail and detail.get("folder_status") == "in_folder":  # 记录存在，而且还在票夹里
        return saved_id  # 可以放心复用这个 id
    st.session_state["saved_invoice_id"] = None  # 记录没了或已被移出票夹：清掉这个过期标记
    return None  # 返回空，让上层重新存入


def _find_in_folder(invoice_number):  # 内部函数：按发票号码在当前员工的票夹里找这张发票
    """返回该发票号码在当前员工票夹中的发票 id；没找到就返回 None"""
    if not invoice_number:  # 号码为空时不做匹配
        return None  # 避免误命中票夹里其他没识别出号码的发票
    for inv in _db.list_folder_invoices(employee_id=get_current_employee_id()):  # 只查当前员工的票夹
        if (inv.get("invoice_number") or "").strip() == invoice_number:  # 号码完全一致
            return inv["id"]  # 返回它在库里的 id
    return None  # 转完一圈没找到


def _save_to_folder(invoice_data, result, quiet=False):  # 内部函数：把当前发票存进票夹
    """把当前发票存进票夹。

    返回：成功 → 发票 id；失败 → None（失败时用轻提示说明原因）
    quiet=True 时不提示，用于「填写报销单」前的静默自动存入"""
    saved_id = _alive_saved_id()  # 先看本次上传是否已经存过（并确认它还真的在票夹里）
    if saved_id:  # 已经存过
        if not quiet:  # 需要提示时才提示
            _notify(True, "该发票已在票夹中，无需重复存入")  # 轻提示重复存入
        return saved_id  # 直接复用已有 id，不再写库

    file_bytes = st.session_state.get("upload_file_bytes")  # 解析时缓存的原始文件字节
    file_name = st.session_state.get("upload_file_name")  # 解析时缓存的原始文件名
    if not file_bytes:  # 找不到原始文件（例如用户刚清空过缓存）
        if not quiet:  # 需要提示时才提示
            _notify(False, "存入不成功：找不到原始文件，请重新上传")  # 轻提示失败原因
        return None  # 没法存，返回空

    save_result = save_invoice_to_folder(  # 调用数据库模块的保存函数，把文件 + 解析结果 + 校验结果一起写库
        file_bytes=file_bytes,  # 原始文件字节
        original_filename=file_name or "发票",  # 原始文件名
        invoice_data=invoice_data,  # 识别出来的发票字段（中文键）
        parse_status="success",  # 标记为识别成功
        audit_result=build_audit_record(result),  # 基础校验结果一并写库，发票详情页要用
        employee_id=get_current_employee_id(),  # 轻量多员工：用当前切换的身份
    )

    if save_result.get("ok"):  # 写库成功
        st.session_state["saved_invoice_id"] = save_result["id"]  # 记下发票 id，后面填写报销单直接复用
        if not quiet:  # 需要提示时才提示
            _notify(True, "存入成功")  # 轻提示「存入成功」
        return save_result["id"]  # 把 id 返回给调用方

    number = (invoice_data.get("发票号码") or "").strip()  # 写库失败：先拿到这张发票的号码备用
    existing_id = _find_in_folder(number)  # 去当前员工的票夹里找找，是不是早前就已经存过这张发票了
    if not existing_id:  # 当前员工票夹里没有 → 直接显示数据库返回的错误原因（已包含"被谁存了"的提示）
        if not quiet:
            _notify(False, "存入不成功：" + str(save_result.get("error") or "未知错误"))
        return None  # 返回空表示失败

    st.session_state["saved_invoice_id"] = existing_id  # 票夹里已经有了：直接复用它的 id
    if not quiet:  # 需要提示时才提示
        _notify(True, "存入成功：该发票已存在于票夹中")  # 轻提示已经在票夹里了
    return existing_id  # 把已有发票的 id 返回给调用方


st.set_page_config(page_title="发票上传", layout="wide")  # 设置浏览器标签标题和宽屏布局

with st.container(key="pagehead"):  # 统一页头容器（样式由 app.py 统一控制，其他页面也一样）
    st.header("发票上传")  # 统一字号的大标题
    st.caption("上传 XML / PDF / 图片发票，自动识别并执行基础合规校验")  # 标题下的灰色说明
    st.divider()  # 灰色横线，与标题、正文的间距已统一收紧


uploaded_file = st.file_uploader(  # 文件上传控件：用户选择发票文件后返回文件对象
    "选择发票文件，支持XML、PDF、PNG、JPG格式",  # 控件上方的提示文字
    type=["xml", "pdf", "png", "jpg", "jpeg"]  # 只允许这些后缀，其他文件点不上
)

if uploaded_file is not None:  # 只有用户真的选了文件，才走「识别」这一步
    file_bytes = uploaded_file.getvalue()  # 把上传的文件读成二进制（后面存票夹要用）
    # 用「文件名 + 大小 + 内容哈希」给这次上传算一个指纹，用来判断用户是否换了文件
    file_signature = f"{uploaded_file.name}|{len(file_bytes)}|{hashlib.sha256(file_bytes).hexdigest()}"

    if st.session_state.get("pipeline_signature") != file_signature:  # 换文件了：重新解析，并清掉上一张发票留下的状态
        with st.spinner("正在解析发票并执行基础校验..."):  # 转圈提示只在首次解析该文件时出现
            with tempfile.NamedTemporaryFile(delete=False, suffix=uploaded_file.name) as tmp:  # 建临时文件，后缀跟随原文件名
                tmp.write(file_bytes)  # 把上传内容写进临时文件
                tmp_path = tmp.name  # 记下临时文件路径，交给识别器使用

            result = run_basic_check_pipeline(tmp_path)  # 执行流程：解析发票 + 12 项基础校验（到此为止，不调用 Agent）

        st.session_state.pipeline_result = result  # 结果存进会话状态（这也是「切页面回来内容还在」的关键）
        st.session_state.pipeline_signature = file_signature  # 记下本次解析的文件指纹
        st.session_state.upload_file_bytes = file_bytes  # 缓存原始字节（存入票夹时用，不再依赖上传控件）
        st.session_state.upload_file_name = uploaded_file.name  # 缓存原始文件名
        st.session_state["saved_invoice_id"] = None  # 新文件：清掉上一张的「已存入票夹」标记
        for stale_key in ("pending_consult", "pending_consult_history", "pending_consult_agent", "pending_consult_asked"):
            st.session_state.pop(stale_key, None)  # 清掉上一张发票的咨询材料，避免串味

result = st.session_state.get("pipeline_result")  # 取本次会话缓存的识别结果：切到别的页面再回来，它依然在

if result is None:  # 这个会话还没有解析过任何发票
    st.info("还没有选择文件。上传发票后会自动识别并做基础合规校验，然后给出下一步操作入口。")  # 蓝色引导条
else:  # 有识别结果：完整渲染（下面这段不再依赖上传控件，所以切页面回来内容不会丢）
    info_l, info_r = st.columns([5, 1], vertical_alignment="center")  # 左：当前发票说明；右：清空按钮
    with info_l:  # 左侧说明
        st.caption(f"当前发票：{st.session_state.get('upload_file_name') or '未命名'}（识别结果已保留，重新选择文件即可替换）")  # 灰色小字
    with info_r:  # 右侧清空入口
        if st.button("清空", use_container_width=True, key="clear_pipeline"):  # 想换一张发票时点它
            for stale_key in ("pipeline_result", "pipeline_signature", "upload_file_bytes", "upload_file_name", "saved_invoice_id"):
                st.session_state.pop(stale_key, None)  # 把本次会话的识别缓存全部清掉
            st.rerun()  # 立刻重跑一次，页面回到「还没有选择文件」的状态

    if result["status"] == "failed":  # 流程失败（例如无法识别）
        st.error(f"处理失败：{result['error']['message']}")  # 用红色错误框显示失败原因
    else:  # 流程成功
        invoice_data = result["invoice_data"]  # 取出解析出来的发票字段字典
        summary = result["summary"]  # 基础校验汇总（含 final_result）
        passed = summary["final_result"] == "通过"  # 基础校验是否通过

        st.subheader("发票识别信息")  # 小标题：识别结果
        col1, col2 = st.columns(2)  # 把区域分成左右两列，信息更紧凑
        with col1:  # 左列：发票自身信息
            st.write("发票类型：", invoice_data.get("发票类型", ""))  # 显示发票类型
            st.write("发票号码：", invoice_data.get("发票号码", ""))  # 显示发票号码
            st.write("开票日期：", invoice_data.get("开票日期", ""))  # 显示开票日期
        with col2:  # 右列：双方与金额
            st.write("购买方：", invoice_data.get("购买方名称", ""))  # 显示购买方名称
            st.write("销售方：", invoice_data.get("销售方名称", ""))  # 显示销售方名称
            st.write("价税合计：", invoice_data.get("价税合计小写", ""))  # 显示价税合计金额

        if "明细列表" in invoice_data and invoice_data["明细列表"]:  # 如果解析出了商品明细
            st.subheader("商品明细")  # 小标题：商品明细
            st.dataframe(invoice_data["明细列表"])  # 用表格展示明细行

        # 识别出的完整JSON数据（存储在数据库中的原始内容，便于核对解析是否完整）
        st.subheader("识别数据（JSON）")  # 小标题：识别JSON
        with st.expander("查看/复制识别出的完整JSON（入库原样存储）", expanded=False):  # 折叠面板，默认收起
            st.json(invoice_data)  # 用JSON树展示识别出的所有字段（与存入数据库 parsed_json 内容一致）
            st.caption("以上内容即存入数据库 parsed_json 字段的原始数据，供核对解析字段是否完整。")  # 灰色说明

        st.subheader("基础校验结果")  # 小标题：基础校验结果
        st.code(format_for_user(result), language=None)  # 用等宽文本块显示逐条校验结论，保持排版

        st.divider()  # 分隔线：把校验结果和下面的操作区隔开
        st.subheader("请选择操作")  # 小标题：操作区

        if passed:  # 基础校验通过
            st.success("基础校验通过：可以存入票夹，也可以直接填写报销单")  # 绿色提示
        else:  # 基础校验没通过
            st.warning("基础校验未通过：可以先咨询 AI 审核员了解原因；填写报销单暂不开放")  # 黄色提示

        op1, op2 = st.columns(2)  # 第一行两个操作
        op3, op4 = st.columns(2)  # 第二行两个操作

        # ---------- 操作1：咨询AI审核员（带着发票信息 + 基础校验结果跳到帮助中心） ----------
        with op1:
            if st.button("咨询AI审核员", use_container_width=True):  # 点击后跳转
                st.session_state["pending_consult"] = {  # 把本次要咨询的材料打包存好
                    "invoice_data": invoice_data,  # 发票识别结果（中文键）
                    "basic_check_text": format_for_user(result),  # 基础校验结果明细文本
                    "final_result": summary["final_result"],  # 基础校验结论
                    "invoice_number": invoice_data.get("发票号码", ""),  # 发票号码（帮助中心顶部展示用）
                    "seller_name": invoice_data.get("销售方名称", ""),  # 销售方（展示用）
                    "total_amount": invoice_data.get("价税合计小写", ""),  # 金额（展示用）
                }
                st.session_state["pending_consult_asked"] = False  # 让帮助中心重新自动发起第一问
                st.session_state["pending_consult_history"] = []  # 清空上一轮咨询对话
                st.session_state["pending_consult_agent"] = None  # 强制重新创建 Agent（绑定新发票）
                st.switch_page("pages/4_帮助中心.py")  # 跳转到帮助中心

        # ---------- 操作2：存入票夹 ----------
        with op2:
            if st.button("存入票夹", use_container_width=True):  # 点击后存库
                _save_to_folder(invoice_data, result)  # 成功 / 失败都由函数内部用轻提示告诉用户

        # ---------- 操作3：填写报销单（必须基础校验通过；进入前自动存入票夹） ----------
        with op3:
            if st.button("填写报销单", type="primary", use_container_width=True, disabled=not passed):  # 未通过时按钮禁用
                invoice_id = _save_to_folder(invoice_data, result, quiet=True)  # 先静默存入票夹，拿到发票 id
                if invoice_id:  # 存入成功才跳转
                    st.session_state["expense_form_invoice_id"] = invoice_id  # 把发票 id 传给报销单填写页
                    st.session_state["expense_form_flash"] = "发票已自动存入票夹"  # 到填写页再弹一次提示
                    st.switch_page("pages/7_报销单填写.py")  # 跳转到报销单填写页
            if not passed:  # 按钮被禁用时说明原因
                st.caption("基础校验未通过，无法填写报销单")  # 灰色小字

        # ---------- 操作4：联系人工财务（暂未实现） ----------
        with op4:
            st.button("联系人工财务", use_container_width=True, disabled=True)  # 置灰占位
            st.caption("功能开发中，暂未开放")  # 灰色小字说明
