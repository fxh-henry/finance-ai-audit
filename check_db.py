import sqlite3

conn = sqlite3.connect('database/finance_audit.db')
cursor = conn.cursor()

# 查看所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('=== 数据库中的表 ===')
for t in tables:
    print(f'  - {t[0]}')

print()

# 查看每个表的结构和数据
for t in tables:
    table_name = t[0]
    print(f'=== 表: {table_name} ===')

    # 表结构
    cursor.execute(f'PRAGMA table_info({table_name})')
    columns = cursor.fetchall()
    print('字段:')
    for col in columns:
        print(f'  {col[1]} ({col[2]})')

    # 数据量
    cursor.execute(f'SELECT COUNT(*) FROM {table_name}')
    count = cursor.fetchone()[0]
    print(f'数据量: {count} 条')

    # 样例数据（前3条）
    if count > 0:
        cursor.execute(f'SELECT * FROM {table_name} LIMIT 3')
        rows = cursor.fetchall()
        print('样例数据:')
        for row in rows:
            print(f'  {row}')
    print()

conn.close()
