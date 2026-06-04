# Minimal Flask-SQLAlchemy-like DB helper using sqlite3
import sqlite3
import os
from flask import g, current_app


class DB:
    """Lightweight SQLite wrapper, no SQLAlchemy dependency."""

    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app):
        app.teardown_appcontext(self._close_db)

    @property
    def db_path(self):
        return current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

    def get_conn(self):
        if 'db_conn' not in g:
            g.db_conn = sqlite3.connect(self.db_path)
            g.db_conn.row_factory = sqlite3.Row
            g.db_conn.execute('PRAGMA journal_mode=WAL')
            g.db_conn.execute('PRAGMA foreign_keys=ON')
        return g.db_conn

    def execute(self, sql, params=None):
        conn = self.get_conn()
        cursor = conn.execute(sql, params or ())
        conn.commit()
        return cursor

    def query(self, sql, params=None):
        conn = self.get_conn()
        return conn.execute(sql, params or ()).fetchall()

    def query_one(self, sql, params=None):
        conn = self.get_conn()
        return conn.execute(sql, params or ()).fetchone()

    def executemany(self, sql, params_list):
        conn = self.get_conn()
        conn.executemany(sql, params_list)
        conn.commit()

    @staticmethod
    def _close_db(exc):
        conn = g.pop('db_conn', None)
        if conn is not None:
            conn.close()


db = DB()
