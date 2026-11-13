"""
测试数据库
===========
基于 SQLite 的本地数据存储，管理测试用例、执行结果和历史记录。

设计说明：
- 使用 SQLite 作为本地数据库，无需额外安装
- 自动建表，支持数据库版本迁移
- 线程安全（使用 check_same_thread=False + 连接池）
- 提供 CRUD 操作和统计查询接口
"""

import sqlite3
import json
import threading
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from loguru import logger

from .model import TestCase, TestSuite, TestStatus, Priority


class TestDatabase:
    """
    测试数据库
    ----------
    管理测试用例和执行结果的持久化存储。
    """

    DB_VERSION = 1  # 数据库版本号，用于迁移

    def __init__(self, db_path: str = "test_results.db"):
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（线程安全）"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        """初始化数据库表结构"""
        try:
            conn = self._conn
            cursor = conn.cursor()

            # 测试用例表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS test_cases (
                    case_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    priority TEXT DEFAULT 'P1',
                    case_data TEXT NOT NULL,
                    created_at TEXT,
                    updated_at TEXT,
                    author TEXT DEFAULT '',
                    version TEXT DEFAULT '1.0'
                )
            """)

            # 测试套件表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS test_suites (
                    suite_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    suite_data TEXT NOT NULL,
                    created_at TEXT
                )
            """)

            # 执行记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_records (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suite_id TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    total_count INTEGER DEFAULT 0,
                    passed_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    skipped_count INTEGER DEFAULT 0,
                    total_duration REAL DEFAULT 0.0,
                    environment TEXT DEFAULT '',
                    operator TEXT DEFAULT '',
                    record_data TEXT DEFAULT '{}'
                )
            """)

            # 用例执行详情表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_details (
                    detail_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER,
                    case_id TEXT,
                    status TEXT,
                    duration REAL DEFAULT 0.0,
                    error_message TEXT DEFAULT '',
                    log_data TEXT DEFAULT '[]',
                    FOREIGN KEY (record_id) REFERENCES execution_records(record_id)
                )
            """)

            # 创建索引
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_cases_category
                ON test_cases(category)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_cases_priority
                ON test_cases(priority)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_details_record
                ON execution_details(record_id)
            """)

            conn.commit()
            logger.debug(f"数据库初始化完成: {self._db_path}")

            # 迁移：将已有的导入用例标记为模板
            self._migrate_import_templates()

        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")

    def _migrate_import_templates(self):
        """将 group 以'导入：'开头的已有用例标记为 is_template=True"""
        try:
            rows = self._conn.execute(
                "SELECT case_id, case_data FROM test_cases"
            ).fetchall()
            updated = 0
            for row in rows:
                data = json.loads(row["case_data"])
                group = data.get("group", "")
                if group.startswith("导入：") and not data.get("is_template", False):
                    data["is_template"] = True
                    self._conn.execute(
                        "UPDATE test_cases SET case_data = ? WHERE case_id = ?",
                        (json.dumps(data, ensure_ascii=False), row["case_id"])
                    )
                    updated += 1
            if updated:
                self._conn.commit()
                logger.info(f"迁移完成：{updated} 个导入用例已标记为模板")
        except Exception as e:
            logger.warning(f"模板迁移失败: {e}")

    # ---------- 测试用例 CRUD ----------

    def save_case(self, case: TestCase) -> bool:
        """保存或更新测试用例"""
        try:
            now = datetime.now().isoformat()
            # 空 case_id 会导致主键冲突互相覆盖，自动生成并写回用例对象
            if not case.case_id:
                case.case_id = f"case-{uuid.uuid4().hex[:12]}"
            self._conn.execute("""
                INSERT OR REPLACE INTO test_cases
                (case_id, name, description, category, tags, priority,
                 case_data, created_at, updated_at, author, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case.case_id, case.name, case.description, case.category,
                json.dumps(case.tags, ensure_ascii=False),
                case.priority.value,
                json.dumps(case.to_dict(), ensure_ascii=False),
                case.created_at.isoformat() if case.created_at else now,
                now, case.author, case.version
            ))
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"保存用例失败: {e}")
            return False

    def load_case(self, case_id: str) -> Optional[TestCase]:
        """加载单个测试用例"""
        try:
            row = self._conn.execute(
                "SELECT case_data FROM test_cases WHERE case_id = ?",
                (case_id,)
            ).fetchone()
            if row:
                data = json.loads(row["case_data"])
                return TestCase.from_dict(data)
            return None
        except Exception as e:
            logger.error(f"加载用例失败: {e}")
            return None

    def delete_case(self, case_id: str) -> bool:
        """删除测试用例"""
        try:
            self._conn.execute(
                "DELETE FROM test_cases WHERE case_id = ?", (case_id,)
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除用例失败: {e}")
            return False

    def list_cases(self, category: str = None, priority: Priority = None,
                   tag: str = None, keyword: str = None,
                   limit: int = 10000, offset: int = 0) -> List[TestCase]:
        """
        查询测试用例列表，支持多条件过滤。
        """
        try:
            query = "SELECT case_id, case_data FROM test_cases WHERE 1=1"
            params = []

            if category:
                query += " AND category = ?"
                params.append(category)
            if priority:
                query += " AND priority = ?"
                params.append(priority.value)
            if tag:
                query += " AND tags LIKE ?"
                params.append(f'%"{tag}"%')
            if keyword:
                query += " AND (name LIKE ? OR description LIKE ?)"
                params.extend([f"%{keyword}%", f"%{keyword}%"])

            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = self._conn.execute(query, params).fetchall()
            cases = []
            for row in rows:
                data = json.loads(row["case_data"])
                case = TestCase.from_dict(data)
                # 以数据库主键为准（旧数据可能由 save_case 自动生成了 ID）
                case.case_id = row["case_id"]
                cases.append(case)
            return cases

        except Exception as e:
            logger.error(f"查询用例失败: {e}")
            return []

    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT category FROM test_cases WHERE category != '' ORDER BY category"
            ).fetchall()
            return [row["category"] for row in rows]
        except Exception:
            return []

    def get_all_tags(self) -> List[str]:
        """获取所有标签"""
        try:
            rows = self._conn.execute(
                "SELECT tags FROM test_cases"
            ).fetchall()
            all_tags = set()
            for row in rows:
                tags = json.loads(row["tags"])
                all_tags.update(tags)
            return sorted(all_tags)
        except Exception:
            return []

    def get_all_groups(self) -> List[str]:
        """获取所有分组名称（从 case_data JSON 中提取 group 字段）"""
        try:
            rows = self._conn.execute(
                "SELECT case_data FROM test_cases"
            ).fetchall()
            groups = set()
            for row in rows:
                data = json.loads(row["case_data"])
                g = data.get("group", "")
                if g:
                    groups.add(g)
            return sorted(groups)
        except Exception:
            return []

    def count_cases(self) -> int:
        """统计用例总数"""
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM test_cases"
            ).fetchone()
            return row["cnt"] if row else 0
        except Exception:
            return 0

    # ---------- 执行记录 ----------

    def save_execution(self, suite: TestSuite, environment: str = "",
                       operator: str = "") -> Optional[int]:
        """
        保存执行记录。

        返回:
            record_id: 记录 ID
        """
        try:
            now = datetime.now().isoformat()
            cursor = self._conn.execute("""
                INSERT INTO execution_records
                (suite_id, start_time, end_time, total_count, passed_count,
                 failed_count, error_count, skipped_count, total_duration,
                 environment, operator, record_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                suite.suite_id, now, now,
                suite.total_count, suite.passed_count,
                suite.failed_count, suite.error_count, suite.skipped_count,
                suite.total_duration, environment, operator,
                json.dumps(suite.to_dict(), ensure_ascii=False)
            ))
            record_id = cursor.lastrowid

            # 保存每个用例的执行详情
            for case in suite.test_cases:
                self._conn.execute("""
                    INSERT INTO execution_details
                    (record_id, case_id, status, duration, error_message, log_data)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    record_id, case.case_id, case.status.value,
                    case.duration, case.error_message,
                    json.dumps(case.log_entries, ensure_ascii=False)
                ))

            self._conn.commit()
            logger.info(f"执行记录已保存: ID={record_id}")
            return record_id

        except Exception as e:
            logger.error(f"保存执行记录失败: {e}")
            self._conn.rollback()
            return None

    def get_execution_history(self, limit: int = 50) -> List[Dict]:
        """获取执行历史"""
        try:
            rows = self._conn.execute("""
                SELECT * FROM execution_records
                ORDER BY start_time DESC LIMIT ?
            """, (limit,)).fetchall()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"查询执行历史失败: {e}")
            return []

    def get_execution_details(self, record_id: int) -> List[Dict]:
        """获取某次执行的详细结果"""
        try:
            rows = self._conn.execute("""
                SELECT * FROM execution_details WHERE record_id = ?
                ORDER BY detail_id
            """, (record_id,)).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"查询执行详情失败: {e}")
            return []

    def delete_execution_record(self, record_id: int) -> bool:
        """删除一次执行记录及其用例详情"""
        try:
            self._conn.execute(
                "DELETE FROM execution_details WHERE record_id = ?",
                (record_id,)
            )
            self._conn.execute(
                "DELETE FROM execution_records WHERE record_id = ?",
                (record_id,)
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除执行记录失败: {e}")
            return False

    # ---------- 统计查询 ----------

    def get_statistics(self, days: int = 30) -> Dict[str, Any]:
        """获取测试统计信息"""
        try:
            total_cases = self.count_cases()

            # 最近 N 天的执行统计
            rows = self._conn.execute("""
                SELECT
                    COUNT(*) as exec_count,
                    SUM(passed_count) as total_passed,
                    SUM(failed_count) as total_failed,
                    SUM(error_count) as total_error,
                    SUM(total_count) as total_run,
                    AVG(total_duration) as avg_duration
                FROM execution_records
                WHERE start_time >= datetime('now', ?)
            """, (f"-{days} days",)).fetchone()

            if rows and rows["exec_count"]:
                total_run = rows["total_run"]
                total_passed = rows["total_passed"] or 0
                return {
                    "total_cases": total_cases,
                    "executions_in_period": rows["exec_count"],
                    "total_run": total_run,
                    "total_passed": total_passed,
                    "total_failed": rows["total_failed"] or 0,
                    "total_error": rows["total_error"] or 0,
                    "pass_rate": round(total_passed / total_run * 100, 2) if total_run > 0 else 0,
                    "avg_duration": round(rows["avg_duration"] or 0, 2),
                }
            else:
                return {
                    "total_cases": total_cases,
                    "executions_in_period": 0,
                    "pass_rate": 0,
                }

        except Exception as e:
            logger.error(f"统计查询失败: {e}")
            return {}

    # ---------- 清理 ----------

    def close(self):
        """关闭数据库连接"""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def vacuum(self):
        """压缩数据库"""
        try:
            self._conn.execute("VACUUM")
            logger.info("数据库已压缩")
        except Exception as e:
            logger.error(f"数据库压缩失败: {e}")
