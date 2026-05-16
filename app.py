import os
import random
import uuid
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, session, jsonify, g

app = Flask(__name__)
app.secret_key = 'experiment-secret-key-2026'

DATABASE = 'data/experiment.db'


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                participant_id TEXT DEFAULT '',
                group_type TEXT DEFAULT '',
                show_baseline TEXT DEFAULT '',
                phase1_total INTEGER DEFAULT 0,
                phase1_correct INTEGER DEFAULT 0,
                phase1_score REAL DEFAULT 0.0,
                phase2_total INTEGER DEFAULT 0,
                phase2_correct INTEGER DEFAULT 0,
                epsilon REAL DEFAULT 0.0,
                phase2_adjusted REAL DEFAULT 0.0,
                phase2_comparison TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        ''')
        # 兼容旧表：如果 show_baseline 列不存在则添加
        try:
            conn.execute('ALTER TABLE sessions ADD COLUMN show_baseline TEXT DEFAULT ""')
        except:
            pass


def generate_problems(n=100, exclude=None):
    """生成 n 道不重复的 30-50 范围内加减法题（a, b 均在 [30, 50]）"""
    exclude = exclude or set()
    problems = []
    seen = set(exclude)
    while len(problems) < n:
        op = random.choice(['+', '-'])
        a = random.randint(30, 50)
        b = random.randint(30, 50)
        if op == '-':
            a, b = max(a, b), min(a, b)
            answer = a - b
        else:
            answer = a + b
        if (a, b, op) not in seen:
            seen.add((a, b, op))
            problems.append({"a": a, "b": b, "op": op, "answer": answer})
    return problems


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/experiment')
def experiment():
    return render_template('experiment.html')


@app.route('/api/start', methods=['POST'])
def start_experiment():
    """初始化会话，生成题目"""
    session_id = str(uuid.uuid4())
    participant_id = request.json.get('participant_id', '')

    phase1_problems = generate_problems(100)
    phase2_problems = generate_problems(100, exclude={(p['a'], p['b']) for p in phase1_problems})

    db = get_db()
    db.execute(
        'INSERT INTO sessions (id, participant_id, created_at) VALUES (?, ?, ?)',
        (session_id, participant_id, datetime.now().isoformat())
    )
    db.commit()

    session['session_id'] = session_id
    session['phase1_problems'] = phase1_problems
    session['phase2_problems'] = phase2_problems

    return jsonify({
        'session_id': session_id,
        'phase1_problems': phase1_problems,
        'phase2_problems': phase2_problems
    })


@app.route('/api/phase1_submit', methods=['POST'])
def phase1_submit():
    """第一阶段提交：记录答对数量"""
    answers = request.json.get('answers', [])  # [{a, b, user_answer}, ...]
    correct = sum(1 for item in answers if item.get('user_answer') == item.get('correct_answer'))

    db = get_db()
    db.execute(
        'UPDATE sessions SET phase1_total = ?, phase1_correct = ?, phase1_score = ? WHERE id = ?',
        (len(answers), correct, correct * 0.02, session.get('session_id'))
    )
    db.commit()

    # 随机分组并计算 epsilon
    group_type = random.choice(['high_volatility', 'low_volatility'])
    if group_type == 'low_volatility':
        epsilon = round(random.uniform(-0.1, 0.1), 4)
    else:
        epsilon = round(random.uniform(-0.3, 0.3), 4)

    session['group_type'] = group_type
    session['epsilon'] = epsilon
    session['phase1_correct'] = correct

    db.execute(
        'UPDATE sessions SET group_type = ? WHERE id = ?',
        (group_type, session.get('session_id'))
    )
    db.commit()

    return jsonify({'correct': correct, 'group_type': group_type})


@app.route('/api/save_baseline', methods=['POST'])
def save_baseline():
    """保存基线显示选择"""
    show_baseline = request.json.get('show_baseline', 'false')
    db = get_db()
    db.execute(
        'UPDATE sessions SET show_baseline = ? WHERE id = ?',
        (show_baseline, session.get('session_id'))
    )
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/phase2_submit', methods=['POST'])
def phase2_submit():
    """第二阶段提交：计算调整后积分并比较"""
    answers = request.json.get('answers', [])
    correct = sum(1 for item in answers if item.get('user_answer') == item.get('correct_answer'))

    epsilon = session.get('epsilon', 0)
    phase1_correct = session.get('phase1_correct', 0)
    phase2_adjusted = round(correct * (1 + epsilon), 4)
    comparison = 'high' if phase2_adjusted > phase1_correct else 'low'

    db = get_db()
    db.execute(
        'UPDATE sessions SET phase2_total = ?, phase2_correct = ?, epsilon = ?, phase2_adjusted = ?, phase2_comparison = ? WHERE id = ?',
        (len(answers), correct, epsilon, phase2_adjusted, comparison, session.get('session_id'))
    )
    db.commit()

    return jsonify({'comparison': comparison})


@app.route('/admin')
def admin():
    return render_template('admin.html')


@app.route('/api/admin/sessions')
def admin_sessions():
    """返回所有会话数据"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT * FROM sessions ORDER BY created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/admin/stats')
def admin_stats():
    """返回汇总统计"""
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT * FROM sessions').fetchall()

    total = len(rows)
    high = sum(1 for r in rows if r['group_type'] == 'high_volatility')
    low = sum(1 for r in rows if r['group_type'] == 'low_volatility')
    avg_p1 = sum(r['phase1_correct'] for r in rows) / total if total > 0 else 0
    avg_p2 = sum(r['phase2_correct'] for r in rows) / total if total > 0 else 0

    return jsonify({
        'total_sessions': total,
        'high_volatility': high,
        'low_volatility': low,
        'avg_phase1_correct': round(avg_p1, 2),
        'avg_phase2_correct': round(avg_p2, 2),
    })


# 启动时自动初始化数据库（gunicorn 和 python app.py 都会执行）
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
