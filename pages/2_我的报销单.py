# -*- coding: utf-8 -*-
"""我的报销单：按行程号分组展示全部报销单 + 逐单状态 + 详情入口"""
import importlib  # 用于重新加载数据库模块，避免热重载时用到旧代码

import streamlit as st  # 导入 Streamlit

import database.db as _db  # 引入数据库模块（查询报销单）
from utils.expense_form import ext_fields_summary
from utils.current_user import get_current_employee_id  # 引入“分类扩展字段 → 一行小字”的工具

_db = importlib.reload(_db)  # 强制重新加载，保证拿到最新版本的函数
list_expense_forms = _db.list_expense_forms  # 取出“查询报销单列表”的函数

st.set_page_config(page_title="我的报销单", layout="wide")  # 页面配置：标签标题 + 宽屏布局

STATUS_COLOR = {  # 单据状态 → 展示颜色（Streamlit 的 :颜色[文字] 语法）
    "已通过": "green",  # 终审通过：绿色
    "已驳回": "red",  # 终审驳回：红色
    "待财务终审": "orange",  # 等待终审：橙色
}


def _fmt_amount(value):  # 工具函数：把金额格式化成 ¥1,234.00
    if value is None or value == "":  # 金额为空（可能是识别失败）
        return "-"  # 显示一个短横线
    try:  # 正常情况是数字
        return f"¥{float(value):,.2f}"  # 千分位 + 保留两位小数
    except (TypeError, ValueError):  # 不是数字时原样显示，至少不报错
        return str(value)


def _status_text(status):  # 把状态渲染成带颜色的文字
    status = status or "待财务终审"  # 没有状态时按“待财务终审”展示
    color = STATUS_COLOR.get(status, "gray")  # 查不到就统一用灰色
    return ":" + color + "[" + status + "]"  # 返回 Markdown 彩色文字


forms = list_expense_forms(employee_id=get_current_employee_id())  # 取出当前员工的全部报销单

trips = []  # 行程号列表：保持数据库返回的顺序
grouped = {}  # 行程号 → 该行程下的报销单列表
for form in forms:  # 逐单归组
    trip = (form.get("trip_no") or "").strip() or "未分组行程"  # 没填行程号的归到“未分组行程”
    if trip not in grouped:  # 第一次见到这个行程号
        grouped[trip] = []  # 先建空列表
        trips.append(trip)  # 记下顺序
    grouped[trip].append(form)  # 把这张单放进对应行程

# ---------------------------------------------------------------------------
# 页头：左边标题 + 统计，右边“填写报销单”入口（与其他页面同一套标题样式）
# ---------------------------------------------------------------------------
with st.container(key="pagehead"):  # 统一页头容器：样式由 app.py 统一控制
    head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")  # 左标题、右按钮，两列底部对齐
    with head_l:  # 左侧：标题区
        st.header("我的报销单")  # 统一字号的大标题
        st.caption(f"共 {len(forms)} 张报销单 · {len(trips)} 个行程")  # 灰色小字：单据数与行程数
    with head_r:  # 右侧：新建入口
        if st.button("＋ 填写报销单", type="primary", use_container_width=True):  # 主色按钮
            st.switch_page("pages/7_报销单填写.py")  # 跳转到报销单填写页
    st.divider()  # 灰色横线，间距由全局样式统一控制

if not forms:  # 一张报销单都没有
    with st.container(border=True):  # 带边框的卡片容器，让空页面也显得完整
        st.markdown("**暂无报销单**")  # 卡片小标题
        st.caption("请先在「我的票夹」里选一张发票，点击卡片上的「填写报销单」开始。")  # 引导文字
    st.stop()  # 没有数据就不用往下渲染了

# ---------------------------------------------------------------------------
# 按行程号分组：每个行程一张卡片，卡片里逐单列出
# ---------------------------------------------------------------------------
for trip in trips:  # 逐个行程渲染
    trip_forms = grouped[trip]  # 这个行程下的所有单据
    total = sum(float(f.get("total_amount") or 0) for f in trip_forms)  # 行程合计金额
    passed = sum(1 for f in trip_forms if (f.get("status") or "") == "已通过")  # 该行程已通过的单据数

    with st.container(border=True):  # 一个行程 = 一张带边框的卡片
        st.markdown(f"#### 📁 行程　{trip}")  # 行程标题
        st.caption(f"{len(trip_forms)} 张单据 · 合计 {_fmt_amount(total)} · 已通过 {passed} 张")  # 行程汇总

        for form in trip_forms:  # 逐单渲染一行
            left, right = st.columns([5, 1], vertical_alignment="center")  # 左信息、右按钮
            with left:  # 左侧：单据信息
                st.markdown(  # 第一行：单号 + 状态
                    f"**{form.get('form_no') or '-'}**　{_status_text(form.get('status'))}"
                )
                st.caption(  # 第二行：费用类型 / 销售方 / 金额 / 发票号码 / 提交时间
                    f"{form.get('expense_type') or '未分类'} · {form.get('seller_name') or '未识别销售方'} · "
                    f"{_fmt_amount(form.get('total_amount'))} · 发票 {form.get('invoice_number') or '-'} · "
                    f"提交于 {form.get('submit_time') or '-'}"
                )
                ext_text = ext_fields_summary(form.get("expense_type"), form.get("ext_fields"))  # 分类扩展字段
                if ext_text:  # 有填分类字段才显示第三行
                    st.caption(ext_text)  # 灰色小字显示（例如：入住城市：常州 · 住宿晚数：2）
            with right:  # 右侧：详情按钮
                if st.button("查看", key=f"form_open_{form['id']}", use_container_width=True):  # 每行按钮 key 唯一
                    st.session_state["expense_form_id"] = form["id"]  # 记录要查看的报销单 id
                    st.switch_page("pages/8_报销单详情.py")  # 跳转到报销单详情页（该页不在侧边栏显示）
