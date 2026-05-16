// ===== 全局状态 =====
let currentPhase = 1;               // 1 或 2
let problems = [];                  // 当前阶段题目数组
let currentIndex = 0;               // 当前题目索引
let answers = [];                   // 已提交的答案 [{a, b, op, user_answer, correct_answer}, ...]
let timerSeconds = 60;              // 倒计时秒数
let timerInterval = null;           // 计时器句柄
let quizPassed = false;             // P2 测验是否通过
let p2QuizAnswer = 0;              // P2 测验正确答案

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
        currentPhase = 1;
        problems = JSON.parse(sessionStorage.getItem('phase1_problems') || '[]');
        currentIndex = 0;
        answers = [];
        timerSeconds = 60;
        startProblemRound();
    });
})();

// ===== 第二阶段指导语：测验 + 强制停留 20 秒 =====
let p2WaitTimer = null;
let p2CanStart = false;

function generateP2Quiz() {
    // 随机生成测验题目
    const p1Score = Math.floor(Math.random() * 21) + 20; // 20~40
    const groupType = sessionStorage.getItem('group_type');
    const epsRange = groupType === 'high_volatility' ? 0.30 : 0.10;
    const eps = (Math.random() * epsRange * 2 - epsRange).toFixed(2);
    const p2Correct = Math.floor(Math.random() * 21) + 15; // 15~35
    p2QuizAnswer = parseFloat((p2Correct * (1 + parseFloat(eps))).toFixed(2));
    window._p2QuizP1Score = p1Score;

    document.getElementById('quiz-question').innerHTML =
        '假设 第一阶段答对题数 = <strong>' + p1Score + '</strong>（第一阶段积分 = ' + p1Score + '），' +
        'ε = <strong>' + eps + '</strong>，第二阶段答对题数 = <strong>' + p2Correct + '</strong>。' +
        '请计算 第二阶段积分（<strong>保留两位小数</strong>），并判断获得高奖金还是低奖金。';
    document.getElementById('quiz-feedback').textContent = '';
    document.getElementById('quiz-feedback').className = 'quiz-feedback';
    document.getElementById('quiz-answer').value = '';
    document.getElementById('quiz-bonus').value = '';
}

function initP2Wait() {
    const btn = document.getElementById('start-p2-btn');
    const countdownEl = document.getElementById('p2-countdown');
    let remaining = 20;
    countdownEl.textContent = remaining;
    p2CanStart = false;
    quizPassed = false;
    btn.disabled = true;
    btn.textContent = '请先完成测验并等待倒计时';
    document.querySelector('#instructions-p2 .wait-hint').style.display = '';

    // 显示 ε 范围
    const groupType = sessionStorage.getItem('group_type');
    const epsilonEl = document.getElementById('epsilon-range');
    if (groupType === 'high_volatility') {
        epsilonEl.textContent = 'ε 在 [-0.30, 0.30] 区间';
    } else {
        epsilonEl.textContent = 'ε 在 [-0.10, 0.10] 区间';
    }

    // 显示基线信息：一定能获得高奖金的最低做对题数
    const showBaseline = sessionStorage.getItem('show_baseline') === 'true';
    const guaranteedEl = document.getElementById('guaranteed-bonus');
    if (showBaseline) {
        const p1Correct = parseInt(sessionStorage.getItem('phase1_correct')) || 0;
        const epsLower = groupType === 'high_volatility' ? -0.30 : -0.10;
        const guaranteed = Math.floor(p1Correct / (1 + epsLower)) + 1;
        document.getElementById('guaranteed-count').textContent = guaranteed;
        guaranteedEl.style.display = '';
    } else {
        guaranteedEl.style.display = 'none';
    }

    generateP2Quiz();

    p2WaitTimer = setInterval(() => {
        remaining--;
        countdownEl.textContent = remaining;
        if (remaining <= 0) {
            clearInterval(p2WaitTimer);
            p2WaitTimer = null;
            p2CanStart = true;
            document.querySelector('#instructions-p2 .wait-hint').style.display = 'none';
            checkP2Ready();
        }
    }, 1000);
}

function checkP2Ready() {
    const btn = document.getElementById('start-p2-btn');
    if (p2CanStart && quizPassed) {
        btn.disabled = false;
        btn.textContent = '开始做题';
    }
}

// 测验提交
document.getElementById('quiz-submit-btn').addEventListener('click', () => {
    const userAnswer = parseFloat(document.getElementById('quiz-answer').value);
    const bonusChoice = document.getElementById('quiz-bonus').value;
    const feedback = document.getElementById('quiz-feedback');

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
    const expectedBonus = p2QuizAnswer > window._p2QuizP1Score ? 'high' : 'low';
    const bonusCorrect = bonusChoice === expectedBonus;

    if (calcCorrect && bonusCorrect) {
        feedback.textContent = '正确！第二阶段积分 = ' + p2QuizAnswer.toFixed(2) + '，获得' + (expectedBonus === 'high' ? '高奖金' : '低奖金') + '。';
        feedback.className = 'quiz-feedback quiz-success';
        quizPassed = true;
        document.getElementById('quiz-submit-btn').disabled = true;
        document.getElementById('quiz-answer').disabled = true;
        document.getElementById('quiz-bonus').disabled = true;
        checkP2Ready();
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

document.getElementById('quiz-answer').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        document.getElementById('quiz-submit-btn').click();
    }
});

document.getElementById('start-p2-btn').addEventListener('click', () => {
    currentPhase = 2;
    problems = JSON.parse(sessionStorage.getItem('phase2_problems') || '[]');
    currentIndex = 0;
    answers = [];
    timerSeconds = 60;
    startProblemRound();
});

// ===== 休息页 → 第二阶段指导语 =====
document.getElementById('to-p2-btn').addEventListener('click', () => {
    const baselineRadio = document.querySelector('input[name="baseline"]:checked');
    const showBaseline = baselineRadio ? baselineRadio.value === 'show' : false;
    sessionStorage.setItem('show_baseline', showBaseline ? 'true' : 'false');

    // 发送基线选择到服务器
    fetch('/api/save_baseline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ show_baseline: showBaseline ? 'true' : 'false' })
    });

    showScreen('instructions-p2');
    initP2Wait();
});

// ===== 开始做题（通用） =====
function startProblemRound() {
    showScreen('problem-screen');

    // 配置计时器栏
    const timerEpsilon = document.getElementById('timer-epsilon');
    const baselineCounter = document.getElementById('baseline-counter');
    const showBaseline = sessionStorage.getItem('show_baseline') === 'true';

    if (currentPhase === 2) {
        const groupType = sessionStorage.getItem('group_type');
        if (groupType === 'high_volatility') {
            timerEpsilon.textContent = 'ε 在 [-0.30, 0.30] 区间';
        } else {
            timerEpsilon.textContent = 'ε 在 [-0.10, 0.10] 区间';
        }
        timerEpsilon.style.display = '';
        if (showBaseline) {
            const p1Correct = sessionStorage.getItem('phase1_correct') || '0';
            document.getElementById('p1-correct-count').textContent = p1Correct;
            document.getElementById('p2-correct-count').textContent = '0';
            baselineCounter.style.display = '';
        } else {
            baselineCounter.style.display = 'none';
        }
    } else {
        timerEpsilon.style.display = 'none';
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
}

// ===== 提交当前题目答案 =====
function submitAnswer() {
    if (currentIndex >= problems.length) return;

    const input = document.getElementById('answer-input');
    const userAnswer = parseInt(input.value, 10);
    const p = problems[currentIndex];

    answers.push({
        a: p.a,
        b: p.b,
        op: p.op,
        user_answer: isNaN(userAnswer) ? null : userAnswer,
        correct_answer: p.answer
    });

    // 更新基线计数器
    if (currentPhase === 2 && sessionStorage.getItem('show_baseline') === 'true') {
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

    // 未做的题都算不对
    while (currentIndex < problems.length) {
        const p = problems[currentIndex];
        answers.push({
            a: p.a,
            b: p.b,
            op: p.op,
            user_answer: null,
            correct_answer: p.answer
        });
        currentIndex++;
    }

    document.getElementById('timer').classList.remove('warning');

    if (currentPhase === 1) {
        // 提交第一阶段结果
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
            showScreen('break-screen');
        });
    } else {
        // 提交第二阶段结果
        fetch('/api/phase2_submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answers: answers })
        })
        .then(r => r.json())
        .then(data => {
            const textEl = document.getElementById('result-text');
            if (data.comparison === 'high') {
                textEl.textContent = '恭喜！您获得了高奖金！';
                textEl.className = 'result-text result-high';
            } else {
                textEl.textContent = '很遗憾，您获得了低奖金。';
                textEl.className = 'result-text result-low';
            }
            showScreen('result-screen');
        });
    }
}
