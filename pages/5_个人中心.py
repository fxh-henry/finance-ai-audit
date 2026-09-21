# -*- coding: utf-8 -*-
"""个人中心：占位页面，后续展示员工信息"""
import streamlit as st  # 导入 Streamlit

st.set_page_config(page_title="个人中心", layout="wide")  # 页面配置：标签标题 + 宽屏布局

with st.container(key="pagehead"):  # 统一页头容器（与其他页面保持完全一致的标题样式）
    st.header("个人中心")  # 统一字号的大标题
    st.caption("员工个人信息与报销偏好设置")  # 标题下的灰色说明
    st.divider()  # 灰色横线，间距由全局样式统一控制

with st.container(border=True):  # 带边框的卡片容器，让空页面也显得完整
    st.markdown("**员工信息**")  # 卡片小标题
    st.caption("姓名 / 工号 / 部门 / 职级等信息将在后续版本接入。")  # 灰色说明文字