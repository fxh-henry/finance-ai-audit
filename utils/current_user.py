# -*- coding: utf-8 -*-
"""当前员工工具：轻量多员工模式，页面顶部切换身份，不做登录"""
import streamlit as st  # 导入 Streamlit，用于读写 session_state

from database.db import get_default_employee_id, list_employees  # 数据库员工查询


def get_current_employee_id():
    """
    获取当前操作的员工ID。
    优先从 session_state 取用户切换后的员工，没有则用默认员工（E001）。
    非Streamlit环境下直接返回默认员工ID。
    """
    try:
        emp_id = st.session_state.get("current_employee_id")
        if emp_id:
            return emp_id
    except Exception:
        pass  # 非Streamlit环境，st.session_state不可用
    return get_default_employee_id()


def get_current_employee():
    """获取当前员工的完整信息（id/emp_no/name/department/level）"""
    emp_id = get_current_employee_id()
    for emp in list_employees():
        if emp["id"] == emp_id:
            return emp
    return None


def render_employee_selector():
    """
    在侧边栏渲染员工切换下拉框。
    切换后把员工ID存入 session_state，全页面生效。
    """
    employees = list_employees()
    if not employees:
        return

    # 构造下拉框显示文字："张三 · 销售部 · 普通员工"
    options = [emp["id"] for emp in employees]
    label_map = {
        emp["id"]: f"{emp['name']} · {emp['department']} · {emp['level']}"
        for emp in employees
    }

    # 当前选中的员工ID
    current_id = get_current_employee_id()
    default_index = options.index(current_id) if current_id in options else 0

    selected = st.sidebar.selectbox(
        "当前身份",
        options,
        index=default_index,
        format_func=lambda v: label_map.get(v, str(v)),
        key="employee_selector",
    )

    # 切换后更新 session_state
    if selected != st.session_state.get("current_employee_id"):
        st.session_state["current_employee_id"] = selected
        st.rerun()  # 刷新页面，让所有数据按新身份加载
