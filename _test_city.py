# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\agent\财务报销项目")
from audit.expense_standard import check_expense_standard, get_city_tier

print("湖南 →", get_city_tier("湖南"))
print("长沙 →", get_city_tier("长沙"))
print("宿迁 →", get_city_tier("宿迁"))
print("北京 →", get_city_tier("北京"))
print("空 →", get_city_tier(""))

r = check_expense_standard("住宿费", city="湖南", level="普通员工", amount=300)
print("湖南300元:", r["passed"], r["caliber"])

r = check_expense_standard("住宿费", city="长沙", level="普通员工", amount=300)
print("长沙300元:", r["passed"], r["caliber"])

r = check_expense_standard("住宿费", city="宿迁", level="普通员工", amount=300)
print("宿迁300元:", r["passed"], r["caliber"])
