# -*- coding: utf-8 -*-
"""分析数据库结构"""
import sqlite3

conn = sqlite3.connect(r'D:\agent\财务报销项目\database\finance_audit.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 列出所有表
print('=' * 70)
print('一、数据库中的所有表')
print('=' * 70)
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
for t in tables:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    count = cur.fetchone()[0]
    print(f'  {t}: {count} 条记录')

print()
print('=' * 70)
print('二、每张表的字段结构')
print('=' * 70)
for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = cur.fetchall()
    print(f'\n【{t}】({len(cols)}个字段)')
    for c in cols:
        pk = 'PK' if c['pk'] else ''
        print(f'  {c["name"]:25s} {c["type"]:10s} {pk}')

print()
print('=' * 70)
print('三、索引')
print('=' * 70)
cur.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")
for r in cur.fetchall():
    print(f'  {r["name"]}  (表: {r["tbl_name"]})')
    print(f'    {r["sql"]}')

conn.close()
