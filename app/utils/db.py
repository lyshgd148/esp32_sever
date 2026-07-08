import pymysql
from dbutils.pooled_db import PooledDB

Pool = PooledDB(
    creator=pymysql,
    maxconnections=10,
    mincached=2,
    maxcached=5,
    blocking=True,
    setsession=[],
    ping=0,
    host="127.0.0.1",
    port=3306,
    user="lys",
    passwd="l147",
    charset="utf8",
    db="lys_test_db",
)


def fetch_one(sql, args=None):
    conn = Pool.connection()
    cursor = conn.cursor()
    cursor.execute(sql, args or [])
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result


def fetch_all(sql, args=None):
    conn = Pool.connection()
    cursor = conn.cursor()
    cursor.execute(sql, args or [])
    result = cursor.fetchall()
    cursor.close()
    conn.close()
    return result
