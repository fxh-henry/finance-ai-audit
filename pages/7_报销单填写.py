# -*- coding: utf-8 -*-
"""报销单填写：选票夹发票 → 填报销单（统一主表 + 分类扩展）→ 与发票一起提交 Agent 终审
   支持编辑模式：从详情页点"编辑"进来，预填现有数据，保存后重新审核"""
import importlib  # 用于热重载数据库模块，避免 Streamlit 热重载时拿到旧代码
import hashlib  # 用于计算语义审核缓存的签名
from datetime import date, datetime  # 日期控件默认值 / 审核时间戳

import streamlit as st  # 导入 Streamlit

import database.db as _db  # 引入数据库模块（票夹发票 / 报销单增改）
from audit.reason_check import check_reason_semantic
from pipeline import run_expense_form_audit
from pipeline import format_anomaly_for_user  # 引入"发票 + 报销单"一起送 Agent 终审的流程
from utils.audit_verdict import extract_verdict  # 把 Agent 回答解析成 通过 / 不通过
from utils.current_user import get_current_employee_id
from utils.expense_form import (  # 报销单配置与工具（和列表页、详情页、Agent 共用同一份）
    EXPENSE_CATEGORIES,  # 费用类型 → 分类扩展字段定义
    EXPENSE_TYPE_NAMES,  # 费用类型名称列表
    default_ext_fields,  # 某费用类型的扩展字段默认值
    guess_expense_type,  # 根据发票内容猜费用类型
    invoice_data_from_detail,  # 发票详情 → 中文键发票数据字典
)

_db = importlib.reload(_db)  # 强制重新加载，保证拿到最新版本的函数
list_folder_invoices = _db.list_folder_invoices  # 票夹发票列表
get_invoice_detail = _db.get_invoice_detail  # 单张发票详情（含明细）
list_expense_forms = _db.list_expense_forms  # 已有报销单（判断哪些发票已填过）
list_trip_numbers = _db.list_trip_numbers  # 已有行程号（可选来复用）
create_expense_form = _db.create_expense_form  # 新建报销单
update_expense_form_audit = _db.update_expense_form_audit  # 回写终审结果与状态
update_expense_form_fields = _db.update_expense_form_fields  # 更新报销单可编辑字段（编辑模式用）
get_expense_form = _db.get_expense_form  # 查询单张报销单（编辑模式预填数据用）

st.set_page_config(page_title="报销单填写", layout="wide")  # 页面配置：标签标题 + 宽屏布局

NEW_TRIP = "＋ 新建行程号"  # 行程号下拉框里的"新建"选项文字


def _fmt_amount(value):  # 工具函数：把金额格式化成 ¥1,234.00
    if value is None or value == "":  # 金额为空
        return "-"  # 显示短横线
    try:  # 正常是数字
        return f"¥{float(value):,.2f}"  # 千分位 + 两位小数
    except (TypeError, ValueError):  # 不是数字时原样显示
        return str(value)


def _clean_number(value):  # 把长小数的数字型文本收成两位小数（例如 2437.735849056604 → 2437.74），非数字原样返回
    if value is None or value == '':  # 空值
        return ''  # 返回空字符串
    text = str(value)  # 统一转成字符串处理
    try:  # 能转成浮点数说明是数字
        number = float(text)  # 先转成数字
    except (TypeError, ValueError):  # 不是数字（例如"一次"）
        return text  # 原样返回
    return f'{number:.2f}' if '.' in text else text  # 带小数点的收成两位，整数（例如数量）保持原样


def _render_ext_field(field, index, default_value=None):  # 按字段类型渲染一个"分类扩展"输入控件，并返回用户填的值
    key = f"ext_{index}_{field['key']}"  # 控件 key：带上序号保证全页唯一
    label = field["label"]  # 控件标题
    help_text = field.get("help") or None  # 标题右侧的小问号提示（没有就不显示）
    field_type = field.get("type", "text")  # 字段类型，默认按文本处理
    # 编辑模式下用传入的默认值，否则用字段定义里的默认值
    default = default_value if default_value is not None else field.get("default")
    if field_type == "int":  # 整数类型（例如房间数、晚数）
        return st.number_input(label, min_value=0, value=int(default or 0), step=1, key=key, help=help_text)
    if field_type == "float":  # 小数类型
        return st.number_input(label, min_value=0.0, value=float(default or 0.0), step=1.0, key=key, help=help_text)
    if field_type == "select":  # 下拉类型（例如交通方式）
        options = field.get("options") or []  # 可选项列表
        index_default = options.index(default) if default in options else 0  # 默认选中项
        return st.selectbox(label, options, index=index_default, key=key, help=help_text)
    return st.text_input(label, value=str(default or ""), key=key, help=help_text)  # 其余按文本处理


invoices = list_folder_invoices(employee_id=get_current_employee_id())  # 当前员工票夹里的发票
form_map = {f["invoice_id"]: f for f in list_expense_forms(employee_id=get_current_employee_id())}  # 当前员工的报销单
pending = [inv for inv in invoices if inv["id"] not in form_map]  # 还没填报销单的发票

# ---------------------------------------------------------------------------
# 页头：左边标题 + 说明，右边"返回票夹"（与其他页面同一套标题样式）
# ---------------------------------------------------------------------------
with st.container(key="pagehead"):  # 统一页头容器：样式由 app.py 统一控制
    head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")  # 左标题、右按钮，两列底部对齐
    with head_l:  # 左侧：标题区
        st.header("报销单填写")  # 统一字号的大标题
        st.caption("1 张发票对应 1 张报销单；同一趟出差的多张单填同一个行程号，即可合并查看")  # 灰色说明
    with head_r:  # 右侧：返回入口
        if st.button("← 返回票夹", use_container_width=True):  # 次要按钮
            st.switch_page("pages/3_我的票夹.py")  # 跳回我的票夹页
    st.divider()  # 灰色横线，间距由全局样式统一控制

flash = st.session_state.pop("expense_form_flash", None)  # 从「发票上传」页带过来的提示（只弹一次）
if flash:  # 有提示才弹窗
    st.toast(flash, icon="✅")  # 轻量弹窗提示

# ---------------------------------------------------------------------------
# 编辑模式判断：如果 session_state 里有 edit_expense_form_id，说明是从详情页点"编辑"进来的
# ---------------------------------------------------------------------------
edit_form_id = st.session_state.get("edit_expense_form_id")  # 要编辑的报销单ID
is_edit_mode = edit_form_id is not None  # 是否编辑模式
edit_form = get_expense_form(edit_form_id) if is_edit_mode else None  # 加载现有报销单数据

if is_edit_mode and not edit_form:  # 编辑模式但查不到单据（可能已被删除）
    st.error("要编辑的报销单不存在或已被删除。")
    if st.button("返回我的报销单"):
        st.switch_page("pages/2_我的报销单.py")
    st.stop()

if is_edit_mode:  # 编辑模式下，提示用户
    st.info(f"编辑模式：正在修改报销单 {edit_form.get('form_no') or ''}，修改后将重新执行审核。")


# ---------------------------------------------------------------------------
# 前置判断：票夹为空 / 发票都已填过，都给一个明确的下一步（编辑模式下跳过）
# ---------------------------------------------------------------------------
if not is_edit_mode:  # 只有新建模式才需要这些判断
    if not invoices:  # 票夹里一张发票都没有
        st.warning("票夹里还没有发票，请先上传发票并存入票夹，再来填写报销单。")  # 黄色提示
        if st.button("去上传发票", type="primary"):  # 引导按钮
            st.switch_page("pages/1_发票上传.py")  # 跳转到发票上传页
        st.stop()  # 停止渲染本页剩余内容

    if not pending:  # 每张发票都已经有报销单了
        st.info("票夹里的发票都已填写报销单，可在「我的报销单」查看进度。")  # 蓝色提示
        if st.button("去我的报销单", type="primary"):  # 引导按钮
            st.switch_page("pages/2_我的报销单.py")  # 跳转到我的报销单页
        st.stop()  # 停止渲染

# ---------------------------------------------------------------------------
# 第①步：选发票（发票号码由系统从票夹带出，用户不用也不能手填）
#   编辑模式下：发票固定为当前报销单关联的发票，不可更换
# ---------------------------------------------------------------------------
st.markdown("**① 选择发票**")  # 步骤标题

if is_edit_mode:
    # 编辑模式：直接用现有报销单关联的发票，不允许更换
    invoice_id = edit_form["invoice_id"]
    detail = get_invoice_detail(invoice_id)
    st.text_input(
        "关联发票（编辑模式下不可更换）",
        value=f"{detail.get('invoice_number') or '无号码'} · {detail.get('seller_name') or '-'} · {_fmt_amount(detail.get('total_amount'))}",
        disabled=True,
    )
else:
    # 新建模式：从票夹里选
    options = [inv["id"] for inv in pending]  # 下拉框的值用发票 id，避免显示名重复
    label_map = {}  # 发票 id → 下拉框显示文字
    for inv in pending:  # 逐张发票拼显示文字
        label_map[inv["id"]] = (
            f"{inv.get('invoice_number') or '无号码'} · "
            f"{(inv.get('seller_name') or '未识别销售方')[:16]} · "
            f"{_fmt_amount(inv.get('total_amount'))}"
        )

    preset = st.session_state.get("expense_form_invoice_id")  # 从发票详情/票夹跳进来时带过来的发票 id
    default_index = options.index(preset) if preset in options else 0  # 默认选中带过来的那张，否则选第一张
    invoice_id = st.selectbox(  # 发票选择框
        "从票夹选择要报销的发票",  # 控件标题
        options,  # 可选值
        index=default_index,  # 默认选中项
        format_func=lambda v: label_map.get(v, str(v)),  # 把 id 转成"号码 · 销售方 · 金额"
    )
    detail = get_invoice_detail(invoice_id)  # 取这张发票的完整信息（基本信息 + 商品明细）

# ---------------------------------------------------------------------------
# 第②步：选费用类型（决定下面要补充哪些分类信息）
#   编辑模式下：预填现有报销单的费用类型
# ---------------------------------------------------------------------------
st.markdown("**② 选择费用类型**")  # 步骤标题
invoice_data = invoice_data_from_detail(detail)  # 转成中文键字典（Agent 与类型猜测都用它）

if is_edit_mode:
    # 编辑模式：用现有报销单的费用类型
    saved_type = edit_form.get("expense_type") or ""
    type_index = EXPENSE_TYPE_NAMES.index(saved_type) if saved_type in EXPENSE_TYPE_NAMES else 0
else:
    # 新建模式：根据发票内容猜一个
    guessed = guess_expense_type(invoice_data)
    type_index = EXPENSE_TYPE_NAMES.index(guessed) if guessed in EXPENSE_TYPE_NAMES else len(EXPENSE_TYPE_NAMES) - 1

expense_type = st.selectbox(  # 费用类型下拉框
    "费用类型（决定下面要补充哪些分类信息）",  # 控件标题
    EXPENSE_TYPE_NAMES,  # 全部费用类型
    index=type_index,  # 默认选中项
    format_func=lambda name: EXPENSE_CATEGORIES[name]["icon"] + " " + name,  # 带图标显示
)
st.caption(EXPENSE_CATEGORIES[expense_type]["desc"])  # 灰色小字：这个费用类型是什么

# ---------------------------------------------------------------------------
# 第③步：行程号（同一趟出差的多张单据填同一个号，之后可以按行程分组查看）
#   编辑模式下：预填现有报销单的行程号
# ---------------------------------------------------------------------------
st.markdown("**③ 行程号**")  # 步骤标题
existing_trips = list_trip_numbers()  # 库里已有的行程号

# 编辑模式下已保存的行程号
if is_edit_mode:
    saved_trip = (edit_form.get("trip_no") or "") if edit_form else ""
else:
    saved_trip = ""

# 当前是否处于「新建输入」模式（记录在 session_state，切换后不丢）
# 初始值：编辑模式且已保存行程号不在历史里 → 直接进入新建输入
if "trip_new_mode" not in st.session_state:
    st.session_state.trip_new_mode = is_edit_mode and bool(saved_trip) and saved_trip not in existing_trips

if st.session_state.trip_new_mode:
    # ===== 新建输入模式：同一位置直接显示输入框（不再出现下拉框） =====
    # 预填值：编辑模式带过来的、且不在历史里的行程号
    prefill = saved_trip if (saved_trip and saved_trip not in existing_trips) else ""
    trip_no = st.text_input(  # 输入框（占据原下拉框的位置，不会额外多出一行）
        "行程号（新建）",  # 控件标题
        value=prefill,  # 预填值
        placeholder="例如：2026-09 常州出差",  # 提示文字
        key="new_trip_no",  # 控件key
    )
    if st.button("← 改为选择已有行程号", key="back_to_select_trip"):  # 想改选历史行程号时点它
        st.session_state.trip_new_mode = False  # 切回选择模式
        st.rerun()  # 重新渲染
else:
    # ===== 选择模式：下拉框（第一项=新建行程号，后面是已有行程号） =====
    trip_options = [NEW_TRIP] + existing_trips  # 全部选项
    default_idx = trip_options.index(saved_trip) if saved_trip in trip_options else 0  # 编辑模式预选中已保存值
    trip_choice = st.selectbox(  # 行程号下拉框
        "行程号（选择已有行程号，或选择「新建行程号」）",  # 控件标题
        trip_options,  # 全部选项
        index=default_idx,  # 默认选中项
        key="trip_select",  # 控件key
    )
    if trip_choice == NEW_TRIP:  # 选中「新建行程号」：原地切换到输入模式
        st.session_state.trip_new_mode = True  # 记录模式
        st.rerun()  # 重跑，让同一位置变成输入框
    else:  # 选中已有行程号：直接沿用
        trip_no = trip_choice

if existing_trips and not st.session_state.trip_new_mode:  # 选择模式下用小字提示已有行程号
    st.caption("已有行程号：" + "、".join(existing_trips))

# ---------------------------------------------------------------------------
# 第④⑤⑥⑦步：把整张报销单放进 st.form
#   表单里的控件改动不会触发页面重跑，填到一半不会被清空，点提交才一次性收走
# ---------------------------------------------------------------------------
with st.form("expense_form", clear_on_submit=False):  # 报销单表单
    st.markdown("**④ 发票信息（由票夹自动带出，不可修改）**")  # 发票模块：只读展示
    inv_l, inv_r = st.columns(2)  # 左右两列摆六个只读字段
    with inv_l:  # 左列
        st.text_input("发票号码", value=detail.get("invoice_number") or "无号码", disabled=True)  # 发票号码（禁用）
        st.text_input("开票日期", value=detail.get("invoice_date") or "-", disabled=True)  # 开票日期（禁用）
        st.text_input("发票类型", value=detail.get("invoice_type") or "-", disabled=True)  # 发票类型（禁用）
    with inv_r:  # 右列
        st.text_input("销售方名称", value=detail.get("seller_name") or "-", disabled=True)  # 销售方（禁用）
        st.text_input("购买方名称", value=detail.get("buyer_name") or "-", disabled=True)  # 购买方（禁用）
        st.text_input("价税合计", value=_fmt_amount(detail.get("total_amount")), disabled=True)  # 金额（禁用）

    st.markdown("**⑤ 报销单主表（所有费用类型共有）**")  # 统一主表字段
    base_l, base_r = st.columns(2)  # 左右两列

    # 编辑模式下预填现有数据
    if is_edit_mode:
        saved_occur = edit_form.get("occur_date") or ""
        try:
            default_occur = datetime.strptime(saved_occur, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            default_occur = date.today()
        default_reason = edit_form.get("reason") or ""
        default_participants = edit_form.get("participants") or ""
        default_amount = float(edit_form.get("total_amount") or 0)
    else:
        default_occur = date.today()
        default_reason = ""
        default_participants = ""
        default_amount = float(detail.get("total_amount") or 0)

    with base_l:  # 左列
        occur = st.date_input("费用发生日期", value=default_occur)  # 发生日期
        reason = st.text_input("报销事由", value=default_reason, placeholder="例如：2026年9月常州出差住宿")  # 事由
        participants = st.text_input("参与人", value=default_participants, placeholder="多人用顿号分隔")  # 参与人
    with base_r:  # 右列
        amount = st.number_input(  # 报销金额
            "报销金额（元）",  # 控件标题
            min_value=0.0,  # 最小 0
            value=default_amount,  # 默认值
            step=1.0,  # 每次点加减变化 1 元
            format="%.2f",  # 保留两位小数
        )
        st.text_input("关联发票", value=detail.get("invoice_number") or "无号码", disabled=True)  # 关联发票（只读）

    st.markdown(f"**⑥ {expense_type}的分类扩展信息**")  # 分类扩展：按费用类型条件渲染
    ext_values = {}  # 收集用户填的分类字段
    fields = EXPENSE_CATEGORIES[expense_type]["fields"]  # 当前费用类型的字段定义

    # 编辑模式下：取出已保存的扩展字段作为默认值
    saved_ext = edit_form.get("ext_fields") or {} if is_edit_mode else {}

    for start in range(0, len(fields), 3):  # 每行摆 3 个字段
        row_fields = fields[start : start + 3]  # 这一行的字段
        cols = st.columns(3)  # 三列
        for offset, (col, field) in enumerate(zip(cols, row_fields)):  # 逐个字段渲染
            with col:  # 在当前列里画控件
                # 编辑模式下传入已保存的值作为默认值
                field_default = saved_ext.get(field["key"]) if is_edit_mode else None
                ext_values[field["key"]] = _render_ext_field(field, start + offset, default_value=field_default)

    st.markdown("**⑦ 报销明细（发票上已有的明细已自动注入，可修改或增删行）**")  # 明细模块
    detail_rows = []  # data_editor 的初始行

    if is_edit_mode and edit_form.get("detail_items"):
        # 编辑模式：用报销单里保存的明细
        for item in edit_form["detail_items"]:
            detail_rows.append({
                "项目名称": item.get("项目名称") or "",
                "规格型号": item.get("规格型号") or "",
                "单位": item.get("单位") or "",
                "数量": item.get("数量") or "",
                "单价": _clean_number(item.get("单价")),
                "金额": float(item.get("金额") or 0),
            })
    else:
        # 新建模式：从发票明细搬过来
        for item in (detail.get("items") or []):
            detail_rows.append({
                "项目名称": item.get("item_name") or "",
                "规格型号": item.get("spec") or "",
                "单位": item.get("unit") or "",
                "数量": item.get("quantity") or "",
                "单价": _clean_number(item.get("unit_price")),  # 单价：长小数收成两位
                "金额": float(item.get("amount") or 0),
            })
    if not detail_rows:  # 没有明细行时给一行空行，保证表格有列可增删
        detail_rows = [{"项目名称": "", "规格型号": "", "单位": "", "数量": "", "单价": "", "金额": 0.0}]

    edited = st.data_editor(  # 可增删行的明细表格
        detail_rows,  # 初始数据
        num_rows="dynamic",  # 允许用户增加 / 删除行
        use_container_width=True,  # 表格撑满宽度
        column_config={  # 列配置
            "项目名称": st.column_config.TextColumn("项目名称", width="large"),  # 名称列宽一些
            "金额": st.column_config.NumberColumn("金额", format="%.2f"),  # 金额按两位小数显示
        },
        key=f"detail_editor_{invoice_id}",  # key 带上发票 id
    )

    # 底部两个按钮：保存草稿 / 提交审核
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        save_clicked = st.form_submit_button(  # 保存按钮：只存草稿，不进审核
            "保存报销单",
            use_container_width=True,
        )
    with btn_col2:
        submit_clicked = st.form_submit_button(  # 提交按钮：存库 + 风控 + Agent终审
            "提交报销单，进入财务终审",
            type="primary",
            use_container_width=True,
        )
    submitted = save_clicked or submit_clicked  # 任意一个被点都算提交了表单
    action = "save" if save_clicked else ("submit" if submit_clicked else None)  # 记录用户点了哪个



# ============================================================================
# 辅助函数0：保存确认弹窗（语义不通过时弹出）
# ============================================================================
@st.dialog("事由语义审核未通过", width=400)
def _show_save_confirm_dialog(semantic_msg, semantic_detail,
                               is_edit_mode, edit_form_id, edit_form, invoice_id,
                               trip_no, expense_type, occur_text, reason, amount,
                               participants, ext_values, detail_items):
    """语义审核不通过时的保存确认弹窗"""
    st.error(semantic_msg)  # 红色框，只显示原因（标题已说明是语义审核未通过）
    with st.expander("查看详细分析", expanded=False):
        st.write(semantic_detail)
    st.caption("仍要保存为草稿吗？")  # 小字
    st.caption("保存后可在「我的报销单」中继续修改。")  # 灰色小字，间距紧凑
    st.markdown("<hr style='margin: 2px 0 8px 0; border: none; border-top: 1px solid #e0e0e0;'>", unsafe_allow_html=True)  # 紧凑横线
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("确认保存草稿", type="primary", use_container_width=True, key="dialog_confirm_save"):
            _save_as_draft(
                is_edit_mode, edit_form_id, edit_form, invoice_id,
                trip_no, expense_type, occur_text, reason, amount,
                participants, ext_values, detail_items,
            )
            st.rerun()  # 关闭弹窗
    with btn_col2:
        if st.button("取消", use_container_width=True, key="dialog_cancel_save"):
            st.rerun()  # 关闭弹窗，不保存


# ============================================================================
# 辅助函数1：保存为草稿（不执行审核）
# ============================================================================
def _save_as_draft(is_edit_mode, edit_form_id, edit_form, invoice_id,
                   trip_no, expense_type, occur_text, reason, amount,
                   participants, ext_values, detail_items):
    """保存报销单为草稿状态，不执行风控和Agent审核"""
    if is_edit_mode:
        save = update_expense_form_fields(
            form_id=edit_form_id,
            trip_no=trip_no.strip(),
            expense_type=expense_type,
            occur_date=occur_text,
            reason=reason.strip(),
            total_amount=float(amount),
            participants=participants.strip(),
            ext_fields=ext_values,
            detail_items=detail_items,
        )
        form_no = edit_form.get("form_no") if edit_form else ""
    else:
        save = create_expense_form(
            invoice_id=invoice_id,
            trip_no=trip_no.strip(),
            expense_type=expense_type,
            occur_date=occur_text,
            reason=reason.strip(),
            total_amount=float(amount),
            participants=participants.strip(),
            ext_fields=ext_values,
            detail_items=detail_items,
            status="草稿",  # 草稿状态，不进审核
        )
        form_no = save.get("form_no", "")

    if not save.get("ok", True):
        st.error(save.get("error", "保存失败"))
    else:
        st.success(f"报销单已保存为草稿（单号：{form_no}），可在「我的报销单」中继续编辑或提交。")


# ============================================================================
# 辅助函数2：提交审核（保存 + 风控 + Agent终审 + 跳详情页）
# ============================================================================
def _submit_for_audit(is_edit_mode, edit_form_id, edit_form, invoice_id,
                      trip_no, expense_type, occur_text, reason, amount,
                      participants, ext_values, detail_items, invoice_data,
                      reason_check):
    """提交报销单：保存为待终审 → 风控检测 → Agent终审 → 跳详情页"""
    if is_edit_mode:
        save = update_expense_form_fields(
            form_id=edit_form_id,
            trip_no=trip_no.strip(),
            expense_type=expense_type,
            occur_date=occur_text,
            reason=reason.strip(),
            total_amount=float(amount),
            participants=participants.strip(),
            ext_fields=ext_values,
            detail_items=detail_items,
        )
        form_id_for_audit = edit_form_id
        form_no_for_agent = edit_form.get("form_no") if edit_form else ""
    else:
        save = create_expense_form(
            invoice_id=invoice_id,
            trip_no=trip_no.strip(),
            expense_type=expense_type,
            occur_date=occur_text,
            reason=reason.strip(),
            total_amount=float(amount),
            participants=participants.strip(),
            ext_fields=ext_values,
            detail_items=detail_items,
            status="待财务终审",
        )
        form_id_for_audit = save.get("id")
        form_no_for_agent = save.get("form_no")

    if not save.get("ok", True):
        st.error(save.get("error", "保存失败"))
        return

    # 风控检测 + Agent终审
    agent_payload = {
        "form_no": form_no_for_agent,
        "trip_no": trip_no.strip(),
        "expense_type": expense_type,
        "occur_date": occur_text,
        "reason": reason.strip(),
        "total_amount": float(amount),
        "participants": participants.strip(),
        "ext_fields": ext_values,
        "detail_items": detail_items,
    }
    with st.spinner("正在执行风控检测和AI终审，请稍候..."):
        audit = run_expense_form_audit(
            invoice_data, agent_payload,
            exclude_form_id=form_id_for_audit,
            employee_id=get_current_employee_id(),
        )

    # 展示风控检测结果
    anomaly_result = audit.get("anomaly_check", {})
    if anomaly_result.get("has_risk"):
        st.warning(f"风控检测发现 {anomaly_result['risk_count']} 项异常")
    else:
        st.success("风控检测通过，未发现异常")
    with st.expander("查看风控检测详情", expanded=False):
        st.code(format_anomaly_for_user(anomaly_result), language=None)

    # 解析终审结论，写回数据库
    verdict = extract_verdict(audit.get("answer")) or "不通过"
    status = "已通过" if verdict == "通过" else "已驳回"
    update_expense_form_audit(
        form_id_for_audit,
        audit_result={
            "final_result": verdict,
            "agent_answer": audit.get("answer"),
            "anomaly_check": anomaly_result,
            "reason_check": reason_check,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        status=status,
    )
    st.session_state["expense_form_id"] = form_id_for_audit
    st.session_state.pop("expense_form_invoice_id", None)
    st.session_state.pop("edit_expense_form_id", None)
    st.switch_page("pages/8_报销单详情.py")

# ---------------------------------------------------------------------------
# 提交处理：两个按钮（保存草稿 / 提交审核），都先做事由语义审核
#   保存：语义不通过时弹窗确认是否强制保存为草稿
#   提交：语义不通过时阻断，给"问AI"和"找人工"入口
# ---------------------------------------------------------------------------
if submitted:  # 用户点了保存或提交
    problems = []  # 收集基础校验不通过的原因
    if not (trip_no or "").strip():  # 行程号必须填
        problems.append("请填写行程号")
    if not reason.strip():  # 报销事由必须填
        problems.append("请填写报销事由")
    if amount <= 0:  # 金额必须大于 0
        problems.append("报销金额必须大于 0")

    if problems:  # 基础校验有问题：直接告诉用户，不往下走
        st.error("请先完善：" + "；".join(problems))
    else:  # 基础校验通过，整理明细数据
        detail_items = []  # 明细模块里用户最终确认的行
        for row in edited:  # 逐行整理
            name = str(row.get("项目名称") or "").strip()
            if not name:  # 名称为空的行直接丢掉
                continue
            row_amount = row.get("金额")
            if isinstance(row_amount, float) and row_amount != row_amount:  # NaN判断
                row_amount = None
            detail_items.append({
                "项目名称": name,
                "规格型号": row.get("规格型号") or "",
                "单位": row.get("单位") or "",
                "数量": row.get("数量") or "",
                "单价": row.get("单价") or "",
                "金额": row_amount,
            })
        occur_text = occur.strftime("%Y-%m-%d")  # 日期转字符串

        # ===== 事由语义审核（带缓存：表单内容没改就不重复执行） =====
        # 用"发票ID + 事由 + 费用类型"算一个签名，判断表单内容是否变了
        semantic_signature = hashlib.md5(
            f"{invoice_id}|{reason.strip()}|{expense_type}".encode("utf-8")
        ).hexdigest()
        cached = st.session_state.get("semantic_check_cache")  # 上次的缓存
        if cached and cached.get("signature") == semantic_signature:
            # 签名一样，说明表单内容没改，直接用缓存结果
            reason_check = cached["result"]
            st.caption("事由语义审核结果已缓存（表单内容未变更）")
        else:
            # 签名变了，重新执行语义审核
            with st.spinner("正在执行事由语义审核..."):
                reason_check = check_reason_semantic(invoice_data, reason.strip(), expense_type=expense_type)
            # 存进缓存
            st.session_state["semantic_check_cache"] = {
                "signature": semantic_signature,
                "result": reason_check,
            }

        semantic_passed = reason_check["passed"]  # 语义是否通过
        semantic_msg = reason_check["message"]  # 简短原因
        semantic_detail = reason_check["details"]  # 详细分析

        # 展示语义审核结果
        if semantic_passed and reason_check["level"] == "pass":
            st.success(f"事由语义审核通过：{semantic_msg}")
        else:
            st.warning(f"事由语义审核提醒：{semantic_msg}")
        with st.expander("查看事由语义审核详情", expanded=False):
            st.write(semantic_detail)

        # ====================================================================
        # 分支1：用户点了"保存报销单"
        # ====================================================================
        if action == "save":
            if semantic_passed:
                # 语义通过：直接保存为草稿
                _save_as_draft(
                    is_edit_mode, edit_form_id, edit_form, invoice_id,
                    trip_no, expense_type, occur_text, reason, amount,
                    participants, ext_values, detail_items,
                )
            else:
                # 语义不通过：弹出确认框，问用户是否强制保存
                _show_save_confirm_dialog(
                    semantic_msg, semantic_detail,
                    is_edit_mode, edit_form_id, edit_form, invoice_id,
                    trip_no, expense_type, occur_text, reason, amount,
                    participants, ext_values, detail_items,
                )

        # ====================================================================
        # 分支2：用户点了"提交报销单"
        # ====================================================================
        elif action == "submit":
            if not semantic_passed:
                # 语义不通过：阻断提交，给问AI和找人工入口
                st.error("事由语义审核未通过，无法提交财务终审。请修改报销事由后再提交。")
                st.divider()
                st.markdown("**你可以：**")
                help_col1, help_col2 = st.columns(2)
                with help_col1:
                    if st.button("咨询AI助手", use_container_width=True, key="ask_ai_reason"):
                        # 把当前发票和事由存到session_state，跳帮助中心咨询
                        st.session_state["pending_consult"] = {
                            "invoice_data": invoice_data,
                            "basic_check_text": "（报销单填写页跳转）事由语义审核未通过，需要咨询如何修改报销事由。",
                            "invoice_number": invoice_data.get("发票号码", ""),
                            "seller_name": invoice_data.get("销售方名称", ""),
                            "total_amount": invoice_data.get("价税合计小写", ""),
                            "final_result": "事由语义不通过",
                        }
                        st.switch_page("pages/4_帮助中心.py")
                with help_col2:
                    if st.button("联系财务人员", use_container_width=True, key="ask_human_reason"):
                        st.info("已通知财务人员，稍后会与您联系。您也可以直接前往财务部咨询。")
            else:
                # 语义通过：正常提交，执行风控+Agent终审
                _submit_for_audit(
                    is_edit_mode, edit_form_id, edit_form, invoice_id,
                    trip_no, expense_type, occur_text, reason, amount,
                    participants, ext_values, detail_items, invoice_data,
                    reason_check,
                )

