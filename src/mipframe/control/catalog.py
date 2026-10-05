"""SQLite is a persistent catalog, never a numerical execution engine."""
import sqlite3
from pathlib import Path


def create(path, rows, variables, numbers, declarations):
    with sqlite3.connect(Path(path)/'metadata.sqlite') as db:
        db.executescript('''
        CREATE TABLE rows(id INTEGER PRIMARY KEY,name TEXT UNIQUE,lo INTEGER,hi INTEGER,line INTEGER);
        CREATE TABLE variables(id INTEGER PRIMARY KEY,name TEXT UNIQUE,kind TEXT,lo INTEGER,hi INTEGER,obj INTEGER,line INTEGER);
        CREATE TABLE numbers(id INTEGER PRIMARY KEY,numerator TEXT,denominator TEXT,approx REAL);
        CREATE TABLE declarations(variable INTEGER PRIMARY KEY,kind TEXT);
        ''')
        db.executemany('INSERT INTO rows VALUES(?,?,?,?,?)', rows)
        db.executemany('INSERT INTO variables VALUES(?,?,?,?,?,?,?)', variables)
        db.executemany('INSERT INTO numbers VALUES(?,?,?,?)', numbers)
        db.executemany('INSERT INTO declarations VALUES(?,?)', declarations)


def publish_import(writer):
    def numbers():
        for i,v in enumerate(writer.values):
            try:approx=float(v)
            except OverflowError:approx=None
            yield i,str(v.numerator),str(v.denominator),approx
    create(writer.path,writer.row_data.records(),writer.var_data.records(),numbers(),((i,'CIB'[int(v)]) for i,v in enumerate(writer.var_data.columns[2])))
