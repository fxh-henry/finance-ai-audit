# -*- coding: utf-8 -*-
"""报销单详情：从我的报销单点击“查看”后跳转进来（不在侧边栏显示）"""
import importlib
import json  # 用于重新加载数据库模块，避免热重载时用到旧代码
from datetime import datetime  # 记录申诉提交时间

import streamlit as st  # 导入 Streamlit

import database.db as _db  # 引入数据库模块（查报销单 / 查发票 / 删报销单）
from utils.audit_verdict import normalize_audit, resolve_final_verdict  # 结论规范化 + 取终审结论
from utils.expense_form import normalize_ext_fields  # 分类扩展字段
from utils.current_user import get_current_employee_id, get_current_employee  # 当前登录员工（含职级）
from pipeline import run_expense_form_audit, format_anomaly_for_user  # 终审流程

_db = importlib.reload(_db)  # 强制重新加载，保证拿到最新版本的函数
get_expense_form = _db.get_expense_form  # 查询单张报销单
get_invoice_detail = _db.get_invoice_detail  # 查询关联发票
delete_expense_form = _db.delete_expense_form  # 删除报销单
update_expense_form_audit = _db.update_expense_form_audit  # 回写终审结果

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
        _current_emp = get_current_employee()  # 当前身份（含姓名 / 部门 / 职级）
        if _current_emp:  # 取到员工信息才显示这一行
            # 让用户看到报销人职级：这是费用标准（住宿费等）实际使用的核算档位
            st.caption(
                f"报销人：{_current_emp.get('name')} · {_current_emp.get('department')} · "
                f"职级 {_current_emp.get('level')}"
            )
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

# ---------------------------------------------------------------------------
# AI审核决策过程（折叠时间轴）
# ---------------------------------------------------------------------------
trace_data = audit.get("trace") or {}
trace_steps = trace_data.get("steps") or []
if trace_steps:
    with st.expander("AI审核决策过程（点击展开查看Agent思考与工具调用）", expanded=False):
        st.caption("以下为AI审核员在本次审核中的完整推理轨迹：")
        for step in trace_steps:
            step_kind = step.get("kind", "")
            step_text = step.get("text", "")
            tool_name = step.get("tool_name", "")
            tool_args = step.get("tool_args", {})
            tool_result = step.get("tool_result", {})

            if step_kind == "thinking":
                clean = step_text.lstrip("#").strip()
                display = clean[:200] + ("..." if len(clean) > 200 else "")
                st.markdown(f"💭 **思考**：{display}")
            elif step_kind == "tool_call":
                st.markdown(f"🔧 **调用工具**：`{tool_name}`")
                st.code(json.dumps(tool_args, ensure_ascii=False, indent=2), language="json")
            elif step_kind == "tool_result":
                st.markdown(f"📦 **工具返回**：`{tool_name}`")
                st.code(json.dumps(tool_result, ensure_ascii=False, indent=2)[:500], language="json")
            elif step_kind == "answer":
                clean = step_text.lstrip("#").strip()
                display = clean[:200] + ("..." if len(clean) > 200 else "")
                st.markdown(f"✅ **最终回答**：{display}")
            elif step_kind == "structured":
                st.markdown(f"📋 **结构化裁决**：结论 = {step_text}")
            st.divider()

# ---------------------------------------------------------------------------
# 制度依据（引用溯源高亮）：结论引用的条款 ↔ RAG检索到的原文
# ---------------------------------------------------------------------------
structured_data = audit.get("structured") or {}
citations = structured_data.get("citations") or []
# 从trace里取出RAG工具返回的制度原文
rag_policy_texts = []
for step in (trace_steps or []):
    if step.get("tool_name") == "rag_policy_search_tool":
        tr = step.get("tool_result") or {}
        pt = tr.get("policy_text") or ""
        if pt:
            rag_policy_texts.append(pt)

if citations or rag_policy_texts:
    with st.expander("制度依据（引用溯源）：结论引用的条款 ↔ 检索原文", expanded=False):
        if citations:
            st.markdown("**本次审核引用的制度条款：**")
            for c in citations:
                st.markdown(f"📌 {c}")
            st.markdown("")
        if rag_policy_texts:
            st.markdown("**RAG检索到的制度原文：**")
            for i, pt in enumerate(rag_policy_texts, 1):
                with st.container(border=True):
                    st.caption(f"检索结果 {i}")
                    st.write(pt[:800] + ("..." if len(pt) > 800 else ""))

# ---------------------------------------------------------------------------
# 防幻觉校验报告（折叠面板）
# ---------------------------------------------------------------------------
guard = audit.get("verdict_guard") or {}
if guard:
    with st.expander("防幻觉校验报告（点击展开查看逐项检查）", expanded=False):
        st.caption(f"校验结论：{'通过' if guard.get('ok') else '存在问题'}")
        if guard.get("errors"):
            st.error(f"错误项：{'; '.join(guard['errors'])}")
        if guard.get("warnings"):
            st.warning(f"警告项：{'; '.join(guard['warnings'])}")
        for check in guard.get("checks", []):
            level = check.get("level", "")
            icon = "✅" if level == "pass" else ("⚠️" if level == "warn" else "❌")
            st.markdown(f"{icon} **{check['name']}**：{check['detail']}")

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
        # 草稿状态：直接在本页提交审核，不用跳回填写页
        st.info("这是一张草稿，尚未提交审核。")
        submit_col, edit_col, delete_col = st.columns([2, 1, 1])
        with submit_col:
            if st.button("提交审核", type="primary", use_container_width=True):
                # 从当前单据构造送审数据
                invoice_detail = get_invoice_detail(form.get("invoice_id"))
                invoice_data = invoice_detail.get("parsed") or {}
                agent_payload = {
                    "form_no": form.get("form_no"),
                    "trip_no": form.get("trip_no"),
                    "expense_type": form.get("expense_type"),
                    "occur_date": form.get("occur_date"),
                    "reason": form.get("reason"),
                    "total_amount": form.get("total_amount"),
                    "participants": form.get("participants"),
                    "ext_fields": form.get("ext_fields"),
                    "detail_items": form.get("detail_items"),
                }
                # ---------- 实时渲染审核过程 ----------
                st.markdown("### AI审核过程")
                timeline_container = st.container()
                step_count = [0]  # 用list包装以便闭包修改

                def on_audit_event(event):
                    """Agent每跑一步就实时渲染到页面上。"""
                    etype = event.get("type", "")
                    with timeline_container:
                        if etype == "thinking":
                            st.markdown(f"💭 **思考**：{event.get('text', '')[:150]}")
                        elif etype == "tool_call":
                            st.markdown(f"🔧 **调用工具**：`{event.get('tool_name', '')}`")
                        elif etype == "tool_result":
                            st.markdown(f"📦 **工具返回**：`{event.get('tool_name', '')}` 完成")
                        elif etype == "answer":
                            st.markdown(f"✅ **最终回答生成中...**")
                        elif etype == "structured":
                            st.markdown(f"📋 **结构化裁决完成**")
                        elif etype == "reflection_start":
                            st.markdown(f"🔁 **{event.get('text', '')}**")
                        elif etype == "reflection_result":
                            st.markdown(f"🔁 **{event.get('text', '')}**")

                with st.spinner("正在执行风控检测和AI终审，请稍候..."):
                    audit_result = run_expense_form_audit(
                        invoice_data, agent_payload,
                        exclude_form_id=form_id,
                        employee_id=get_current_employee_id(),
                        on_event=on_audit_event,
                    )

                # 取终审结论
                verdict = resolve_final_verdict(audit_result) or "不通过"
                new_status = "已通过" if verdict == "通过" else "已驳回"

                # 提取运行轨迹，存进数据库供详情页展示
                trace_data = {}
                agent_obj = audit_result.get("agent")
                if agent_obj and hasattr(agent_obj, "last_trace"):
                    trace_data = agent_obj.last_trace.to_dict()
                guard_report = audit_result.get("verdict_guard") or {}

                update_expense_form_audit(
                    form_id,
                    audit_result={
                        "final_result": verdict,
                        "agent_answer": audit_result.get("answer"),
                        "structured": audit_result.get("verdict") or {},
                        "anomaly_check": audit_result.get("anomaly_check"),
                        "trace": trace_data,
                        "verdict_guard": guard_report,
                        "reflection": audit_result.get("reflection") or {},
                        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    status=new_status,
                )
                st.rerun()
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


