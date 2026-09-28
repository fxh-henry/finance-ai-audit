# -*- coding: utf-8 -*-
"""修复保存草稿后跳转逻辑"""
path = r"D:\agent\财务报销项目\pages\7_报销单填写.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''def _save_as_draft(is_edit_mode, edit_form_id, edit_form, invoice_id,
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
        st.success(f"报销单已保存为草稿（单号：{form_no}），可在「我的报销单」中继续编辑或提交。")'''

new = '''def _save_as_draft(is_edit_mode, edit_form_id, edit_form, invoice_id,
                   trip_no, expense_type, occur_text, reason, amount,
                   participants, ext_values, detail_items):
    """保存报销单为草稿状态，不执行风控和Agent审核。保存成功后跳转到详情页。"""
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
        saved_form_id = edit_form_id
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
        saved_form_id = save.get("id")

    if not save.get("ok", True):
        st.error(save.get("error", "保存失败"))
    else:
        # 保存成功：跳到详情页，让用户看到刚保存的草稿
        st.session_state["expense_form_id"] = saved_form_id
        st.session_state.pop("edit_expense_form_id", None)
        st.session_state.pop("expense_form_invoice_id", None)
        st.switch_page("pages/8_报销单详情.py")'''

if old not in content:
    print("ERROR: old block not found")
    raise SystemExit(1)

content = content.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("OK")
