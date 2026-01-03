import os
import datetime
import pytest

# 日志文件路径
LOG_FILE = os.path.join(os.path.dirname(__file__), "pytest_realtime_log.txt")

def log_to_file(msg):
    """写入日志并强制刷新到磁盘"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            f.write(f"[{timestamp}] {msg}\n")
            f.flush()
            os.fsync(f.fileno()) # 关键：强制写入硬盘
    except Exception as e:
        print(f"Failed to write to log file: {e}")

def pytest_sessionstart(session):
    """测试会话开始时清空日志"""
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"Test Session Started at {datetime.datetime.now()}\n")

def pytest_runtest_setup(item):
    """每个测试开始前执行"""
    log_to_file(f"STARTING: {item.nodeid}")

def pytest_runtest_logreport(report):
    """报告测试结果"""
    if report.when == "call":
        outcome = "PASSED" if report.outcome == "passed" else "FAILED"
        log_to_file(f"FINISHED: {report.nodeid} - {outcome}")

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """测试结束后记录最终统计结果"""
    passed = len(terminalreporter.stats.get('passed', []))
    failed = len(terminalreporter.stats.get('failed', []))
    skipped = len(terminalreporter.stats.get('skipped', []))
    error = len(terminalreporter.stats.get('error', []))
    
    log_to_file("\n" + "=" * 30)
    log_to_file("TEST SESSION SUMMARY")
    log_to_file("=" * 30)
    log_to_file(f"PASSED:  {passed}")
    log_to_file(f"FAILED:  {failed}")
    log_to_file(f"SKIPPED: {skipped}")
    log_to_file(f"ERRORS:  {error}")
    log_to_file(f"Exit Status: {exitstatus}")
    log_to_file("=" * 30)
