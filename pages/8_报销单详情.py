# -*- coding: utf-8 -*-
"""报销单详情：从我的报销单点击“查看”后跳转进来（不在侧边栏显示）"""
import importlib  # 用于重新加载数据库模块，避免热重载时用到旧代码
from datetime import datetime  # 记录申诉提交时间

import streamlit as st  # 导入 Streamlit

import database.db as _db  # 引入数据库模块（查报销单 / 查发票 / 删报销单）
from utils.audit_verdict import normalize_audit  # 结论规范化工具（保证展示的结论与 Agent 一致）
from utils.expense_form import normalize_ext_fields  # 分类扩展字段 → “标签 + 值”列表

_db = importlib.reload(_db)  # 强制重新加载，保证拿到最新版本的函数
get_expense_form = _db.get_expense_form  # 查询单张报销单
get_invoice_detail = _db.get_invoice_detail  # 查询关联发票
delete_expense_form = _db.delete_expense_form  # 删除报销单（已驳回时允许重填）

st.set_page_config(page_title="报销单详情", layout="wide")  # 页面配置：标签标题 + 宽屏布局


def _fmt_amount(value):  # 工具函数：把金额格式化成 ¥1,234.00
    if value is None or value == "":  # 金额为空
        return "-"  # 显示短横线
    try:  # 正常是数字
        return f"¥{float(value):,.2f}"  # 千分位 + 两位小数
    except (TypeError, ValueError):  # 不是数字时原样显示
        return str(value)


@st.dialog("人工申诉", width=400)  # 申诉弹窗：窄弹窗与报销单填写页的语义审核弹窗保持一致
def render_appeal_dialog(form_no):  # 参数：当前报销单号（写进申诉记录）
    st.write("如对本次审核结论有异议，请填写申诉理由及补充说明。")  # 弹窗顶部说明
    appeal_reason = st.text_area(  # 申诉理由输入框
        "申诉理由",  # 控件标题
        placeholder="请说明您认为审核结论有误的依据，或需要补充的材料",  # 提示文字
        key="appeal_reason_input",  # 控件key
    )
    if st.button("提交申诉", type="primary", use_container_width=True):  # 提交按钮
        if not (appeal_reason or "").strip():  # 理由为空
            st.warning("请填写申诉理由")  # 提示必填
        else:  # 理由非空：记录到会话状态，关闭弹窗后在主页面显示成功提示
            st.session_state["appeal_record"] = {  # 申诉记录（暂存会话，供主页面显示）
                "form_no": form_no,  # 报销单号
                "reason": appeal_reason.strip(),  # 申诉理由
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 提交时间
            }
            st.rerun()  # 重跑页面：关闭弹窗，主页面显示提交成功提示


form_id = st.session_state.get("expense_form_id")  # 取列表页存下的报销单 id（没有就是 None）
form = get_expense_form(form_id) if form_id else None  # 有 id 才去数据库查，避免无意义查询

# ---------------------------------------------------------------------------
# 页头：左边标题 + 摘要，右边“返回我的报销单”（与其他页面同一套标题样式）
# ---------------------------------------------------------------------------
with st.container(key="pagehead"):  # 统一页头容器：样式由 app.py 统一控制
    head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")  # 左标题、右按钮，两列底部对齐
    with head_l:  # 左侧：标题与摘要
        st.header("报销单详情")  # 统一字号的大标题
        if form:  # 查到单据：显示“单号 · 费用类型 · 金额 · 状态”摘要
            st.caption(
                f"{form.get('form_no') or '-'} · {form.get('expense_type') or '未分类'} · "
                f"{_fmt_amount(form.get('total_amount'))} · 状态：{form.get('status') or '-'}"
            )
        else:  # 没查到：只显示通用说明
            st.caption("查看报销单内容、关联发票与财务终审结论")  # 灰色说明
    with head_r:  # 右侧：返回按钮
        if st.button("← 返回我的报销单", use_container_width=True):  # 点击返回
            st.switch_page("pages/2_我的报销单.py")  # 跳回我的报销单页
    st.divider()  # 灰色横线，间距由全局样式统一控制

if not form_id:  # 没有带报销单 id 进来（例如直接访问本页）
    st.warning("未选择报销单，请从「我的报销单」进入。")  # 黄色提示
    st.stop()  # 停止继续渲染

if not form:  # 有 id 但查不到（可能已被删除）
    st.error("报销单不存在或已被删除。")  # 红色提示
    st.stop()  # 停止渲染

audit = normalize_audit(form.get("audit")) or {}  # 审核记录；顺手纠正结论，保证与 Agent 判定一致

# ---------------------------------------------------------------------------
# 报销单主表信息
# ---------------------------------------------------------------------------
st.subheader("基本信息")  # 小标题
c1, c2, c3 = st.columns(3)  # 三列展示
with c1:  # 第一列：单号 / 行程 / 类型
    st.write("报销单号：", form.get("form_no") or "-")  # 系统自动生成的单号
    st.write("行程号：", form.get("trip_no") or "-")  # 行程号（同行程可一起查看）
    st.write("费用类型：", form.get("expense_type") or "-")  # 费用类型
with c2:  # 第二列：日期 / 金额 / 参与人
    st.write("费用发生日期：", form.get("occur_date") or "-")  # 发生日期
    st.write("报销金额：", _fmt_amount(form.get("total_amount")))  # 报销金额
    st.write("参与人：", form.get("participants") or "-")  # 参与人
with c3:  # 第三列：状态 / 时间 / 事由
    st.write("状态：", form.get("status") or "-")  # 单据状态
    st.write("提交时间：", form.get("submit_time") or "-")  # 提交时间
    st.write("报销事由：", form.get("reason") or "-")  # 报销事由

# ---------------------------------------------------------------------------
# 发票模块：报销单只是壳，真正的票据还是票夹里的那张发票
# ---------------------------------------------------------------------------
st.subheader("关联发票")  # 小标题
invoice = get_invoice_detail(form.get("invoice_id")) if form.get("invoice_id") else None  # 查关联发票
if invoice:  # 发票还在
    i1, i2, i3 = st.columns(3)  # 三列展示发票信息
    with i1:  # 第一列
        st.write("发票号码：", invoice.get("invoice_number") or "-")  # 发票号码（系统带出，非手填）
        st.write("开票日期：", invoice.get("invoice_date") or "-")  # 开票日期
    with i2:  # 第二列
        st.write("销售方：", invoice.get("seller_name") or "-")  # 销售方
        st.write("购买方：", invoice.get("buyer_name") or "-")  # 购买方
    with i3:  # 第三列
        st.write("价税合计：", _fmt_amount(invoice.get("total_amount")))  # 价税合计
        st.write("原文件：", invoice.get("original_filename") or "-")  # 原始文件名
    if st.button("查看这张发票", use_container_width=True):  # 跳转按钮
        st.session_state["folder_invoice_id"] = invoice["id"]  # 记录要查看的发票 id
        st.switch_page("pages/6_发票详情.py")  # 跳转到发票详情页
else:  # 发票被删了
    st.warning("关联发票不存在或已被移除。")  # 黄色提示

# ---------------------------------------------------------------------------
# 分类扩展信息：不同费用类型填的专属字段（统一主表 + 分类扩展的“扩展”部分）
# ---------------------------------------------------------------------------
ext_rows = normalize_ext_fields(form.get("expense_type"), form.get("ext_fields"))  # 取出有值的扩展字段
if ext_rows:  # 有填才显示
    st.subheader(f"{form.get('expense_type') or '费用'}分类信息")  # 小标题带上费用类型
    ext_cols = st.columns(3)  # 三列摆放
    for index, row in enumerate(ext_rows):  # 逐项渲染
        with ext_cols[index % 3]:  # 轮流放进三列
            st.write(f"{row['label']}：", row["value"])  # 标签 + 值

# ---------------------------------------------------------------------------
# 明细模块：发票上原有的商品明细已自动注入，这里展示员工最终确认的版本
# ---------------------------------------------------------------------------
st.subheader("报销明细")  # 小标题
items = form.get("detail_items") or []  # 明细行
if items:  # 有明细才画表格
    st.dataframe(  # 表格展示
        [  # 逐行整理成“中文表头 → 值”的字典
            {
                "项目名称": it.get("项目名称"),  # 项目名称
                "规格型号": it.get("规格型号"),  # 规格型号
                "单位": it.get("单位"),  # 单位
                "数量": it.get("数量"),  # 数量
                "单价": it.get("单价"),  # 单价
                "金额": it.get("金额"),  # 金额
            }
            for it in items  # 遍历每一条明细
        ],
        use_container_width=True,  # 表格撑满宽度
        hide_index=True,  # 隐藏默认行号列
    )
else:  # 没有明细
    st.caption("没有明细行")  # 灰色提示

# ---------------------------------------------------------------------------
# 报销事由语义审核：用LLM判断事由与发票内容是否匹配
# ---------------------------------------------------------------------------
st.subheader("事由语义审核")  # 小标题
reason_check = audit.get("reason_check") or {}  # 从审核记录中取出事由语义审核结果

if reason_check:
    if reason_check.get("level") == "pass":
        st.success(f"审核通过：{reason_check.get('message', '')}")
    else:
        st.warning(f"审核提醒：{reason_check.get('message', '')}")
    if reason_check.get("details"):
        with st.expander("查看审核详情", expanded=False):
            st.write(reason_check["details"])
else:
    st.caption("无事由语义审核记录（较早提交的报销单可能没有此项）")

st.divider()  # 分隔线

# ---------------------------------------------------------------------------
# 风控检测结果：提交报销单时自动跑的6项风控检测（重复/连号/拆分/金额临界/高频/行为画像）
# ---------------------------------------------------------------------------
st.subheader("风控检测")  # 小标题
anomaly_check = audit.get("anomaly_check") or {}  # 从审核记录中取出风控结果

# 判断是否真的跑过风控：有风控结果字段才算跑过，空字典不算
ran_anomaly = bool(anomaly_check) and ("has_risk" in anomaly_check or "error" in anomaly_check)

if not ran_anomaly:
    # 草稿/未提交审核：根本没跑过风控，不能显示"通过"
    st.caption("未执行风控检测（草稿状态，提交审核后自动执行）")
elif anomaly_check.get("error"):
    # 风控检测本身出错了
    st.warning(f"风控检测异常：{anomaly_check['error']}")
elif anomaly_check.get("has_risk"):
    # 有风险：红色横幅 + 标准化列出每项风险
    st.error(f"风险管控失败 · 共发现 {anomaly_check['risk_count']} 项异常")
    # 按风险等级分组展示
    high_items = [i for i in anomaly_check["risk_items"] if i["level"] == "high"]
    medium_items = [i for i in anomaly_check["risk_items"] if i["level"] == "medium"]
    low_items = [i for i in anomaly_check["risk_items"] if i["level"] == "low"]
    if high_items:
        st.markdown("**高风险**")
        for i, item in enumerate(high_items, 1):
            st.markdown(f"{i}. **{item['type']}**：{item['message']}")
    if medium_items:
        st.markdown("**中风险**")
        for i, item in enumerate(medium_items, 1):
            st.markdown(f"{i}. **{item['type']}**：{item['message']}")
    if low_items:
        st.markdown("**低风险**")
        for i, item in enumerate(low_items, 1):
            st.markdown(f"{i}. **{item['type']}**：{item['message']}")
else:
    # 真的跑过且无风险：绿色提示
    st.success("风控检测通过 · 6项检测全部正常（重复报销/连号/拆分/金额临界/高频/行为画像）")

st.divider()  # 分隔线

# ---------------------------------------------------------------------------
# Agent 财务终审结果：发票 + 报销单 + 风控结果一起送审后写回来的结论
# ---------------------------------------------------------------------------
st.subheader("Agent 财务终审")  # 小标题
final_result = audit.get("final_result")  # 终审结论
if final_result == "通过":  # 通过
    st.success(f"已终审 · 通过　·　审核时间：{audit.get('checked_at') or '-'}")  # 绿色结论条
elif final_result == "不通过":  # 不通过
    st.error(f"已终审 · 未通过　·　审核时间：{audit.get('checked_at') or '-'}")  # 红色结论条
else:  # 还没终审
    st.warning("暂无终审记录")  # 黄色提示

if audit.get("agent_answer"):  # 有 Agent 回答文本才显示
    st.markdown("### 审核分析")  # 大标题，与截图格式一致
    st.markdown(audit["agent_answer"])  # 用markdown渲染，支持标题/列表/加粗等格式

# 按钮区上方灰线：内联样式收紧上下间距（Streamlit 默认 hr 上下各约 1rem 太松）
st.markdown(
    "<hr style='margin:4px 0 10px 0; border:none; border-top:1px solid #E2E8F0;'>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 结果操作区：对审核结论有疑问 → 咨询AI审核员；对结论有异议 → 人工申诉
# ---------------------------------------------------------------------------
op1, op2 = st.columns(2)  # 两个按钮并排
with op1:  # 左侧：咨询AI审核员
    if st.button("咨询AI审核员", type="primary", use_container_width=True):  # 书面化按钮
        # 构造帮助中心的咨询材料：带上这张报销单的发票数据和基础校验文本
        invoice_audit = invoice.get("audit") or {}  # 发票表里存的基础校验记录
        st.session_state["pending_consult"] = {  # 帮助中心靠这个 session_state 接收咨询材料
            "invoice_number": invoice.get("invoice_number") or "",  # 发票号码
            "seller_name": invoice.get("seller_name") or "",  # 销售方
            "total_amount": _fmt_amount(invoice.get("total_amount")),  # 价税合计
            "final_result": audit.get("final_result") or "-",  # 审核结论
            "invoice_data": invoice.get("parsed") or {},  # 完整识别 JSON
            "basic_check_text": invoice_audit.get("detail_text") or "",  # 基础校验逐条结果
        }
        # 清掉可能残留的旧咨询状态，确保帮助中心开新对话
        for stale_key in ("pending_consult_history", "pending_consult_agent", "pending_consult_asked"):
            st.session_state.pop(stale_key, None)
        st.switch_page("pages/4_帮助中心.py")  # 跳转到帮助中心的发票咨询

with op2:  # 右侧：人工申诉
    if st.button("人工申诉", use_container_width=True):  # 书面化按钮
        render_appeal_dialog(form.get("form_no") or "-")  # 打开申诉弹窗

appeal_record = st.session_state.get("appeal_record")  # 最近一次提交的申诉记录
if appeal_record and appeal_record.get("form_no") == (form.get("form_no") or "-"):  # 是本单的申诉
    st.success(  # 绿色成功提示（显示在按钮下方）
        f"申诉已提交 · 单号 {appeal_record['form_no']} · {appeal_record['time']}，"
        f"财务人员将结合您提供的理由进行复核。"
    )

# ---------------------------------------------------------------------------
# 还没通过的单据：允许删掉重填（否则这张发票会被“占用”，永远不能再提交）
#   已通过的单据不给删除入口，避免删掉已经生效的报销记录
# ---------------------------------------------------------------------------
if (form.get("status") or "") in ("已驳回", "待财务终审", "草稿"):  # 草稿/待终审/已驳回都能编辑或删除
    st.divider()  # 分隔线

    current_status = form.get("status") or ""

    if current_status == "草稿":
        # 草稿状态：最显眼的按钮是"提交审核"，其次才是编辑和删除
        st.info("这是一张草稿，尚未提交审核。完善内容后可提交进入财务终审。")
        submit_col, edit_col, delete_col = st.columns([2, 1, 1])
        with submit_col:
            if st.button("提交审核", type="primary", use_container_width=True):
                st.session_state["edit_expense_form_id"] = form_id
                st.switch_page("pages/7_报销单填写.py")
        with edit_col:
            if st.button("编辑", use_container_width=True):
                st.session_state["edit_expense_form_id"] = form_id
                st.switch_page("pages/7_报销单填写.py")
        with delete_col:
            st.caption("删除后发票可重新填写。")
            confirm = st.checkbox("我确认删除", key="confirm_delete_form")
            if st.button("删除", use_container_width=True, disabled=not confirm):
                delete_expense_form(form_id)
                st.session_state.pop("expense_form_id", None)
                st.success("已删除")
                st.switch_page("pages/2_我的报销单.py")
    else:
        # 已驳回/待终审：编辑 + 删除
        edit_col, delete_col = st.columns(2)
        with edit_col:
            if st.button("编辑报销单", type="primary", use_container_width=True):
                st.session_state["edit_expense_form_id"] = form_id
                st.switch_page("pages/7_报销单填写.py")

        with delete_col:
            st.caption("删除后这张报销单会从系统里消失，对应的发票会重新变成「未填写」，可以再填一次。")
            confirm = st.checkbox("我确认删除这张报销单", key="confirm_delete_form")
            if st.button("删除此报销单并重新填写", use_container_width=True, disabled=not confirm):
                delete_expense_form(form_id)
                st.session_state.pop("expense_form_id", None)
                st.success("已删除，可回到票夹重新填写")
                st.switch_page("pages/2_我的报销单.py")
