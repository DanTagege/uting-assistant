"""清理平台里的 mock 演示数据，只保留真实账号，让网页看板显示真实数字。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402

KEEP = {"16639373818"}


def main() -> None:
    db.conn()
    rows = db.q("SELECT username FROM accounts")
    removed = []
    for r in rows:
        user = r["username"]
        if user in KEEP:
            continue
        db.ex("DELETE FROM jobs WHERE account=?", (user,))
        db.ex("DELETE FROM hosting WHERE username=?", (user,))
        db.ex("DELETE FROM mock_state WHERE account=?", (user,))
        db.ex("DELETE FROM accounts WHERE username=?", (user,))
        removed.append(user)
    # 清掉孤儿任务（mock 时期产生的）
    db.ex("DELETE FROM jobs WHERE account NOT IN (SELECT username FROM accounts)")
    db.ex("DELETE FROM job_logs WHERE job_id NOT IN (SELECT id FROM jobs)")
    db.ex("DELETE FROM papers")
    db.ex("DELETE FROM answer_bank WHERE source LIKE 'mock%' OR source LIKE 'file:%'")

    accounts = db.one("SELECT COUNT(*) AS c FROM accounts")["c"]
    jobs = db.one("SELECT COUNT(*) AS c FROM jobs")["c"]
    hosted = db.one("SELECT COUNT(*) AS c FROM hosting WHERE active=1")["c"]
    print(f"已清理 {len(removed)} 个演示账号：{removed}")
    print(f"现在：accounts={accounts} jobs={jobs} hosted={hosted}")


if __name__ == "__main__":
    main()
