# -*- coding: utf-8 -*-
path = r'D:\agent\财务报销项目\pages\7_报销单填写.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            with st.spinner("正在执行风控检测和AI终审，请稍候..."):  # 转圈提示
                # 传入 form_id_for_audit 排除当前报销单，避免风控把自己判为重复报销
                # 传入 employee_id 让风控检测用当前员工的历史数据
                audit = run_expense_form_audit(invoice_data, agent_payload, exclude_form_id=form_id_for_audit, employee_id=get_current_employee_id())

            # ===== 展示风控检测结果 =====
            anomaly_result = audit.get("anomaly_check", {})
            if anomaly_result.get("has_risk"):
                st.warning(f"风控检测发现 {anomaly_result['risk_count']} 项异常")
            else:
                st.success("风控检测通过，未发现异常")
            with st.expander("查看风控检测详情", expanded=False):
                st.code(format_anomaly_for_user(anomaly_result), language=None)'''

new = '''            with st.spinner("正在执行事由语义审核、风控检测和AI终审，请稍候..."):  # 转圈提示
                # ===== 第1步：报销事由语义审核 =====
                reason_check = check_reason_semantic(invoice_data, reason.strip(), expense_type=expense_type)

                # ===== 第2步：风控检测 + Agent终审 =====
                # 传入 form_id_for_audit 排除当前报销单，避免风控把自己判为重复报销
                # 传入 employee_id 让风控检测用当前员工的历史数据
                audit = run_expense_form_audit(invoice_data, agent_payload, exclude_form_id=form_id_for_audit, employee_id=get_current_employee_id())

            # ===== 展示报销事由语义审核结果 =====
            if reason_check["level"] == "pass":
                st.success(f"事由语义审核通过：{reason_check['message']}")
            else:
                st.warning(f"事由语义审核提醒：{reason_check['message']}")
            with st.expander("查看事由语义审核详情", expanded=False):
                st.write(reason_check["details"])

            # ===== 展示风控检测结果 =====
            anomaly_result = audit.get("anomaly_check", {})
            if anomaly_result.get("has_risk"):
                st.warning(f"风控检测发现 {anomaly_result['risk_count']} 项异常")
            else:
                st.success("风控检测通过，未发现异常")
            with st.expander("查看风控检测详情", expanded=False):
                st.code(format_anomaly_for_user(anomaly_result), language=None)'''

content = content.replace(old, new, 1)

# 把语义审核结果也存入audit_result
old2 = '''                    "anomaly_check": anomaly_result,  # 风控检测结果
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 审核时间'''
new2 = '''                    "anomaly_check": anomaly_result,  # 风控检测结果
                    "reason_check": reason_check,  # 报销事由语义审核结果
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 审核时间'''
content = content.replace(old2, new2, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
