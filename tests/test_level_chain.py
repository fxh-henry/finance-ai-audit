# -*- coding: utf-8 -*-
"""临时验证脚本：员工职级 -> 消费标准 链路"""
import sys
sys.path.insert(0, ".")
from database.db import get_employee, list_employees
from agent.tools.audit_tools import expense_standard_tool, set_audit_context, get_audit_context
from agent.review_agent import build_system_prompt

print("=" * 62)
print("测试1：按ID查员工（含职级）")
for e in list_employees():
    print(f"  {e['id']} {e['name']} {e['department']} {e['level']}")
li = get_employee(2)
print(f"  get_employee(2) -> {li}")

print("=" * 62)
print("测试2：未注入上下文 + 模型漏传 level（修复前的静默降级路径）")
set_audit_context(None)
r = expense_standard_tool.invoke({"expense_type": "住宿费", "amount": 480, "city": "上海"})
print(f"  passed={r['passed']} needs_human={r['needs_human']}")
print(f"  caliber={r['caliber']}")
print(f"  message={r['message']}")

print("=" * 62)
print("测试3：注入李四(部门经理)上下文 + 模型漏传 level")
set_audit_context(get_employee(2))
print(f"  注入的上下文: {get_audit_context()}")
r = expense_standard_tool.invoke({"expense_type": "住宿费", "amount": 480, "city": "上海"})
print(f"  passed={r['passed']} standard={r['standard']} exceed={r['exceed']}")
print(f"  caliber={r['caliber']}")

print("=" * 62)
print("测试4：换成张三(普通员工) 同一张发票")
set_audit_context(get_employee(1))
r = expense_standard_tool.invoke({"expense_type": "住宿费", "amount": 480, "city": "上海"})
print(f"  passed={r['passed']} standard={r['standard']} exceed={r['exceed']}")
print(f"  caliber={r['caliber']}")

print("=" * 62)
print("测试5：模型显式传 level 时以模型传参为准")
set_audit_context(get_employee(1))
r = expense_standard_tool.invoke({"expense_type": "住宿费", "amount": 480, "city": "上海", "level": "部门经理"})
print(f"  passed={r['passed']} standard={r['standard']} caliber={r['caliber']}")

print("=" * 62)
print("测试6：提示词是否注入职级")
set_audit_context(None)
p = build_system_prompt({"发票号码": "X", "价税合计小写": "480"}, employee=get_employee(2))
hit = [ln.strip() for ln in p.splitlines() if "职级" in ln or "报销人" in ln]
for ln in hit:
    print("  " + ln)

print("=" * 62)
print("测试7：反思与守门的判据是否已经一致")
from utils.reflection import _standard_missing as rf
from utils.verdict_guard import _is_standard_missing as vg
samples = [
    {"standard": 400, "passed": False, "message": "住宿费超标：实际480元，超标80元"},
    {"standard": 0, "passed": None, "needs_human": True, "message": "职级[总裁]不在已知职级中，需人工确认"},
    {"standard": None, "passed": None, "needs_human": True, "message": "缺少出差城市信息"},
]
for s in samples:
    print(f"  reflection={rf(s)}  verdict_guard={vg(s)}  {'一致' if rf(s) == vg(s) else '不一致!!'}")