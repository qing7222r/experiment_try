import os
import json
import random
import uuid
import sqlite3
import psycopg2
import psycopg2.extras
from datetime import datetime
from flask import Flask, render_template_string, request, session, jsonify, g, Response

app = Flask(__name__)
app.secret_key = 'experiment-secret-key-2026'

DATABASE_URL = os.environ.get('DATABASE_URL', '')
DATABASE = 'data/experiment.db'
USING_PG = bool(DATABASE_URL)

# ==================== 内联模板 ====================

INDEX_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>行为实验</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="container">
        <h1>欢迎参加行为实验</h1>
        <p class="subtitle">请仔细阅读指导语后开始</p>

        <div class="card">
            <div class="form-group">
                <label for="participant-id">请输入您的被试编号：</label>
                <input type="text" id="participant-id" placeholder="例如：P001" autofocus>
            </div>
            <button id="start-btn" class="btn btn-primary">开始实验</button>
            <p id="error-msg" class="error"></p>
        </div>
    </div>

    <script>
        document.getElementById('start-btn').addEventListener('click', async () => {
            const participantId = document.getElementById('participant-id').value.trim();
            const errorMsg = document.getElementById('error-msg');

            if (!participantId) {
                errorMsg.textContent = '请输入被试编号';
                return;
            }

            errorMsg.textContent = '';
            document.getElementById('start-btn').disabled = true;
            document.getElementById('start-btn').textContent = '初始化中...';

            try {
                const resp = await fetch('/api/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ participant_id: participantId })
                });
                const data = await resp.json();
                sessionStorage.setItem('session_id', data.session_id);
                sessionStorage.setItem('phase1_problems', JSON.stringify(data.phase1_problems));
                sessionStorage.setItem('phase2a_problems', JSON.stringify(data.phase2a_problems));
                sessionStorage.setItem('phase2b_problems', JSON.stringify(data.phase2b_problems));
                window.location.href = '/experiment';
            } catch (e) {
                errorMsg.textContent = '连接失败，请刷新页面重试';
                document.getElementById('start-btn').disabled = false;
                document.getElementById('start-btn').textContent = '开始实验';
            }
        });

        document.getElementById('participant-id').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                document.getElementById('start-btn').click();
            }
        });
    </script>
</body>
</html>'''

EXPERIMENT_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>行为实验</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <!-- 第一阶段指导语 -->
    <div id="instructions-p1" class="screen active">
        <div class="container">
            <h1>第一阶段</h1>
            <div class="card instructions-card">
                <p>你需要在 <strong>1分钟</strong> 内完成尽可能多的加减法题。</p>
                <p>每做对一道题 <strong>积1分</strong>，可获得 <strong>0.02元</strong>。</p>
                <p>题目会逐题出现，在输入框中填写答案后，点击"下一题"按钮或按 <strong>回车键</strong> 继续。</p>
                <p>未作答的题目将不计分。</p>
                <p>本实验预计<strong>5分钟</strong>完成。</p>
                <p class="wait-hint">请仔细阅读以上指导语（<span id="p1-countdown">10</span> 秒后可开始）</p>
                <button id="start-p1-btn" class="btn btn-primary" disabled>开始做题</button>
            </div>
        </div>
    </div>

    <!-- 休息页面 1：第一阶段 → 第二阶段（不显示基线） -->
    <div id="break-screen-1" class="screen">
        <div class="container">
            <h1>第一阶段结束</h1>
            <div class="card">
                <p>请稍作休息。</p>
                <p>准备好后点击下方按钮进入第二阶段。</p>
                <button id="to-p2a-btn" class="btn btn-primary">进入第二阶段</button>
            </div>
        </div>
    </div>

    <!-- 第二阶段指导语（不显示基线） -->
    <div id="instructions-p2a" class="screen">
        <div class="container">
            <h1>第二阶段</h1>
            <div class="card instructions-card">
                <p>你需要在 <strong>1分钟</strong> 内完成尽可能多的加减法题。你的奖励将取决于你的表现，规则如下：</p>
                <div class="rule-box">
                    <p><strong>第一阶段积分 = 第一阶段答对题数</strong></p>
                    <p><strong>第二阶段积分 = 第二阶段答对题数 × (1 + ε)</strong></p>
                    <p>其中 ε 是一个随机系数，<strong id="epsilon-range-p2a"></strong>内<strong>服从均匀分布</strong>。</p>
                    <p>如果 <strong>第二阶段积分 > 第一阶段积分</strong>，获得<strong class="text-high">高奖金 5元</strong>；</p>
                    <p>如果 <strong>第二阶段积分 < 第一阶段积分</strong>，获得<strong class="text-low">低奖金 1元</strong>。</p>
                </div>
                <div class="example-box">
                    <p><strong>示例：</strong></p>
                    <p>假设 第一阶段答对题数 = <strong>40</strong>（第一阶段积分 = 40），ε = <strong>0.20</strong>，第二阶段答对题数 = <strong>35</strong>。</p>
                    <p>第二阶段积分 = 35 × (1 + 0.20) = <strong>42</strong></p>
                    <p>42 > 40，所以获得<strong class="text-high">高奖金</strong>。</p>
                </div>
                <div class="quiz-box" id="quiz-box-p2a">
                    <p><strong>请计算以下题目，答对后方可进入实验：</strong></p>
                    <p id="quiz-question-p2a"></p>
                    <div class="quiz-input-row">
                        <span>第二阶段积分 = </span>
                        <input type="number" id="quiz-answer-p2a" placeholder="保留两位小数" autocomplete="off" step="0.01">
                    </div>
                    <div class="quiz-input-row">
                        <span>获得的奖金是：</span>
                        <select id="quiz-bonus-p2a">
                            <option value="">-- 请选择 --</option>
                            <option value="high">高奖金 5元</option>
                            <option value="low">低奖金 1元</option>
                        </select>
                        <button id="quiz-submit-btn-p2a" class="btn btn-primary btn-sm">提交</button>
                    </div>
                    <p id="quiz-feedback-p2a" class="quiz-feedback"></p>
                </div>
                <p class="calc-hint">切出小程序没关系，可以用手机的计算器计算。</p>
                <p class="volatility-hint">现在引入了波动ε，收益和作对的题数不是线性关系了！</p>
                <p class="wait-hint">请仔细阅读以上指导语（<span id="p2a-countdown">20</span> 秒后可开始）</p>
                <button id="start-p2a-btn" class="btn btn-primary" disabled>开始做题</button>
            </div>
        </div>
    </div>

    <!-- 休息页面 2：第二阶段不显示基线 → 第二阶段显示基线 -->
    <div id="break-screen-2" class="screen">
        <div class="container">
            <h1>第二轮结束</h1>
            <div class="card result-card">
                <p id="p2a-result-text" class="result-text"></p>
                <p class="subtitle">请根据您刚才完成的任务和获得奖金的经历，在下列问题中选择最符合您真实感受的选项（1 = 完全不同意，7 = 完全同意）。</p>

                <div class="question-item">
                    <p class="question-text">1. 我认为我能获得高额奖金（5元），主要取决于我投入的努力。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="p2a_q1" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q1" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q1" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q1" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q1" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q1" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q1" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <div class="question-item">
                    <p class="question-text">2. 整体来看，我觉得这轮游戏是公平的。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="p2a_q2" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q2" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q2" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q2" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q2" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q2" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="p2a_q2" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <p id="p2a-questionnaire-error" class="error"></p>
                <button id="to-p2b-btn" class="btn btn-primary">进入下一轮</button>
            </div>
        </div>
    </div>

    <!-- 第三轮指导语（显示基线） -->
    <div id="instructions-p2b" class="screen">
        <div class="container">
            <h1>第三轮</h1>
            <div class="card instructions-card">
                <p>规则与上一轮相同，<strong>现在你将看到第一阶段的成绩作为基线参考</strong>。</p>
                <div class="rule-box">
                    <p><strong>第一阶段积分 = 第一阶段答对题数</strong>（即<strong>基线</strong>）</p>
                    <p><strong>第二阶段积分 = 第二阶段答对题数 × (1 + ε)</strong></p>
                    <p>其中 ε 是一个随机系数，<strong id="epsilon-range-p2b"></strong>内<strong>服从均匀分布</strong>。</p>
                    <p id="guaranteed-bonus" class="guaranteed-bonus" style="display:none;">本轮游戏你做对 <strong id="guaranteed-count"></strong> 题一定能获得高奖金（5元）。</p>
                    <p>如果 <strong>第二阶段积分 > 第一阶段积分</strong>，获得<strong class="text-high">高奖金 5元</strong>；</p>
                    <p>如果 <strong>第二阶段积分 < 第一阶段积分</strong>，获得<strong class="text-low">低奖金 1元</strong>。</p>
                </div>
                <button id="start-p2b-btn" class="btn btn-primary">开始做题</button>
            </div>
        </div>
    </div>

    <!-- 问卷页面 - 第1页 -->
    <div id="questionnaire-screen" class="screen">
        <div class="container">
            <h1>第三轮结束</h1>
            <div class="card result-card">
                <p id="p2b-result-text" class="result-text"></p>
            </div>
            <p class="subtitle">请根据您刚才完成的任务和获得奖金的经历，在下列问题中选择最符合您真实感受的选项（1 = 完全不同意，7 = 完全同意）。</p>

            <div class="card questionnaire-card" id="questionnaire-page-1">
                <div class="question-item">
                    <p class="question-text">1. 我认为我能获得高额奖金（5元），主要取决于我投入的努力。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="q1" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="q1" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="q1" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="q1" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="q1" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="q1" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="q1" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <div class="question-item">
                    <p class="question-text">2. 整体来看，我觉得这轮游戏是公平的。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="q2" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="q2" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="q2" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="q2" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="q2" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="q2" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="q2" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <p id="questionnaire-page1-error" class="error"></p>
                <div class="questionnaire-nav">
                    <button id="questionnaire-next-btn" class="btn btn-primary">下一页</button>
                </div>
            </div>

            <div class="card questionnaire-card" id="questionnaire-page-2" style="display:none;">
                <div class="question-item">
                    <p class="question-text">3. 我感觉自己能够通过努力控制最终的收入水平。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="q3" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="q3" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="q3" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="q3" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="q3" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="q3" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="q3" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <div class="question-item">
                    <p class="question-text">4. 我认为任务中的随机波动对我最终收入影响很大。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="q4" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="q4" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="q4" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="q4" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="q4" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="q4" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="q4" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <div class="question-item">
                    <p class="question-text">5. 在面对随机波动时，我更倾向于加倍努力来保证收入。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="q5" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="q5" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="q5" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="q5" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="q5" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="q5" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="q5" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <div class="question-item">
                    <p class="question-text">6. 如果让您选择参加一个答题项目，我愿意选择高波动、高回报的项目。</p>
                    <div class="likert-scale">
                        <label class="likert-option"><input type="radio" name="q6" value="1"><span>1</span></label>
                        <label class="likert-option"><input type="radio" name="q6" value="2"><span>2</span></label>
                        <label class="likert-option"><input type="radio" name="q6" value="3"><span>3</span></label>
                        <label class="likert-option"><input type="radio" name="q6" value="4"><span>4</span></label>
                        <label class="likert-option"><input type="radio" name="q6" value="5"><span>5</span></label>
                        <label class="likert-option"><input type="radio" name="q6" value="6"><span>6</span></label>
                        <label class="likert-option"><input type="radio" name="q6" value="7"><span>7</span></label>
                    </div>
                    <div class="likert-labels">
                        <span>完全不同意</span>
                        <span>完全同意</span>
                    </div>
                </div>

                <p id="questionnaire-page2-error" class="error"></p>
                <div class="questionnaire-nav">
                    <button id="questionnaire-prev-btn" class="btn btn-secondary">上一页</button>
                    <button id="questionnaire-submit-btn" class="btn btn-primary">提交问卷</button>
                </div>
            </div>
        </div>
    </div>

    <!-- 结果页面 -->
    <div id="result-screen" class="screen">
        <div class="container">
            <h1>实验结束</h1>
            <div class="card result-card">
                <p class="thank-you">感谢您的参与！</p>
            </div>
        </div>
    </div>

    <!-- 做题页面（所有阶段共用） -->
    <div id="problem-screen" class="screen">
        <div class="problem-container">
            <div class="timer-bar">
                <div class="timer-center">
                    <div class="timer-main">
                        <span>剩余时间：</span>
                        <span id="timer" class="timer">01:00</span>
                    </div>
                    <div id="timer-epsilon" class="timer-epsilon" style="display:none;"></div>
                </div>
                <div id="baseline-counter" class="baseline-counter" style="display:none;">
                    目前做对 <span id="p2-correct-count">0</span> / 第一阶段 <span id="p1-correct-count">0</span> 题
                </div>
            </div>
            <div class="problem-area">
                <div class="problem-display">
                    <span id="num-a">23</span>
                    <span id="problem-op" class="operator">+</span>
                    <span id="num-b">47</span>
                    <span class="operator">=</span>
                    <span class="question-mark">?</span>
                </div>
                <div class="answer-area">
                    <input type="number" id="answer-input" placeholder="输入答案" autocomplete="off">
                </div>
            </div>
            <button id="submit-btn" class="btn btn-submit">下一题 →</button>
        </div>
    </div>

    <script src="/static/experiment.js"></script>
</body>
</html>'''

ADMIN_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>管理后台 - 行为实验</title>
    <link rel="stylesheet" href="/static/style.css">
    <style>
        .admin-table { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 14px; }
        .admin-table th, .admin-table td { border: 1px solid #ddd; padding: 8px 10px; text-align: center; }
        .admin-table th { background: #4a90d9; color: #fff; position: sticky; top: 0; }
        .admin-table tr:nth-child(even) { background: #f9f9f9; }
        .admin-table tr:hover { background: #e8f4fd; }
        .table-wrapper { max-height: 500px; overflow-y: auto; border: 1px solid #ddd; border-radius: 8px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: #f0f7ff; border-radius: 8px; padding: 15px; text-align: center; }
        .stat-card .stat-value { font-size: 28px; font-weight: bold; color: #4a90d9; }
        .stat-card .stat-label { font-size: 13px; color: #666; margin-top: 4px; }
        .high-badge { background: #e74c3c; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .low-badge { background: #3498db; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .btn-csv { margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="container admin-container">
        <h1>管理后台</h1>
        <div class="admin-header">
            <a href="/" class="btn btn-secondary">返回首页</a>
            <button id="refresh-btn" class="btn btn-primary">刷新数据</button>
            <button id="csv-btn" class="btn btn-primary btn-csv">导出 CSV</button>
        </div>

        <div id="stats-area" class="stats-grid"></div>

        <div id="backup-status" class="backup-status"></div>

        <h2>会话记录</h2>
        <div class="table-wrapper">
            <table class="admin-table">
                <thead>
                    <tr>
                        <th>被试编号</th>
                        <th>分组</th>
                        <th>P1 总数</th>
                        <th>P1 答对</th>
                        <th>P1 用时</th>
                        <th>P1 积分</th>
                        <th>P2a 总数</th>
                        <th>P2a 答对</th>
                        <th>P2a 用时</th>
                        <th>P2a 比较</th>
                        <th>P2b 总数</th>
                        <th>P2b 答对</th>
                        <th>P2b 用时</th>
                        <th>ε</th>
                        <th>P2b 调整积分</th>
                        <th>比较结果</th>
                        <th>P2a Q1</th>
                        <th>P2a Q2</th>
                        <th>Q1</th>
                        <th>Q2</th>
                        <th>Q3</th>
                        <th>Q4</th>
                        <th>Q5</th>
                        <th>Q6</th>
                        <th>时间</th>
                    </tr>
                </thead>
                <tbody id="sessions-tbody"></tbody>
            </table>
        </div>
    </div>

    <script>
        const GROUP_LABELS = { high_volatility: '高波动组', low_volatility: '低波动组' };
        const BK_KEY = 'experiment_data_backup';

        function showBackupStatus(serverCount) {
            let raw = localStorage.getItem(BK_KEY);
            let backup = raw ? JSON.parse(raw) : null;
            let backupCount = backup ? backup.sessions.length : 0;
            let backupTime = backup ? new Date(backup.time).toLocaleString('zh-CN') : '';
            let el = document.getElementById('backup-status');
            if (!backupTime) {
                el.innerHTML = '';
                el.className = 'backup-status';
                return;
            }
            let cls = 'backup-status backup-ok';
            let extra = '';
            if (serverCount === 0 && backupCount > 0) {
                cls = 'backup-status backup-warn';
                extra = ' | <span style="color:#e74c3c;">服务器数据为空！点“导出 CSV”可保存本地备份</span>';
            }
            el.className = cls;
            el.innerHTML = '\u{1F4BE} 本地备份: ' + backupTime + ' (共 ' + backupCount + ' 条)' + extra;
        }

        function fmtTimes(t) {
            if (!t) return '-';
            try { const a = JSON.parse(t); if (a.length === 0) return '-'; return a.length + '题 均' + (a.reduce((s,v)=>s+v,0)/a.length).toFixed(1) + 's'; } catch(e) { return '-'; }
        }
        function downloadCSV(sessions, filename) {
            const headers = ['被试编号', '分组', 'P1总数', 'P1答对', 'P1用时(s)', 'P1积分', 'P2a总数', 'P2a答对', 'P2a用时(s)', 'P2a比较', 'P2b总数', 'P2b答对', 'P2b用时(s)', 'ε', 'P2b调整积分', '比较结果', 'P2a Q1', 'P2a Q2', 'Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', '时间'];
            const rows = sessions.map(s => [
                s.participant_id,
                GROUP_LABELS[s.group_type] || '',
                s.phase1_total,
                s.phase1_correct,
                s.p1_times || '',
                s.phase1_score,
                s.phase2a_total,
                s.phase2a_correct,
                s.p2a_times || '',
                s.phase2a_comparison === 'high' ? '高奖金' : s.phase2a_comparison === 'low' ? '低奖金' : '',
                s.phase2_total,
                s.phase2_correct,
                s.p2b_times || '',
                s.epsilon,
                s.phase2_adjusted,
                s.phase2_comparison === 'high' ? '高奖金' : s.phase2_comparison === 'low' ? '低奖金' : '',
                s.p2a_q1 || '',
                s.p2a_q2 || '',
                s.q1 || '',
                s.q2 || '',
                s.q3 || '',
                s.q4 || '',
                s.q5 || '',
                s.q6 || '',
                s.created_at
            ]);
            const csv = [headers, ...rows].map(row => row.join(',')).join('\n');
            const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            a.click();
        }

        async function loadData() {
            const [sessionsResp, statsResp] = await Promise.all([
                fetch('/api/admin/sessions'),
                fetch('/api/admin/stats')
            ]);
            const sessions = await sessionsResp.json();
            const stats = await statsResp.json();

            // 自动备份到浏览器本地存储
            localStorage.setItem(BK_KEY, JSON.stringify({
                time: new Date().toISOString(),
                sessions: sessions
            }));
            showBackupStatus(sessions.length);

            document.getElementById('stats-area').innerHTML = [
                { value: stats.total_sessions, label: '总被试数' },
                { value: stats.high_volatility, label: '高波动组' },
                { value: stats.low_volatility, label: '低波动组' },
                { value: stats.avg_phase1_correct, label: '第一阶段平均答对' },
                { value: stats.avg_phase2_correct, label: '第二阶段平均答对' }
            ].map(s => '<div class="stat-card"><div class="stat-value">' + s.value + '</div><div class="stat-label">' + s.label + '</div></div>').join('');

            const tbody = document.getElementById('sessions-tbody');
            tbody.innerHTML = sessions.map(s => {
                let groupBadge = '';
                if (s.group_type === 'high_volatility') groupBadge = '<span class="high-badge">高波动组</span>';
                else if (s.group_type === 'low_volatility') groupBadge = '<span class="low-badge">低波动组</span>';
                else groupBadge = '-';
                let comparison2a = '-';
                if (s.phase2a_comparison === 'high') comparison2a = '高奖金';
                else if (s.phase2a_comparison === 'low') comparison2a = '低奖金';
                let comparison = '-';
                if (s.phase2_comparison === 'high') comparison = '高奖金';
                else if (s.phase2_comparison === 'low') comparison = '低奖金';
                let time = s.created_at ? new Date(s.created_at).toLocaleString('zh-CN') : '-';
                return '<tr>' +
                    '<td>' + (s.participant_id || '-') + '</td>' +
                    '<td>' + groupBadge + '</td>' +
                    '<td>' + s.phase1_total + '</td>' +
                    '<td>' + s.phase1_correct + '</td>' +
                    '<td>' + fmtTimes(s.p1_times) + '</td>' +
                    '<td>' + s.phase1_score + '</td>' +
                    '<td>' + (s.phase2a_total || '-') + '</td>' +
                    '<td>' + (s.phase2a_correct || '-') + '</td>' +
                    '<td>' + fmtTimes(s.p2a_times) + '</td>' +
                    '<td>' + comparison2a + '</td>' +
                    '<td>' + s.phase2_total + '</td>' +
                    '<td>' + s.phase2_correct + '</td>' +
                    '<td>' + fmtTimes(s.p2b_times) + '</td>' +
                    '<td>' + s.epsilon + '</td>' +
                    '<td>' + s.phase2_adjusted + '</td>' +
                    '<td>' + comparison + '</td>' +
                    '<td>' + (s.p2a_q1 || '-') + '</td>' +
                    '<td>' + (s.p2a_q2 || '-') + '</td>' +
                    '<td>' + (s.q1 || '-') + '</td>' +
                    '<td>' + (s.q2 || '-') + '</td>' +
                    '<td>' + (s.q3 || '-') + '</td>' +
                    '<td>' + (s.q4 || '-') + '</td>' +
                    '<td>' + (s.q5 || '-') + '</td>' +
                    '<td>' + (s.q6 || '-') + '</td>' +
                    '<td>' + time + '</td>' +
                    '</tr>';
            }).join('');
        }

        function exportCSV() {
            fetch('/api/admin/sessions')
                .then(r => r.json())
                .then(sessions => {
                    if (sessions.length === 0) {
                        // 服务器没数据，尝试从本地备份导出
                        let raw = localStorage.getItem(BK_KEY);
                        if (raw) {
                            let backup = JSON.parse(raw);
                            if (backup.sessions.length > 0) {
                                downloadCSV(backup.sessions, 'experiment_data_本地备份.csv');
                                return;
                            }
                        }
                        alert('没有可导出的数据');
                        return;
                    }
                    downloadCSV(sessions, 'experiment_data.csv');
                });
        }

        document.getElementById('refresh-btn').addEventListener('click', loadData);
        document.getElementById('csv-btn').addEventListener('click', exportCSV);
        showBackupStatus(0);
        loadData();
    </script>
</body>
</html>'''

STYLE_CSS = r'''*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: #f0f4f8;
    color: #333;
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
}

.container {
    width: 100%;
    max-width: 1100px;
    padding: 30px 20px;
}

.admin-container { max-width: 1100px; }

h1 { text-align: center; font-size: 28px; margin-bottom: 8px; color: #2c3e50; }
h2 { font-size: 20px; margin: 20px 0 10px; color: #2c3e50; }
.subtitle { text-align: center; color: #7f8c8d; margin-bottom: 24px; }

.card {
    background: #fff;
    border-radius: 12px;
    padding: 30px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
}

.card p { font-size: 16px; line-height: 1.8; margin-bottom: 8px; }

.instructions-card p { font-size: 22px; line-height: 2.0; margin-bottom: 12px; }
.instructions-card h1 { font-size: 36px; }

.rule-box {
    background: #fef9e7;
    border: 2px solid #f9e79f;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 14px 0;
}
.rule-box p { font-size: 20px; }

.text-high { color: #27ae60; }
.text-low { color: #e74c3c; }

.example-box {
    background: #eaf7ee;
    border: 2px solid #a9dfbf;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 14px 0;
}
.example-box p { font-size: 20px; }

.quiz-box {
    background: #eaf2f8;
    border: 2px solid #aed6f1;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 14px 0;
}
.quiz-box p { font-size: 20px; }
.quiz-input-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 12px 0;
    flex-wrap: wrap;
}
.quiz-input-row span { font-size: 20px; }
#quiz-answer {
    width: 160px;
    padding: 10px 14px;
    font-size: 20px;
    text-align: center;
    border: 2px solid #ddd;
    border-radius: 8px;
    outline: none;
}
#quiz-answer:focus { border-color: #4a90d9; }
#quiz-answer::placeholder { font-size: 14px; color: #aaa; }
.btn-sm { padding: 10px 20px; font-size: 16px; }
.quiz-feedback { margin-top: 8px; font-size: 18px; font-weight: bold; }
.quiz-success { color: #27ae60; }
.quiz-error { color: #e74c3c; }

.wait-hint { color: #e67e22; font-weight: bold; font-size: 18px !important; }

.rule-box p,
.example-box p,
.quiz-box p {
    white-space: normal;
    word-break: break-word;
}

#quiz-bonus {
    padding: 10px 14px;
    font-size: 18px;
    border: 2px solid #ddd;
    border-radius: 8px;
    outline: none;
    background: #fff;
    cursor: pointer;
}
#quiz-bonus:focus { border-color: #4a90d9; }

.form-group { margin-bottom: 20px; }
.form-group label { display: block; font-size: 15px; margin-bottom: 8px; color: #555; }
.form-group input {
    width: 100%;
    padding: 12px 16px;
    font-size: 18px;
    border: 2px solid #ddd;
    border-radius: 8px;
    outline: none;
    transition: border-color 0.2s;
}
.form-group input:focus { border-color: #4a90d9; }

.btn {
    display: inline-block;
    padding: 12px 28px;
    font-size: 16px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s;
    font-weight: 500;
}
.btn:active { transform: scale(0.97); }
.btn-primary { background: #4a90d9; color: #fff; }
.btn-primary:hover { background: #357abd; }
.btn-primary:disabled { background: #a0c4e8; cursor: not-allowed; }
.btn-secondary { background: #95a5a6; color: #fff; text-decoration: none; }
.btn-secondary:hover { background: #7f8c8d; }

.problem-container {
    width: 100%;
    max-width: 500px;
    padding: 20px;
    position: relative;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    justify-content: center;
}

.timer-bar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    align-items: center;
    gap: 8px 20px;
    padding: 14px 20px;
    background: #fff;
    box-shadow: 0 1px 6px rgba(0,0,0,0.1);
    font-size: 16px;
    z-index: 10;
    min-height: 56px;
}
.timer-center {
    display: flex;
    flex-direction: column;
    align-items: center;
}
.timer-main {
    display: flex;
    align-items: center;
    gap: 6px;
}
.timer {
    font-size: 36px;
    font-weight: bold;
    color: #e74c3c;
    font-variant-numeric: tabular-nums;
}
.timer.warning { animation: pulse 0.5s ease-in-out infinite alternate; }
@keyframes pulse { from { opacity: 1; } to { opacity: 0.4; } }

.timer-epsilon {
    font-size: 15px;
    color: #555;
    margin-top: 2px;
}

.baseline-counter {
    font-size: 17px;
    font-weight: bold;
    color: #2c3e50;
    white-space: nowrap;
}

.calc-hint {
    color: #888;
    font-size: 18px !important;
    font-style: italic;
}

.volatility-hint {
    color: #e74c3c;
    font-weight: bold;
    font-size: 22px !important;
    margin: 12px 0;
}

.baseline-choice {
    background: #f0f4f8;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 16px 0;
}
.baseline-choice p { font-size: 18px; margin-bottom: 10px; }
.radio-label {
    display: inline-block;
    font-size: 18px;
    margin-right: 30px;
    cursor: pointer;
}
.radio-label input[type="radio"] {
    width: 18px;
    height: 18px;
    margin-right: 6px;
    vertical-align: middle;
    cursor: pointer;
}

.guaranteed-bonus {
    color: #27ae60;
    font-weight: bold;
    margin-top: 6px;
}

.problem-area {
    text-align: center;
    margin: 60px 0 30px;
}

.problem-display {
    font-size: 56px;
    font-weight: bold;
    color: #2c3e50;
    letter-spacing: 8px;
    user-select: none;
}
.operator { color: #4a90d9; margin: 0 4px; }
.question-mark { color: #e74c3c; }

.answer-area { text-align: center; margin-bottom: 20px; }
#answer-input {
    width: 200px;
    padding: 14px 18px;
    font-size: 28px;
    text-align: center;
    border: 2px solid #ddd;
    border-radius: 10px;
    outline: none;
    transition: border-color 0.2s;
    -moz-appearance: textfield;
}
#answer-input::-webkit-inner-spin-button,
#answer-input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
#answer-input:focus { border-color: #4a90d9; }

.btn-submit {
    position: fixed;
    bottom: 30px;
    right: 30px;
    padding: 14px 32px;
    font-size: 18px;
    background: #27ae60;
    color: #fff;
    border: none;
    border-radius: 10px;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(39,174,96,0.3);
    transition: all 0.2s;
}
.btn-submit:hover { background: #219a52; }
.btn-submit:active { transform: scale(0.95); }

.error { color: #e74c3c; margin-top: 10px; font-size: 14px; }

.screen { display: none; }
.screen.active { display: flex; justify-content: center; align-items: center; min-height: 100vh; }

.result-card { text-align: center; }
.result-text { font-size: 32px; font-weight: bold; margin: 20px 0; }
.result-high { color: #27ae60; }
.result-low { color: #e74c3c; }
.thank-you { font-size: 16px; color: #999; margin-top: 20px; }

/* 问卷样式 */
.questionnaire-card { text-align: left; max-width: 800px; margin: 0 auto; }
.questionnaire-section { margin-bottom: 24px; }
.section-title { font-size: 22px; color: #2c3e50; margin-bottom: 16px; border-bottom: 2px solid #4a90d9; padding-bottom: 8px; }
.question-item { margin-bottom: 24px; }
.question-text { font-size: 18px; line-height: 1.6; margin-bottom: 12px; color: #333; }
.likert-scale { display: flex; justify-content: center; gap: 16px; margin-bottom: 6px; }
.likert-option {
    display: flex;
    flex-direction: column;
    align-items: center;
    cursor: pointer;
    user-select: none;
}
.likert-option input[type="radio"] {
    width: 20px;
    height: 20px;
    cursor: pointer;
    accent-color: #4a90d9;
}
.likert-option span {
    font-size: 14px;
    color: #666;
    margin-top: 4px;
}
.likert-labels {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    color: #999;
    margin-bottom: 8px;
    padding: 0 20px;
}
.questionnaire-submit-area { text-align: center; margin-top: 30px; }
.questionnaire-submit-area .btn { font-size: 18px; padding: 14px 40px; }
.questionnaire-nav { display: flex; justify-content: center; gap: 16px; margin-top: 24px; }
.categorical-options { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
.categorical-option { font-size: 17px; cursor: pointer; display: flex; align-items: center; gap: 8px; }
.categorical-option input[type="radio"] { width: 18px; height: 18px; cursor: pointer; accent-color: #4a90d9; }

.admin-header { display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }

.backup-status { padding: 8px 16px; border-radius: 6px; margin-bottom: 12px; font-size: 14px; }
.backup-ok { background: #eaf7ee; color: #27ae60; }
.backup-warn { background: #fdf2f2; color: #c0392b; }'''

EXPERIMENT_JS = r'''// ===== 全局状态 =====
let currentPhase = '1';              // '1', '2a', '2b'
let problems = [];                  // 当前阶段题目数组
let currentIndex = 0;               // 当前题目索引
let answers = [];                   // 已提交的答案
let timerSeconds = 60;              // 倒计时秒数
let timerInterval = null;           // 计时器句柄
let quizPassed = false;             // P2 测验是否通过
let p2QuizAnswer = 0;              // P2 测验正确答案
let questionStartTime = 0;          // 当前题目显示的时间戳

// ===== 屏幕切换 =====
function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
}

// ===== 第一阶段指导语：强制停留 10 秒 =====
(function initP1Wait() {
    const btn = document.getElementById('start-p1-btn');
    const countdownEl = document.getElementById('p1-countdown');
    let remaining = 10;
    countdownEl.textContent = remaining;
    const interval = setInterval(() => {
        remaining--;
        countdownEl.textContent = remaining;
        if (remaining <= 0) {
            clearInterval(interval);
            btn.disabled = false;
            btn.textContent = '开始做题';
            document.querySelector('#instructions-p1 .wait-hint').style.display = 'none';
        }
    }, 1000);

    btn.addEventListener('click', () => {
        currentPhase = '1';
        problems = JSON.parse(sessionStorage.getItem('phase1_problems') || '[]');
        currentIndex = 0;
        answers = [];
        timerSeconds = 60;
        startProblemRound();
    });
})();

// ===== 休息页 1 → 第二阶段（不显示基线） =====
document.getElementById('to-p2a-btn').addEventListener('click', () => {
    sessionStorage.setItem('show_baseline', 'false');
    fetch('/api/save_baseline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ show_baseline: 'false' })
    });
    showScreen('instructions-p2a');
    initP2aWait();
});

// ===== 第二阶段（不显示基线）指导语 =====
let p2aWaitTimer = null;
let p2aCanStart = false;

function generateP2aQuiz() {
    const p1Score = Math.floor(Math.random() * 21) + 20;
    const groupType = sessionStorage.getItem('group_type');
    const epsRange = groupType === 'high_volatility' ? 0.50 : 0.10;
    const eps = (Math.random() * epsRange * 2 - epsRange).toFixed(2);
    const p2Correct = Math.floor(Math.random() * 21) + 15;
    p2QuizAnswer = parseFloat((p2Correct * (1 + parseFloat(eps))).toFixed(2));
    window._p2aQuizP1Score = p1Score;

    document.getElementById('quiz-question-p2a').innerHTML =
        '假设 第一阶段答对题数 = <strong>' + p1Score + '</strong>（第一阶段积分 = ' + p1Score + '），' +
        'ε = <strong>' + eps + '</strong>，第二阶段答对题数 = <strong>' + p2Correct + '</strong>。' +
        '请计算 第二阶段积分（<strong>保留两位小数</strong>），并判断获得高奖金还是低奖金。';
    document.getElementById('quiz-feedback-p2a').textContent = '';
    document.getElementById('quiz-feedback-p2a').className = 'quiz-feedback';
    document.getElementById('quiz-answer-p2a').value = '';
    document.getElementById('quiz-bonus-p2a').value = '';
}

function initP2aWait() {
    const btn = document.getElementById('start-p2a-btn');
    const countdownEl = document.getElementById('p2a-countdown');
    let remaining = 20;
    countdownEl.textContent = remaining;
    p2aCanStart = false;
    quizPassed = false;
    btn.disabled = true;
    btn.textContent = '请先完成测验并等待倒计时';
    document.querySelector('#instructions-p2a .wait-hint').style.display = '';

    const groupType = sessionStorage.getItem('group_type');
    const epsilonEl = document.getElementById('epsilon-range-p2a');
    if (groupType === 'high_volatility') {
        epsilonEl.textContent = 'ε 在 [-0.50, 0.50] 区间';
    } else {
        epsilonEl.textContent = 'ε 在 [-0.10, 0.10] 区间';
    }

    generateP2aQuiz();

    p2aWaitTimer = setInterval(() => {
        remaining--;
        countdownEl.textContent = remaining;
        if (remaining <= 0) {
            clearInterval(p2aWaitTimer);
            p2aWaitTimer = null;
            p2aCanStart = true;
            document.querySelector('#instructions-p2a .wait-hint').style.display = 'none';
            checkP2aReady();
        }
    }, 1000);
}

function checkP2aReady() {
    const btn = document.getElementById('start-p2a-btn');
    if (p2aCanStart && quizPassed) {
        btn.disabled = false;
        btn.textContent = '开始做题';
    }
}

document.getElementById('quiz-submit-btn-p2a').addEventListener('click', () => {
    const userAnswer = parseFloat(document.getElementById('quiz-answer-p2a').value);
    const bonusChoice = document.getElementById('quiz-bonus-p2a').value;
    const feedback = document.getElementById('quiz-feedback-p2a');

    if (isNaN(userAnswer)) {
        feedback.textContent = '请输入第二阶段积分的数值（保留两位小数）。';
        feedback.className = 'quiz-feedback quiz-error';
        return;
    }
    if (!bonusChoice) {
        feedback.textContent = '请选择获得的是高奖金还是低奖金。';
        feedback.className = 'quiz-feedback quiz-error';
        return;
    }

    const calcCorrect = Math.abs(userAnswer - p2QuizAnswer) < 0.005;
    const expectedBonus = p2QuizAnswer > window._p2aQuizP1Score ? 'high' : 'low';
    const bonusCorrect = bonusChoice === expectedBonus;

    if (calcCorrect && bonusCorrect) {
        feedback.textContent = '正确！第二阶段积分 = ' + p2QuizAnswer.toFixed(2) + '，获得' + (expectedBonus === 'high' ? '高奖金' : '低奖金') + '。';
        feedback.className = 'quiz-feedback quiz-success';
        quizPassed = true;
        document.getElementById('quiz-submit-btn-p2a').disabled = true;
        document.getElementById('quiz-answer-p2a').disabled = true;
        document.getElementById('quiz-bonus-p2a').disabled = true;
        checkP2aReady();
    } else {
        let msg = '';
        if (!calcCorrect && !bonusCorrect) {
            msg = '第二阶段积分和奖金判断均错误，请重新计算。';
        } else if (!calcCorrect) {
            msg = '第二阶段积分计算错误，请重新计算（保留两位小数）。';
        } else {
            msg = '奖金判断错误，请重新选择。';
        }
        feedback.textContent = msg;
        feedback.className = 'quiz-feedback quiz-error';
    }
});

document.getElementById('quiz-answer-p2a').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('quiz-submit-btn-p2a').click();
});

document.getElementById('start-p2a-btn').addEventListener('click', () => {
    currentPhase = '2a';
    problems = JSON.parse(sessionStorage.getItem('phase2a_problems') || '[]');
    currentIndex = 0;
    answers = [];
    timerSeconds = 60;
    startProblemRound();
});

// ===== 休息页 2 → 第二阶段（显示基线） =====
document.getElementById('to-p2b-btn').addEventListener('click', () => {
    const q1 = document.querySelector('input[name="p2a_q1"]:checked');
    const q2 = document.querySelector('input[name="p2a_q2"]:checked');
    const errorEl = document.getElementById('p2a-questionnaire-error');

    if (!q1 || !q2) {
        errorEl.textContent = '请回答所有问题后再继续。';
        return;
    }

    errorEl.textContent = '';
    document.getElementById('to-p2b-btn').disabled = true;
    document.getElementById('to-p2b-btn').textContent = '提交中...';

    fetch('/api/p2a_questionnaire_submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q1: parseInt(q1.value), q2: parseInt(q2.value) })
    })
    .then(r => r.json())
    .then(() => {
        sessionStorage.setItem('show_baseline', 'true');
        fetch('/api/save_baseline', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ show_baseline: 'true' })
        });
        showScreen('instructions-p2b');
        initP2bWait();
    })
    .catch(() => {
        errorEl.textContent = '提交失败，请重试。';
        document.getElementById('to-p2b-btn').disabled = false;
        document.getElementById('to-p2b-btn').textContent = '进入下一轮';
    });
});

// ===== 第三轮（显示基线）指导语 =====
function initP2bWait() {
    const btn = document.getElementById('start-p2b-btn');
    btn.disabled = false;
    btn.textContent = '开始做题';

    const groupType = sessionStorage.getItem('group_type');
    const epsilonEl = document.getElementById('epsilon-range-p2b');
    if (groupType === 'high_volatility') {
        epsilonEl.textContent = 'ε 在 [-0.50, 0.50] 区间';
    } else {
        epsilonEl.textContent = 'ε 在 [-0.10, 0.10] 区间';
    }

    // 显示基线：一定能获得高奖金的最低做对题数
    const p1Correct = parseInt(sessionStorage.getItem('phase1_correct')) || 0;
    const epsLower = groupType === 'high_volatility' ? -0.50 : -0.10;
    const guaranteed = Math.floor(p1Correct / (1 + epsLower)) + 1;
    document.getElementById('guaranteed-count').textContent = guaranteed;
    document.getElementById('guaranteed-bonus').style.display = '';
}

document.getElementById('start-p2b-btn').addEventListener('click', () => {
    currentPhase = '2b';
    problems = JSON.parse(sessionStorage.getItem('phase2b_problems') || '[]');
    currentIndex = 0;
    answers = [];
    timerSeconds = 60;
    startProblemRound();
});

// ===== 开始做题（通用） =====
function startProblemRound() {
    showScreen('problem-screen');

    const timerEpsilon = document.getElementById('timer-epsilon');
    const baselineCounter = document.getElementById('baseline-counter');

    if (currentPhase === '2a' || currentPhase === '2b') {
        const groupType = sessionStorage.getItem('group_type');
        if (groupType === 'high_volatility') {
            timerEpsilon.textContent = 'ε 在 [-0.50, 0.50] 区间';
        } else {
            timerEpsilon.textContent = 'ε 在 [-0.10, 0.10] 区间';
        }
        timerEpsilon.style.display = '';
    } else {
        timerEpsilon.style.display = 'none';
    }

    const showBaseline = sessionStorage.getItem('show_baseline') === 'true';
    if (showBaseline) {
        const p1Correct = sessionStorage.getItem('phase1_correct') || '0';
        document.getElementById('p1-correct-count').textContent = p1Correct;
        document.getElementById('p2-correct-count').textContent = '0';
        baselineCounter.style.display = '';
    } else {
        baselineCounter.style.display = 'none';
    }

    renderProblem();
    document.getElementById('answer-input').focus();
    startTimer();
}

// ===== 渲染当前题目 =====
function renderProblem() {
    if (currentIndex >= problems.length) {
        finishRound();
        return;
    }
    const p = problems[currentIndex];
    document.getElementById('num-a').textContent = p.a;
    document.getElementById('problem-op').textContent = p.op;
    document.getElementById('num-b').textContent = p.b;
    document.getElementById('answer-input').value = '';
    document.getElementById('answer-input').focus();
    questionStartTime = Date.now();
}

// ===== 提交当前题目答案 =====
function submitAnswer() {
    if (currentIndex >= problems.length) return;

    const elapsed = (Date.now() - questionStartTime) / 1000;
    const input = document.getElementById('answer-input');
    const userAnswer = parseInt(input.value, 10);
    const p = problems[currentIndex];
    const isCorrect = userAnswer === p.answer;

    answers.push({
        a: p.a,
        b: p.b,
        op: p.op,
        user_answer: isNaN(userAnswer) ? null : userAnswer,
        correct_answer: p.answer,
        elapsed_sec: isCorrect ? parseFloat(elapsed.toFixed(1)) : 0
    });

    if ((currentPhase === '2a' || currentPhase === '2b') && sessionStorage.getItem('show_baseline') === 'true') {
        const correct = answers.filter(a => a.user_answer === a.correct_answer).length;
        document.getElementById('p2-correct-count').textContent = correct;
    }

    currentIndex++;
    renderProblem();
}

// ===== 提交按钮 =====
document.getElementById('submit-btn').addEventListener('click', submitAnswer);

// ===== 回车提交 =====
document.getElementById('answer-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        submitAnswer();
    }
});

// ===== 计时器 =====
function startTimer() {
    updateTimerDisplay();
    timerInterval = setInterval(() => {
        timerSeconds--;
        updateTimerDisplay();

        if (timerSeconds <= 10) {
            document.getElementById('timer').classList.add('warning');
        }

        if (timerSeconds <= 0) {
            clearInterval(timerInterval);
            timerInterval = null;
            finishRound();
        }
    }, 1000);
}

function updateTimerDisplay() {
    const m = Math.floor(timerSeconds / 60);
    const s = timerSeconds % 60;
    document.getElementById('timer').textContent =
        String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

// ===== 结束当前阶段 =====
function finishRound() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }

    while (currentIndex < problems.length) {
        const p = problems[currentIndex];
        answers.push({
            a: p.a,
            b: p.b,
            op: p.op,
            user_answer: null,
            correct_answer: p.answer,
            elapsed_sec: 0
        });
        currentIndex++;
    }

    document.getElementById('timer').classList.remove('warning');

    if (currentPhase === '1') {
        fetch('/api/phase1_submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answers: answers })
        })
        .then(r => r.json())
        .then(data => {
            const p1Correct = answers.filter(a => a.user_answer === a.correct_answer).length;
            sessionStorage.setItem('group_type', data.group_type);
            sessionStorage.setItem('phase1_correct', p1Correct);
            showScreen('break-screen-1');
        });
    } else if (currentPhase === '2a') {
        fetch('/api/phase2a_submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answers: answers })
        })
        .then(r => r.json())
        .then(data => {
            const textEl = document.getElementById('p2a-result-text');
            if (data.comparison === 'high') {
                textEl.textContent = '恭喜！您获得了高奖金！';
                textEl.className = 'result-text result-high';
            } else {
                textEl.textContent = '很遗憾，您获得了低奖金。';
                textEl.className = 'result-text result-low';
            }
            showScreen('break-screen-2');
        });
    } else {
        fetch('/api/phase2_submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answers: answers })
        })
        .then(r => r.json())
        .then(data => {
            const textEl = document.getElementById('p2b-result-text');
            if (data.comparison === 'high') {
                textEl.textContent = '恭喜！您获得了高奖金！';
                textEl.className = 'result-text result-high';
            } else {
                textEl.textContent = '很遗憾，您获得了低奖金。';
                textEl.className = 'result-text result-low';
            }
            showScreen('questionnaire-screen');
        });
    }
}

// ===== 问卷第1页 → 第2页 =====
document.getElementById('questionnaire-next-btn').addEventListener('click', () => {
    const q1 = document.querySelector('input[name="q1"]:checked');
    const q2 = document.querySelector('input[name="q2"]:checked');
    const errorEl = document.getElementById('questionnaire-page1-error');

    if (!q1 || !q2) {
        errorEl.textContent = '请回答所有问题后再继续。';
        return;
    }
    errorEl.textContent = '';
    document.getElementById('questionnaire-page-1').style.display = 'none';
    document.getElementById('questionnaire-page-2').style.display = '';
    document.querySelector('#questionnaire-screen h1').textContent = '问卷（续）';
    document.querySelector('#questionnaire-screen .subtitle').style.display = 'none';
    document.getElementById('p2b-result-text').style.display = 'none';
});

// ===== 返回上一页 =====
document.getElementById('questionnaire-prev-btn').addEventListener('click', () => {
    document.getElementById('questionnaire-page-2').style.display = 'none';
    document.getElementById('questionnaire-page-1').style.display = '';
    document.querySelector('#questionnaire-screen h1').textContent = '第三轮结束';
    document.querySelector('#questionnaire-screen .subtitle').style.display = '';
    document.getElementById('p2b-result-text').style.display = '';
});

// ===== 问卷最终提交 =====
document.getElementById('questionnaire-submit-btn').addEventListener('click', () => {
    const q1 = document.querySelector('input[name="q1"]:checked');
    const q2 = document.querySelector('input[name="q2"]:checked');
    const q3 = document.querySelector('input[name="q3"]:checked');
    const q4 = document.querySelector('input[name="q4"]:checked');
    const q5 = document.querySelector('input[name="q5"]:checked');
    const q6 = document.querySelector('input[name="q6"]:checked');
    const errorEl = document.getElementById('questionnaire-page2-error');

    if (!q1 || !q2 || !q3 || !q4 || !q5 || !q6) {
        errorEl.textContent = '请回答所有问题后再提交。';
        return;
    }

    errorEl.textContent = '';
    document.getElementById('questionnaire-submit-btn').disabled = true;
    document.getElementById('questionnaire-submit-btn').textContent = '提交中...';

    fetch('/api/questionnaire_submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            q1: parseInt(q1.value),
            q2: parseInt(q2.value),
            q3: parseInt(q3.value),
            q4: parseInt(q4.value),
            q5: parseInt(q5.value),
            q6: parseInt(q6.value)
        })
    })
    .then(r => r.json())
    .then(() => {
        showScreen('result-screen');
    })
    .catch(() => {
        errorEl.textContent = '提交失败，请重试。';
        document.getElementById('questionnaire-submit-btn').disabled = false;
        document.getElementById('questionnaire-submit-btn').textContent = '提交问卷';
    });
});'''


# ==================== 静态文件路由 ====================

@app.route('/static/style.css')
def style_css():
    return Response(STYLE_CSS, mimetype='text/css')


@app.route('/static/experiment.js')
def experiment_js():
    return Response(EXPERIMENT_JS, mimetype='application/javascript')


# ==================== 页面路由 ====================

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)


@app.route('/experiment')
def experiment():
    return render_template_string(EXPERIMENT_HTML)


@app.route('/admin')
def admin():
    return render_template_string(ADMIN_HTML)


# ==================== 数据库 ====================

def get_db():
    if 'db' not in g:
        if USING_PG:
            g.db = psycopg2.connect(DATABASE_URL)
            g.db.cursor_factory = psycopg2.extras.RealDictCursor
        else:
            g.db = sqlite3.connect(DATABASE)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def db_exec(sql, params=()):
    """执行写操作，自动适配 SQLite / PostgreSQL"""
    if USING_PG:
        sql = sql.replace('?', '%s')
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    db.commit()


def db_fetch(sql, params=()):
    """执行读操作，返回 dict-like 行列表"""
    if USING_PG:
        sql = sql.replace('?', '%s')
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        conn.close()
        return rows
    else:
        with sqlite3.connect(DATABASE) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, params).fetchall()


def init_db():
    if USING_PG:
        db_exec('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                participant_id TEXT DEFAULT '',
                group_type TEXT DEFAULT '',
                show_baseline TEXT DEFAULT '',
                phase1_total INTEGER DEFAULT 0,
                phase1_correct INTEGER DEFAULT 0,
                phase1_score DOUBLE PRECISION DEFAULT 0.0,
                phase2a_total INTEGER DEFAULT 0,
                phase2a_correct INTEGER DEFAULT 0,
                phase2a_adjusted DOUBLE PRECISION DEFAULT 0.0,
                phase2a_comparison TEXT DEFAULT '',
                phase2_total INTEGER DEFAULT 0,
                phase2_correct INTEGER DEFAULT 0,
                epsilon DOUBLE PRECISION DEFAULT 0.0,
                phase2_adjusted DOUBLE PRECISION DEFAULT 0.0,
                phase2_comparison TEXT DEFAULT '',
                p1_times TEXT DEFAULT '',
                p2a_times TEXT DEFAULT '',
                p2b_times TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        ''')
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN show_baseline TEXT DEFAULT \'\'')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN phase2a_total INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN phase2a_correct INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN phase2a_adjusted DOUBLE PRECISION DEFAULT 0.0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN phase2a_comparison TEXT DEFAULT \'\'')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN p1_times TEXT DEFAULT \'\'')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN p2a_times TEXT DEFAULT \'\'')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN p2b_times TEXT DEFAULT \'\'')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN q1 INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN q2 INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN q3 INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN q4 INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN q5 INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN p2a_q1 INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN p2a_q2 INTEGER DEFAULT 0')
        except:
            pass
        try:
            db_exec('ALTER TABLE sessions ADD COLUMN q6 INTEGER DEFAULT 0')
        except:
            pass
    else:
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
                    phase2a_total INTEGER DEFAULT 0,
                    phase2a_correct INTEGER DEFAULT 0,
                    phase2a_adjusted REAL DEFAULT 0.0,
                    phase2a_comparison TEXT DEFAULT '',
                    phase2_total INTEGER DEFAULT 0,
                    phase2_correct INTEGER DEFAULT 0,
                    epsilon REAL DEFAULT 0.0,
                    phase2_adjusted REAL DEFAULT 0.0,
                    phase2_comparison TEXT DEFAULT '',
                    p1_times TEXT DEFAULT '',
                    p2a_times TEXT DEFAULT '',
                    p2b_times TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                )
            ''')
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN show_baseline TEXT DEFAULT ""')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN phase2a_total INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN phase2a_correct INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN phase2a_adjusted REAL DEFAULT 0.0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN phase2a_comparison TEXT DEFAULT ""')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN p1_times TEXT DEFAULT ""')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN p2a_times TEXT DEFAULT ""')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN p2b_times TEXT DEFAULT ""')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN q1 INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN q2 INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN q3 INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN q4 INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN q5 INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN p2a_q1 INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN p2a_q2 INTEGER DEFAULT 0')
            except:
                pass
            try:
                conn.execute('ALTER TABLE sessions ADD COLUMN q6 INTEGER DEFAULT 0')
            except:
                pass


def generate_problems(n=100, exclude=None):
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


# ==================== API 路由 ====================

@app.route('/api/start', methods=['POST'])
def start_experiment():
    session_id = str(uuid.uuid4())
    participant_id = request.json.get('participant_id', '')

    phase1_problems = generate_problems(100)
    phase2a_problems = generate_problems(100, exclude={(p['a'], p['b']) for p in phase1_problems})
    exclude_all = {(p['a'], p['b']) for p in phase1_problems} | {(p['a'], p['b']) for p in phase2a_problems}
    phase2b_problems = generate_problems(100, exclude=exclude_all)

    db_exec(
        'INSERT INTO sessions (id, participant_id, created_at) VALUES (?, ?, ?)',
        (session_id, participant_id, datetime.now().isoformat())
    )

    session['session_id'] = session_id
    session['phase1_problems'] = phase1_problems
    session['phase2a_problems'] = phase2a_problems
    session['phase2b_problems'] = phase2b_problems

    return jsonify({
        'session_id': session_id,
        'phase1_problems': phase1_problems,
        'phase2a_problems': phase2a_problems,
        'phase2b_problems': phase2b_problems
    })


@app.route('/api/phase1_submit', methods=['POST'])
def phase1_submit():
    answers = request.json.get('answers', [])
    correct = sum(1 for item in answers if item.get('user_answer') == item.get('correct_answer'))
    p1_times = json.dumps([item['elapsed_sec'] for item in answers if item.get('user_answer') == item.get('correct_answer') and item.get('elapsed_sec', 0) > 0])

    db_exec(
        'UPDATE sessions SET phase1_total = ?, phase1_correct = ?, phase1_score = ?, p1_times = ? WHERE id = ?',
        (len(answers), correct, correct * 0.02, p1_times, session.get('session_id'))
    )

    group_type = random.choice(['high_volatility', 'low_volatility'])
    if group_type == 'low_volatility':
        epsilon = round(random.uniform(-0.1, 0.1), 4)
    else:
        epsilon = round(random.uniform(-0.5, 0.5), 4)

    session['group_type'] = group_type
    session['epsilon'] = epsilon
    session['phase1_correct'] = correct

    db_exec(
        'UPDATE sessions SET group_type = ? WHERE id = ?',
        (group_type, session.get('session_id'))
    )

    return jsonify({'correct': correct, 'group_type': group_type})


@app.route('/api/phase2a_submit', methods=['POST'])
def phase2a_submit():
    """第二阶段不显示基线"""
    answers = request.json.get('answers', [])
    correct = sum(1 for item in answers if item.get('user_answer') == item.get('correct_answer'))
    p2a_times = json.dumps([item['elapsed_sec'] for item in answers if item.get('user_answer') == item.get('correct_answer') and item.get('elapsed_sec', 0) > 0])

    epsilon = session.get('epsilon', 0)
    phase1_correct = session.get('phase1_correct', 0)
    phase2a_adjusted = round(correct * (1 + epsilon), 4)
    comparison = 'high' if phase2a_adjusted > phase1_correct else 'low'

    db_exec(
        'UPDATE sessions SET phase2a_total = ?, phase2a_correct = ?, phase2a_adjusted = ?, phase2a_comparison = ?, p2a_times = ? WHERE id = ?',
        (len(answers), correct, phase2a_adjusted, comparison, p2a_times, session.get('session_id'))
    )

    session['phase2a_correct'] = correct
    session['phase2a_comparison'] = comparison

    return jsonify({'comparison': comparison})


@app.route('/api/save_baseline', methods=['POST'])
def save_baseline():
    show_baseline = request.json.get('show_baseline', 'false')
    db_exec(
        'UPDATE sessions SET show_baseline = ? WHERE id = ?',
        (show_baseline, session.get('session_id'))
    )
    return jsonify({'ok': True})


@app.route('/api/phase2_submit', methods=['POST'])
def phase2_submit():
    answers = request.json.get('answers', [])
    correct = sum(1 for item in answers if item.get('user_answer') == item.get('correct_answer'))
    p2b_times = json.dumps([item['elapsed_sec'] for item in answers if item.get('user_answer') == item.get('correct_answer') and item.get('elapsed_sec', 0) > 0])

    epsilon = session.get('epsilon', 0)
    phase1_correct = session.get('phase1_correct', 0)
    phase2_adjusted = round(correct * (1 + epsilon), 4)
    comparison = 'high' if phase2_adjusted > phase1_correct else 'low'

    db_exec(
        'UPDATE sessions SET phase2_total = ?, phase2_correct = ?, epsilon = ?, phase2_adjusted = ?, phase2_comparison = ?, p2b_times = ? WHERE id = ?',
        (len(answers), correct, epsilon, phase2_adjusted, comparison, p2b_times, session.get('session_id'))
    )

    return jsonify({'comparison': comparison})


@app.route('/api/p2a_questionnaire_submit', methods=['POST'])
def p2a_questionnaire_submit():
    q1 = request.json.get('q1', 0)
    q2 = request.json.get('q2', 0)

    db_exec(
        'UPDATE sessions SET p2a_q1 = ?, p2a_q2 = ? WHERE id = ?',
        (q1, q2, session.get('session_id'))
    )

    return jsonify({'ok': True})


@app.route('/api/questionnaire_submit', methods=['POST'])
def questionnaire_submit():
    q1 = request.json.get('q1', 0)
    q2 = request.json.get('q2', 0)
    q3 = request.json.get('q3', 0)
    q4 = request.json.get('q4', 0)
    q5 = request.json.get('q5', 0)
    q6 = request.json.get('q6', 0)

    db_exec(
        'UPDATE sessions SET q1 = ?, q2 = ?, q3 = ?, q4 = ?, q5 = ?, q6 = ? WHERE id = ?',
        (q1, q2, q3, q4, q5, q6, session.get('session_id'))
    )

    return jsonify({'ok': True})

@app.route('/api/admin/sessions')
def admin_sessions():
    rows = db_fetch('SELECT * FROM sessions ORDER BY created_at DESC')
    return jsonify([dict(r) for r in rows])


@app.route('/api/admin/stats')
def admin_stats():
    rows = db_fetch('SELECT * FROM sessions')

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


# ==================== 启动 ====================

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
