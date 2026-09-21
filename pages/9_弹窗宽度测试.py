# -*- coding: utf-8 -*-
"""弹窗宽度测试页：切换不同宽度，实时预览效果"""
import streamlit as st

st.set_page_config(page_title="弹窗宽度测试", layout="wide")

st.header("弹窗宽度测试")
st.caption("选择不同宽度，点击按钮预览弹窗效果")

# 侧边栏选择宽度
with st.sidebar:
    st.markdown("### 弹窗宽度设置")
    width_mode = st.radio(
        "宽度模式",
        ["small", "large", "自定义像素"],
        index=1,  # 默认large
    )
    if width_mode == "自定义像素":
        custom_width = st.slider("像素宽度", min_value=300, max_value=900, value=500, step=50)
        dialog_width = custom_width
    else:
        dialog_width = width_mode

    st.divider()
    st.markdown("### 测试内容")
    test_msg = st.text_input("警告文字", value="事由语义审核未通过：事由\"吃饭\"与保险服务发票明显不匹配。")
    test_detail = st.text_area(
        "详细分析",
        value="报销事由\"吃饭\"描述的是餐饮费用，但发票销售方为永安财产保险股份有限公司，项目名称为\"*金融服务*驾乘人员人身意外伤害保险\"，属于保险费用。两者费用性质完全不符，建议修改报销事由为\"员工意外伤害保险费\"或类似表述。",
        height=120,
    )

st.info(f"当前弹窗宽度：{dialog_width}")

# 测试不同宽度的弹窗
@st.dialog("事由语义审核未通过", width=dialog_width)
def test_dialog(msg, detail):
    st.error(msg)  # 红色框，只显示原因
    with st.expander("查看详细分析", expanded=False):
        st.write(detail)
    st.caption("仍要保存为草稿吗？")  # 小字
    st.caption("保存后可在「我的报销单」中继续修改。")  # 灰色小字
    st.markdown("<hr style='margin: 0px 0 8px 0; border: none; border-top: 1px solid #e0e0e0;'>", unsafe_allow_html=True)  # 紧凑横线
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("确认保存草稿", type="primary", use_container_width=True):
            st.success("已保存（测试）")
            st.rerun()
    with btn_col2:
        if st.button("取消", use_container_width=True):
            st.rerun()

if st.button("打开测试弹窗", type="primary"):
    test_dialog(test_msg, test_detail)

st.divider()
st.markdown("### 宽度参考")
st.markdown("""
| 宽度 | 适用场景 |
|---|---|
| small (~400px) | 简短确认，文字少 |
| 500px | 中等内容，推荐 |
| 600px | 内容较多，有详细分析 |
| large (~800px) | 内容很多，需要并排布局 |
""")
st.caption("Streamlit弹窗内容会自动换行，文字不会溢出，只是行宽不同。")
